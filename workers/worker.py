import asyncio
import hashlib
import hmac
import json
import os
import random
import time
from datetime import datetime, timezone, timedelta

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


def _int_list_env(name: str, default: list[int]) -> list[int]:
    raw = os.getenv(name)

    if not raw:
        return default

    try:
        values = [int(p) for p in raw.split(",") if p.strip()]
    except ValueError:
        print(f"Ignoring invalid {name}={raw!r}")
        return default

    return values or default


# Retry configuration
# Attempt 1 -> 1s, 2 -> 5s, 3 -> 30s, 4 -> 5m, 5 -> 30m.
# Can be overridden (e.g. for demos) with RETRY_DELAYS="1,2,3,4,5".
RETRY_DELAYS = _int_list_env("RETRY_DELAYS", [1, 5, 30, 300, 1800])

JITTER_MAX_SECONDS = 1
MAX_RETRY_ATTEMPTS = len(RETRY_DELAYS)

# Circuit breaker configuration
CIRCUIT_FAILURE_THRESHOLD = 5

# Can be overridden (e.g. for demos) with CIRCUIT_HEALTH_CHECK_INTERVAL=10.
CIRCUIT_HEALTH_CHECK_INTERVAL = int(
    os.getenv("CIRCUIT_HEALTH_CHECK_INTERVAL", "60")
)

# attempt_number conventions (the unique constraint is
# event_id + subscriber_id + attempt_number):
#   1..N  real delivery attempts (count toward the retry budget)
#   0     first manual replay, -2, -3, ... later replays
#  -1     "deferred" placeholder: the subscriber was degraded/paused when the
#        event arrived, so delivery is parked until it recovers. It does not
#        consume the retry budget and is ignored by the circuit breaker.
DEFERRED_ATTEMPT_NUMBER = -1

# Stream reliability
RECLAIM_MIN_IDLE_MS = 120_000
RECLAIM_INTERVAL_SECONDS = 30
MAX_DELIVERIES_PER_MESSAGE = 5


async def create_consumer_group():
    try:
        # "0" so entries published before the group existed are not lost.
        # Re-processing is safe: fan-out skips subscribers that already
        # have an attempt for the event.
        await redis_client.xgroup_create(
            name=EVENT_STREAM,
            groupname=CONSUMER_GROUP,
            id="0",
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
    """Next real delivery attempt number (replays/deferred rows ignored)."""

    stmt = select(
        func.max(DeliveryAttempt.attempt_number)
    ).where(
        DeliveryAttempt.event_id == event_id,
        DeliveryAttempt.subscriber_id == subscriber_id,
        DeliveryAttempt.attempt_number > 0
    )

    result = db.execute(stmt)

    last_attempt = result.scalar()

    if last_attempt is None:
        return 1

    return last_attempt + 1


def get_next_replay_number(db, event_id, subscriber_id) -> int:
    """
    Replays use 0, then -2, -3, ... so repeated replays of the same
    dead letter never collide with the unique constraint (-1 is reserved
    for the deferred placeholder).
    """

    lowest = db.execute(
        select(func.min(DeliveryAttempt.attempt_number)).where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscriber_id == subscriber_id,
            DeliveryAttempt.attempt_number <= 0,
            DeliveryAttempt.attempt_number != DEFERRED_ATTEMPT_NUMBER
        )
    ).scalar()

    if lowest is None:
        return 0

    candidate = lowest - 1

    if candidate == DEFERRED_ATTEMPT_NUMBER:
        candidate -= 1

    return candidate


def has_attempts(db, event_id, subscriber_id) -> bool:
    count = db.execute(
        select(func.count(DeliveryAttempt.id)).where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscriber_id == subscriber_id
        )
    ).scalar()

    return bool(count)


def has_success(db, event_id, subscriber_id) -> bool:
    count = db.execute(
        select(func.count(DeliveryAttempt.id)).where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscriber_id == subscriber_id,
            DeliveryAttempt.status == "success"
        )
    ).scalar()

    return bool(count)


