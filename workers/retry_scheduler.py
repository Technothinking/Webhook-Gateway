import asyncio
import os
from datetime import datetime, timezone

import time
from datetime import timedelta
from sqlalchemy import exists
from app.models.event import Event

import redis.asyncio as redis
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.delivery_attempt import DeliveryAttempt
from app.models.subscriber import Subscriber

from dotenv import load_dotenv

load_dotenv()


REDIS_URL = os.getenv("REDIS_URL")

EVENT_STREAM = "webhook_events"

POLL_INTERVAL_SECONDS = 1

# 'pending_retry' : a failed attempt waiting for its backoff to elapse
# 'deferred'      : an event parked while the subscriber was degraded
SCHEDULABLE_STATUSES = ("pending_retry", "deferred")

# An event still 'pending' with no attempt rows after this long was most
# likely never published (API crashed or Redis failed after the commit).
RECONCILE_AFTER_SECONDS = int(os.getenv("RECONCILE_AFTER_SECONDS", "60"))
RECONCILE_INTERVAL_SECONDS = 10
RECONCILE_GUARD_TTL_SECONDS = 600

redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True
)


async def schedule_due_retries():

    db = SessionLocal()

    try:

        now = datetime.now(timezone.utc)

        stmt = (
            select(DeliveryAttempt)
            .join(
                Subscriber,
                DeliveryAttempt.subscriber_id == Subscriber.id
            )
            .where(
                DeliveryAttempt.status.in_(SCHEDULABLE_STATUSES),
                DeliveryAttempt.next_retry_at.is_not(None),
                DeliveryAttempt.next_retry_at <= now,
                Subscriber.status == "active"
            )
            .order_by(
                DeliveryAttempt.next_retry_at
            )
            .limit(100)
        )

        attempts = db.execute(stmt).scalars().all()

        for attempt in attempts:

            await redis_client.xadd(
                EVENT_STREAM,
                {
                    "event_id": str(attempt.event_id),
                    "subscriber_id": str(
                        attempt.subscriber_id
                    )
                }
            )

            was_deferred = attempt.status == "deferred"

            attempt.status = "retry_queued"

            db.commit()

            next_attempt = (
                attempt.attempt_number + 1
                if attempt.attempt_number > 0
                else 1
            )

            print(
                f"Scheduled {'deferred delivery' if was_deferred else 'retry'}: "
                f"event={attempt.event_id} "
                f"subscriber={attempt.subscriber_id} "
                f"attempt={next_attempt}"
            )

    finally:

        db.close()


async def run_scheduler():

    print("Retry scheduler started...")

    last_reconcile = 0.0

    while True:

        try:

            await schedule_due_retries()

            if time.monotonic() - last_reconcile >= RECONCILE_INTERVAL_SECONDS:
                last_reconcile = time.monotonic()
                await reconcile_unpublished_events()

        except Exception as exc:

            print(f"Retry scheduler error: {exc}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

async def reconcile_unpublished_events():
    db = SessionLocal()

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=RECONCILE_AFTER_SECONDS
        )

        event_ids = db.scalars(
            select(Event.id)
            .where(
                Event.status == "pending",
                Event.published_at.is_(None),
                Event.created_at <= cutoff,
                ~exists().where(DeliveryAttempt.event_id == Event.id),
            )
            .order_by(Event.created_at)
            .limit(100)
        ).all()

        for event_id in event_ids:
            # Republish each event at most once per guard window
            claimed = await redis_client.set(
                f"reconciled:{event_id}",
                "1",
                nx=True,
                ex=RECONCILE_GUARD_TTL_SECONDS,
            )

            if not claimed:
                continue

            await redis_client.xadd(
                EVENT_STREAM,
                {"event_id": str(event_id)}
            )

            print(f"Reconciled unpublished event: {event_id}")

    finally:
        db.close()

async def main():

    await run_scheduler()


if __name__ == "__main__":
    asyncio.run(main())