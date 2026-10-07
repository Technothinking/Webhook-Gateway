import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone, timedelta
from uuid import UUID

import httpx
import redis.asyncio as redis
from redis.exceptions import TimeoutError
from sqlalchemy import select, func

from app.db.session import SessionLocal
from app.models.delivery_attempt import DeliveryAttempt
from app.models.event import Event
from app.models.subscriber import Subscriber
from app.models.subscription import Subscription
from app.models.dead_letter import DeadLetter


from dotenv import load_dotenv

load_dotenv()


REDIS_URL = os.getenv("REDIS_URL")

EVENT_STREAM = "webhook_events"
CONSUMER_GROUP = "delivery_workers"
CONSUMER_NAME = os.getenv("WORKER_NAME", "worker-1")

redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True
)


# Retry configuration
RETRY_DELAYS = [
    1,      # Attempt 1 -> retry after 1 second
    5,      # Attempt 2 -> retry after 5 seconds
    30,     # Attempt 3 -> retry after 30 seconds
    300,    # Attempt 4 -> retry after 5 minutes
    1800    # Attempt 5 -> retry after 30 minutes
]
# RETRY_DELAYS = [1, 2, 3, 4, 5]

JITTER_MAX_SECONDS = 1
MAX_RETRY_ATTEMPTS = len(RETRY_DELAYS)

# Circuit breaker configuration
CIRCUIT_FAILURE_THRESHOLD = 5

CIRCUIT_HEALTH_CHECK_INTERVAL = 60

async def create_consumer_group():
    try:
        await redis_client.xgroup_create(
            name=EVENT_STREAM,
            groupname=CONSUMER_GROUP,
            id="$",
            mkstream=True
        )
        print(f"Created consumer group: {CONSUMER_GROUP}")

    except redis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            print(f"Consumer group already exists: {CONSUMER_GROUP}")
        else:
            raise


def generate_signature(
    secret: str,
    timestamp: str,
    payload_json: str
) -> str:

    signed_payload = f"{timestamp}.{payload_json}"

    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return signature


def calculate_retry_delay(attempt_number: int) -> int:
    """
    Calculate retry delay using the configured exponential-style
    retry schedule plus a small random jitter.
    """

    import random

    index = attempt_number - 1

    if index >= len(RETRY_DELAYS):
        index = len(RETRY_DELAYS) - 1

    base_delay = RETRY_DELAYS[index]

    jitter = random.uniform(
        0,
        JITTER_MAX_SECONDS
    )

    return int(base_delay + jitter)


async def get_next_attempt_number(
    db,
    event_id,
    subscriber_id
) -> int:

    stmt = select(
        func.max(DeliveryAttempt.attempt_number)
    ).where(
        DeliveryAttempt.event_id == event_id,
        DeliveryAttempt.subscriber_id == subscriber_id
    )

    result = db.execute(stmt)

    last_attempt = result.scalar()

    if last_attempt is None:
        return 1

    return last_attempt + 1


def move_to_dead_letter(
    db,
    event_id,
    subscriber_id,
    failed_reason,
):
    existing_dead_letter = db.scalar(
        select(DeadLetter).where(
            DeadLetter.event_id == event_id,
            DeadLetter.subscriber_id == subscriber_id,
        )
    )

    if existing_dead_letter is not None:
        return existing_dead_letter

    dead_letter = DeadLetter(
        event_id=event_id,
        subscriber_id=subscriber_id,
        failed_reason=failed_reason,
    )

    db.add(dead_letter)
    db.commit()
    db.refresh(dead_letter)

    print(
        f"Moved event {event_id} for subscriber "
        f"{subscriber_id} to dead letter queue: {failed_reason}"
    )

    return dead_letter


async def check_circuit_breaker(
    db,
    subscriber: Subscriber
):
    """
    Check the subscriber's most recent delivery attempts.

    If the latest CIRCUIT_FAILURE_THRESHOLD attempts
    are all failures, mark the subscriber as degraded.
    """

    stmt = (
        select(DeliveryAttempt.status)
        .where(
            DeliveryAttempt.subscriber_id == subscriber.id
        )
        .order_by(
            DeliveryAttempt.attempted_at.desc()
        )
        .limit(CIRCUIT_FAILURE_THRESHOLD)
    )

    result = db.execute(stmt)

    recent_statuses = result.scalars().all()

    if len(recent_statuses) < CIRCUIT_FAILURE_THRESHOLD:
        return

    failure_statuses = {
        "pending_retry",
        "retry_queued",
        "failed"
    }

    consecutive_failures = all(
        status in failure_statuses
        for status in recent_statuses
    )

    if consecutive_failures and subscriber.status == "active":

        subscriber.status = "degraded"

        db.commit()

        print(
            f"Circuit breaker OPENED for subscriber "
            f"{subscriber.id}: "
            f"{CIRCUIT_FAILURE_THRESHOLD} consecutive failures"
        )