def defer_delivery(db, event, subscriber, reason: str):
    """
    Park a delivery for a subscriber that cannot receive it right now
    (degraded circuit). The retry scheduler picks 'deferred' rows up as
    soon as the subscriber is active again, so nothing is lost.
    """

    now = datetime.now(timezone.utc)

    row = db.scalar(
        select(DeliveryAttempt).where(
            DeliveryAttempt.event_id == event.id,
            DeliveryAttempt.subscriber_id == subscriber.id,
            DeliveryAttempt.attempt_number == DEFERRED_ATTEMPT_NUMBER
        )
    )

    if row is None:
        row = DeliveryAttempt(
            event_id=event.id,
            subscriber_id=subscriber.id,
            attempt_number=DEFERRED_ATTEMPT_NUMBER,
            status="deferred",
            response_body=reason,
            attempted_at=now,
            next_retry_at=now
        )
        db.add(row)
    else:
        row.status = "deferred"
        row.response_body = reason
        row.next_retry_at = now

    db.commit()

    print(
        f"Deferred event {event.id} for subscriber {subscriber.id}: "
        f"{reason}"
    )


def requeue_after_skip(db, event_id, subscriber_id):
    """
    A retry message reached the worker but the subscriber cannot receive
    it (degraded/paused). The scheduler had already flipped the attempt to
    'retry_queued', which would orphan it. Put it back so the scheduler
    picks it up again once the subscriber is active.
    """

    row = db.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.event_id == event_id,
            DeliveryAttempt.subscriber_id == subscriber_id,
            DeliveryAttempt.status == "retry_queued"
        )
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )

    if row is None:
        return

    if row.attempt_number == DEFERRED_ATTEMPT_NUMBER:
        row.status = "deferred"
    else:
        row.status = "pending_retry"

    row.next_retry_at = datetime.now(timezone.utc)

    db.commit()


