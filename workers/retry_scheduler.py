import asyncio
import os
from datetime import datetime, timezone

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
                DeliveryAttempt.status == "pending_retry",
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

            attempt.status = "retry_queued"

            db.commit()

            print(
                f"Scheduled retry: "
                f"event={attempt.event_id} "
                f"subscriber={attempt.subscriber_id} "
                f"attempt={attempt.attempt_number + 1}"
            )

    finally:

        db.close()


async def run_scheduler():

    print(
        "Retry scheduler started..."
    )

    while True:

        try:

            await schedule_due_retries()

        except Exception as exc:

            print(
                f"Retry scheduler error: {exc}"
            )

        await asyncio.sleep(
            POLL_INTERVAL_SECONDS
        )


async def main():

    await run_scheduler()


if __name__ == "__main__":
    asyncio.run(main())