import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone

import httpx
import redis.asyncio as redis
from redis.exceptions import TimeoutError
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.delivery_attempt import DeliveryAttempt
from app.models.event import Event
from app.models.subscriber import Subscriber
from app.models.subscription import Subscription

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


async def deliver_event(event_id: str):
    db = SessionLocal()

    try:
        event = db.execute(
            select(Event).where(Event.id == event_id)
        ).scalar_one_or_none()

        if event is None:
            print(f"Event not found: {event_id}")
            return

        subscriptions = db.execute(
            select(Subscription, Subscriber)
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
                f"No active subscribers found for event "
                f"{event_id} ({event.event_type})"
            )
            return

        payload_json = json.dumps(
            event.payload,
            separators=(",", ":"),
            sort_keys=True
        )

        async with httpx.AsyncClient() as client:

            for subscription, subscriber in subscriptions:
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

                attempt_number = 1
                start_time = time.perf_counter()

                try:
                    response = await client.post(
                        subscriber.endpoint_url,
                        content=payload_json,
                        headers=headers,
                        timeout=10.0
                    )

                    latency_ms = int(
                        (time.perf_counter() - start_time) * 1000
                    )

                    delivery_status = (
                        "success"
                        if 200 <= response.status_code < 300
                        else "failed"
                    )

                    delivery_attempt = DeliveryAttempt(
                        event_id=event.id,
                        subscriber_id=subscriber.id,
                        attempt_number=attempt_number,
                        status=delivery_status,
                        http_status_code=response.status_code,
                        response_body=response.text,
                        latency_ms=latency_ms,
                        attempted_at=datetime.now(timezone.utc)
                    )

                    db.add(delivery_attempt)
                    db.commit()

                    print(
                        f"Delivered event {event.id} to "
                        f"{subscriber.endpoint_url} "
                        f"status={response.status_code} "
                        f"latency={latency_ms}ms"
                    )

                except httpx.RequestError as exc:
                    latency_ms = int(
                        (time.perf_counter() - start_time) * 1000
                    )

                    delivery_attempt = DeliveryAttempt(
                        event_id=event.id,
                        subscriber_id=subscriber.id,
                        attempt_number=attempt_number,
                        status="failed",
                        http_status_code=None,
                        response_body=str(exc),
                        latency_ms=latency_ms,
                        attempted_at=datetime.now(timezone.utc)
                    )

                    db.add(delivery_attempt)
                    db.commit()

                    print(
                        f"Delivery failed for event {event.id} "
                        f"to {subscriber.endpoint_url}: {exc}"
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

                    print(
                        f"Received event: {event_id} "
                        f"(message_id={message_id})"
                    )

                    await deliver_event(event_id)

                    await redis_client.xack(
                        EVENT_STREAM,
                        CONSUMER_GROUP,
                        message_id
                    )

                    print(
                        f"ACKed message: {message_id}"
                    )

        except TimeoutError:
            # Expected when the stream is idle.
            continue


async def main():
    await consume_events()


if __name__ == "__main__":
    asyncio.run(main())