def refresh_event_status(db, event_id):
    """
    Derive Event.status from its delivery attempts:
      delivered      every targeted subscriber has a success
      dead_lettered  at least one undelivered subscriber was dead-lettered
      retrying       otherwise (pending, deferred or in-flight retries)
    """

    event = db.get(Event, event_id)

    if event is None:
        return

    rows = db.execute(
        select(
            DeliveryAttempt.subscriber_id,
            DeliveryAttempt.status
        ).where(DeliveryAttempt.event_id == event_id)
    ).all()

    if not rows:
        return

    targeted = {subscriber_id for subscriber_id, _ in rows}
    delivered = {
        subscriber_id
        for subscriber_id, status in rows
        if status == "success"
    }
    undelivered = targeted - delivered

    if not undelivered:
        new_status = "delivered"
    else:
        dead = set(
            db.scalars(
                select(DeadLetter.subscriber_id).where(
                    DeadLetter.event_id == event_id
                )
            ).all()
        )

        new_status = (
            "dead_lettered" if undelivered & dead else "retrying"
        )

    if event.status != new_status:
        event.status = new_status
        db.commit()


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
    Check the subscriber's most recent real delivery attempts.

    If the latest CIRCUIT_FAILURE_THRESHOLD attempts
    are all failures, mark the subscriber as degraded.

    Replays and deferred placeholders (attempt_number <= 0) are not
    real delivery attempts and are ignored.
    """

    stmt = (
        select(DeliveryAttempt.status)
        .where(
            DeliveryAttempt.subscriber_id == subscriber.id,
            DeliveryAttempt.attempt_number > 0
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
    attempt_number,
    is_replay=False
):

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
    latency_ms = None

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


def _payload_json(event) -> str:
    return json.dumps(
        event.payload,
        separators=(",", ":"),
        sort_keys=True
    )


async def _deliver_targeted(
    db,
    event,
    subscriber_id,
    replay: bool
):
    """Retry or replay for one specific subscriber."""

    subscriber = db.execute(
        select(Subscriber).where(
            Subscriber.id == subscriber_id
        )
    ).scalar_one_or_none()

    if subscriber is None:
        print(f"Subscriber not found: {subscriber_id}")
        return

    payload_json = _payload_json(event)

    if replay:
        # Replay is an explicit operator action: it bypasses the retry
        # budget and the circuit breaker, and every replay is recorded.
        attempt_number = get_next_replay_number(
            db,
            event.id,
            subscriber.id
        )

        await deliver_to_subscriber(
            db,
            event,
            subscriber,
            payload_json,
            attempt_number,
            is_replay=True
        )

        return

    if has_success(db, event.id, subscriber.id):
        print(
            f"Skipping retry for event {event.id}: already "
            f"delivered to subscriber {subscriber.id}"
        )
        return

    if subscriber.status != "active":
        requeue_after_skip(db, event.id, subscriber.id)

        print(
            f"Subscriber {subscriber.id} is {subscriber.status}; "
            f"retry for event {event.id} kept until it is active"
        )
        return

    attempt_number = await get_next_attempt_number(
        db,
        event.id,
        subscriber.id,
    )

    await deliver_to_subscriber(
        db,
        event,
        subscriber,
        payload_json,
        attempt_number,
    )


async def _fan_out(db, event):
    """Initial delivery to every matching subscriber."""

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
            # degraded subscribers are included so their events are
            # deferred instead of silently dropped; paused are excluded
            Subscriber.status.in_(["active", "degraded"])
        )
    ).all()

    if not subscriptions:

        print(
            f"No active subscribers found for "
            f"event {event.id} "
            f"({event.event_type})"
        )

        event.status = "no_subscribers"
        db.commit()

        return

    payload_json = _payload_json(event)

    for subscription, subscriber in subscriptions:

        # Idempotency guard: a re-processed stream message must not
        # deliver the same event to the same subscriber again.
        if has_attempts(db, event.id, subscriber.id):
            print(
                f"Event {event.id} already processed for "
                f"subscriber {subscriber.id}; skipping"
            )
            continue

        if subscriber.status == "degraded":
            defer_delivery(
                db,
                event,
                subscriber,
                "Subscriber degraded (circuit open); "
                "delivery deferred until recovery"
            )
            continue

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


async def deliver_event(
    event_id: str,
    retry_subscriber_id: str | None = None,
    replay=False,
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
            await _deliver_targeted(
                db,
                event,
                retry_subscriber_id,
                replay
            )
        else:
            await _fan_out(db, event)

        refresh_event_status(db, event.id)

    finally:
        db.close()


async def process_message(message_id, data) -> bool:
    """Deliver one stream message; ACK only if it was handled."""

    event_id = data.get("event_id")
    subscriber_id = data.get("subscriber_id")
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

    try:
        await deliver_event(
            event_id,
            subscriber_id,
            replay=replay,
        )
    except Exception as exc:
        # Leave the message pending: it is reclaimed and retried
        # (up to MAX_DELIVERIES_PER_MESSAGE) instead of killing the worker.
        print(
            f"Error processing message {message_id} "
            f"(event {event_id}): {exc!r}"
        )
        return False

    await redis_client.xack(
        EVENT_STREAM,
        CONSUMER_GROUP,
        message_id
    )

    print(
        f"ACKed message: {message_id}"
    )

    return True


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

                    await process_message(message_id, data)

        except TimeoutError:

            continue

        except Exception as exc:

            print(f"Consumer loop error: {exc!r}")

            await asyncio.sleep(2)


async def reclaim_stale_messages():
    """
    Messages delivered to a consumer that crashed (or failed) before
    ACKing stay in the pending list forever. Periodically claim the
    stale ones and process them again; give up on poison messages.
    """

    while True:

        try:

            result = await redis_client.xautoclaim(
                EVENT_STREAM,
                CONSUMER_GROUP,
                CONSUMER_NAME,
                min_idle_time=RECLAIM_MIN_IDLE_MS,
                start_id="0-0",
                count=10
            )

            claimed = result[1]

            for message_id, data in claimed:

                if not data:
                    # entry no longer exists in the stream
                    await redis_client.xack(
                        EVENT_STREAM,
                        CONSUMER_GROUP,
                        message_id
                    )
                    continue

                pending = await redis_client.xpending_range(
                    EVENT_STREAM,
                    CONSUMER_GROUP,
                    min=message_id,
                    max=message_id,
                    count=1
                )

                deliveries = (
                    pending[0]["times_delivered"] if pending else 0
                )

                if deliveries > MAX_DELIVERIES_PER_MESSAGE:
                    print(
                        f"Giving up on poison message {message_id} "
                        f"after {deliveries} deliveries: {data}"
                    )
                    await redis_client.xack(
                        EVENT_STREAM,
                        CONSUMER_GROUP,
                        message_id
                    )
                    continue

                print(
                    f"Reclaimed stale message {message_id} "
                    f"(delivery #{deliveries})"
                )

                await process_message(message_id, data)

        except Exception as exc:

            print(f"Reclaim loop error: {exc!r}")

        await asyncio.sleep(RECLAIM_INTERVAL_SECONDS)


async def main():

    await asyncio.gather(
        consume_events(),
        run_health_checks(),
        reclaim_stale_messages()
    )


if __name__ == "__main__":
    asyncio.run(main())