async def health_check_subscriber(
    db,
    subscriber: Subscriber
):
    """
    Send a lightweight health-check request to a degraded subscriber.

    A successful response closes the circuit and restores the
    subscriber to active status.
    """

    timestamp = str(
        int(datetime.now(timezone.utc).timestamp())
    )

    health_payload = json.dumps(
        {
            "type": "webhook_health_check",
            "message": "Webhook Reliability Gateway health check"
        },
        separators=(",", ":"),
        sort_keys=True
    )

    signature = generate_signature(
        subscriber.secret,
        timestamp,
        health_payload
    )

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": timestamp,
        "X-Webhook-Health-Check": "true"
    }

    try:
        async with httpx.AsyncClient() as client:

            response = await client.post(
                subscriber.endpoint_url,
                content=health_payload,
                headers=headers,
                timeout=10.0
            )

        if 200 <= response.status_code < 300:

            subscriber.status = "active"

            db.commit()

            print(
                f"Circuit breaker CLOSED for subscriber "
                f"{subscriber.id}: "
                f"health check succeeded "
                f"(status={response.status_code})"
            )

            return True

        print(
            f"Health check failed for subscriber "
            f"{subscriber.id}: "
            f"status={response.status_code}"
        )

        return False

    except httpx.RequestError as exc:

        print(
            f"Health check failed for subscriber "
            f"{subscriber.id}: {exc}"
        )

        return False


async def run_health_checks():
    """
    Periodically probe all degraded subscribers.
    """

    while True:

        db = SessionLocal()

        try:

            subscribers = db.execute(
                select(Subscriber).where(
                    Subscriber.status == "degraded"
                )
            ).scalars().all()

            for subscriber in subscribers:

                await health_check_subscriber(
                    db,
                    subscriber
                )

        except Exception as exc:

            print(
                f"Health check scheduler error: {exc}"
            )

        finally:

            db.close()

        await asyncio.sleep(
            CIRCUIT_HEALTH_CHECK_INTERVAL
        )


async def deliver_to_subscriber(
    db,
    event,
    subscriber,
    payload_json,
    attempt_number
):

    is_replay = attempt_number == 0
    
    timestamp = str(
        int(datetime.now(timezone.utc).timestamp())
    )

    signature = generate_signature(
        subscriber.secret,
        timestamp,
        payload_json
    )

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": signature,
        "X-Webhook-Timestamp": timestamp
    }

    start_time = time.perf_counter()

    try:
        async with httpx.AsyncClient() as client:

            response = await client.post(
                subscriber.endpoint_url,
                content=payload_json,
                headers=headers,
                timeout=10.0
            )

        latency_ms = int(
            (time.perf_counter() - start_time) * 1000
        )

        if 200 <= response.status_code < 300:

            delivery_attempt = DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=attempt_number,
                status="success",
                http_status_code=response.status_code,
                response_body=response.text,
                latency_ms=latency_ms,
                attempted_at=datetime.now(timezone.utc),
                next_retry_at=None
            )

            db.add(delivery_attempt)
            db.commit()

            print(
                f"Delivered event {event.id} to "
                f"{subscriber.endpoint_url} "
                f"attempt={attempt_number} "
                f"status={response.status_code} "
                f"latency={latency_ms}ms"
            )

            return


        if is_replay:
            attempt = DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=attempt_number,
                status="failed",
                http_status_code=response.status_code,
                response_body=response.text,
                latency_ms=latency_ms,
                next_retry_at=None,
            )

            db.add(attempt)
            db.commit()

            print(
                f"Replay failed for event {event.id} "
                f"and subscriber {subscriber.id}"
            )

            return

        # Non-2xx response
        if attempt_number >= MAX_RETRY_ATTEMPTS:
            attempt = DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=attempt_number,
                status="failed",
                http_status_code=response.status_code,
                response_body=response.text,
                latency_ms=latency_ms,
                next_retry_at=None,
            )

            db.add(attempt)
            db.commit()

            move_to_dead_letter(
                db=db,
                event_id=event.id,
                subscriber_id=subscriber.id,
                failed_reason=(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                ),
            )

            await check_circuit_breaker(
                db,
                subscriber,
            )

            print(
                f"Retry attempts exhausted for event {event.id} "
                f"and subscriber {subscriber.id}"
            )

            return

        else:
            retry_delay = calculate_retry_delay(attempt_number)
            next_retry_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=retry_delay)
            )

            delivery_attempt = DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=attempt_number,
                status="pending_retry",
                http_status_code=response.status_code,
                response_body=response.text,
                latency_ms=latency_ms,
                attempted_at=datetime.now(timezone.utc),
                next_retry_at=next_retry_at
            )

            db.add(delivery_attempt)
            db.commit()

            await check_circuit_breaker(
                db,
                subscriber
            )

            print(
                f"Delivery failed for event {event.id} "
                f"to {subscriber.endpoint_url} "
                f"attempt={attempt_number} "
                f"status={response.status_code} "
                f"retry_in={retry_delay}s "
                f"next_retry_at={next_retry_at}"
            )

    except httpx.RequestError as exc:

        if is_replay:
            attempt = DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=attempt_number,
                status="failed",
                response_body=str(exc),
                next_retry_at=None,
            )

            db.add(attempt)
            db.commit()

            print(
                f"Replay failed for event {event.id} "
                f"and subscriber {subscriber.id}: {exc}"
            )

            return


        if attempt_number >= MAX_RETRY_ATTEMPTS:
            attempt = DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=attempt_number,
                status="failed",
                response_body=str(exc),
                next_retry_at=None,
            )

            db.add(attempt)
            db.commit()

            move_to_dead_letter(
                db=db,
                event_id=event.id,
                subscriber_id=subscriber.id,
                failed_reason=f"Request error: {str(exc)}",
            )

            await check_circuit_breaker(
                db,
                subscriber,
            )

            print(
                f"Retry attempts exhausted for event {event.id} "
                f"and subscriber {subscriber.id}: {exc}"
            )

            return

        else:
            retry_delay = calculate_retry_delay(attempt_number)
            next_retry_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=retry_delay)
            )

            delivery_attempt = DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=attempt_number,
                status="pending_retry",
                http_status_code=None,
                response_body=str(exc),
                latency_ms=latency_ms,
                attempted_at=datetime.now(timezone.utc),
                next_retry_at=next_retry_at
            )

            db.add(delivery_attempt)
            db.commit()

            await check_circuit_breaker(
                db,
                subscriber
            )

            print(
                f"Delivery failed for event {event.id} "
                f"to {subscriber.endpoint_url} "
                f"attempt={attempt_number} "
                f"retry_in={retry_delay}s "
                f"next_retry_at={next_retry_at}"
            )

async def deliver_event(
    event_id: str,
    retry_subscriber_id: str | None = None,
    replay = False,
):

    db = SessionLocal()

    try:

        event = db.execute(
            select(Event).where(
                Event.id == event_id
            )
        ).scalar_one_or_none()

        if event is None:
            print(
                f"Event not found: {event_id}"
            )
            return

        if retry_subscriber_id:

            subscriber = db.execute(
                select(Subscriber).where(
                    Subscriber.id == retry_subscriber_id
                )
            ).scalar_one_or_none()

            if subscriber is None:
                print(
                    f"Subscriber not found: "
                    f"{retry_subscriber_id}"
                )
                return

            if subscriber.status == "degraded":
                print(
                    f"Skipping retry for degraded subscriber: "
                    f"{subscriber.id}"
                )
                return

            payload_json = json.dumps(
                event.payload,
                separators=(",", ":"),
                sort_keys=True
            )

            if replay:
                attempt_number = 0
            else:
                attempt_number = await get_next_attempt_number(
                    db,
                    event_id,
                    retry_subscriber_id,
                )

            await deliver_to_subscriber(
                db,
                event,
                subscriber,
                payload_json,
                attempt_number,
            )

            return

        # Initial delivery: fan out to all matching subscribers
        subscriptions = db.execute(
            select(
                Subscription,
                Subscriber
            )
            .join(
                Subscriber,
                Subscription.subscriber_id == Subscriber.id
            )
            .where(
                Subscription.event_type == event.event_type,
                Subscription.is_active.is_(True),
                Subscriber.status == "active"
            )
        ).all()

        if not subscriptions:

            print(
                f"No active subscribers found for "
                f"event {event_id} "
                f"({event.event_type})"
            )

            return

        payload_json = json.dumps(
            event.payload,
            separators=(",", ":"),
            sort_keys=True
        )

        for subscription, subscriber in subscriptions:

            attempt_number = await get_next_attempt_number(
                db,
                event.id,
                subscriber.id
            )

            await deliver_to_subscriber(
                db,
                event,
                subscriber,
                payload_json,
                attempt_number
            )

    finally:
        db.close()


async def consume_events():

    await create_consumer_group()

    print(
        f"Worker '{CONSUMER_NAME}' listening on "
        f"stream '{EVENT_STREAM}'..."
    )

    while True:

        try:

            messages = await redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={
                    EVENT_STREAM: ">"
                },
                count=1,
                block=5000
            )

            if not messages:
                continue

            for stream_name, entries in messages:

                for message_id, data in entries:

                    event_id = data.get("event_id")
                    subscriber_id = data.get(
                        "subscriber_id"
                    )
                    replay = data.get("replay") == "true"

                    print(
                        f"Received event: {event_id} "
                        f"(message_id={message_id})"
                    )

                    if subscriber_id:
                        if replay:
                            print(
                                f"Processing replay for event {event_id} "
                                f"and subscriber {subscriber_id}"
                            )
                        else:
                            print(
                                f"Processing retry for event {event_id} "
                                f"and subscriber {subscriber_id}"
                            )
                    else:
                        print(f"Processing new event {event_id}")

                    await deliver_event(
                        event_id,
                        subscriber_id,
                        replay=replay,
                    )

                    await redis_client.xack(
                        EVENT_STREAM,
                        CONSUMER_GROUP,
                        message_id
                    )

                    print(
                        f"ACKed message: {message_id}"
                    )

        except TimeoutError:

            continue


async def main():

    await asyncio.gather(
        consume_events(),
        run_health_checks()
    )


if __name__ == "__main__":
    asyncio.run(main())