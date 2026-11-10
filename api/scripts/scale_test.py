"""
Week 5 - Day 1: horizontal scaling check.

Fires N events through the real pipeline while several worker replicas share
one Redis consumer group, then verifies:
  1. every event has exactly one success for the subscriber
  2. no extra delivery attempts were recorded
  3. the receiver saw each payload exactly once (true duplicate check)
  4. no stream messages are left pending
  5. more than one worker actually processed messages

Run inside a worker container:

    docker compose up -d --scale worker=3 worker
    docker compose restart demo-subscriber
    docker compose exec -e WEBHOOK_SECRET=<value from .env> -e EXPECT_WORKERS=3 ^
        worker python /app/scripts/scale_test.py

Optional env: SCALE_EVENTS (200), EXPECT_WORKERS (1), SCALE_TIMEOUT (120),
DEMO_API_URL, DEMO_FLAKY_URL.
"""
import os
import sys
import time
import uuid

import httpx
import redis
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.delivery_attempt import DeliveryAttempt
from app.models.subscriber import Subscriber

API_URL = os.getenv("DEMO_API_URL", "http://api:8000")
DEMO_URL = os.getenv("DEMO_FLAKY_URL", "http://demo-subscriber:8001")
SECRET = os.getenv("WEBHOOK_SECRET")
EVENT_COUNT = int(os.getenv("SCALE_EVENTS", "200"))
EXPECT_WORKERS = int(os.getenv("EXPECT_WORKERS", "1"))
TIMEOUT = int(os.getenv("SCALE_TIMEOUT", "120"))

STREAM = "webhook_events"
GROUP = "delivery_workers"
COUNTER_KEY = "worker_processed"

RUN_ID = uuid.uuid4().hex[:8]
EVENT_TYPE = f"scale.{RUN_ID}"


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def main():
    if not SECRET:
        sys.exit("WEBHOOK_SECRET is not set (use the value the demo-subscriber uses).")

    r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    http = httpx.Client(timeout=15.0)

    # Clean slate: receiver healthy and counters reset
    if http.get(f"{DEMO_URL}/outage").json()["outage_mode"]:
        http.post(f"{DEMO_URL}/toggle-outage").raise_for_status()
    http.post(f"{DEMO_URL}/received/reset").raise_for_status()
    r.delete(COUNTER_KEY)

    # Register a subscriber whose secret matches the demo receiver
    response = http.post(
        f"{API_URL}/subscribers/",
        json={"name": f"scale-{RUN_ID}", "endpoint_url": f"{DEMO_URL}/webhook"},
    )
    response.raise_for_status()
    subscriber_id = uuid.UUID(response.json()["id"])

    with SessionLocal() as db:
        subscriber = db.get(Subscriber, subscriber_id)
        subscriber.secret = SECRET
        db.commit()

    http.post(
        f"{API_URL}/subscribers/{subscriber_id}/subscriptions",
        json={"event_type": EVENT_TYPE},
    ).raise_for_status()

    log(f"run {RUN_ID}: firing {EVENT_COUNT} events, expecting {EXPECT_WORKERS} worker(s)")

    started = time.time()
    for i in range(EVENT_COUNT):
        http.post(
            f"{API_URL}/events/",
            json={
                "event_type": EVENT_TYPE,
                "payload": {"scale_run": RUN_ID, "n": i},
                "idempotency_key": f"{RUN_ID}-{i}",
            },
        ).raise_for_status()
    log(f"ingested in {time.time() - started:.1f}s")

    def delivered():
        with SessionLocal() as db:
            return db.scalar(
                select(func.count(func.distinct(DeliveryAttempt.event_id))).where(
                    DeliveryAttempt.subscriber_id == subscriber_id,
                    DeliveryAttempt.status == "success",
                )
            )

    deadline = time.time() + TIMEOUT
    while time.time() < deadline and delivered() < EVENT_COUNT:
        time.sleep(1)
    log(f"all delivered in {time.time() - started:.1f}s total" if delivered() == EVENT_COUNT
        else f"TIMED OUT with {delivered()}/{EVENT_COUNT} delivered")

    time.sleep(3)  # let any stray duplicate show up

    ok = True

    # 1 + 2. DB view: one success per event, no extra attempts
    with SessionLocal() as db:
        per_event = db.execute(
            select(DeliveryAttempt.event_id, func.count())
            .where(
                DeliveryAttempt.subscriber_id == subscriber_id,
                DeliveryAttempt.status == "success",
            )
            .group_by(DeliveryAttempt.event_id)
        ).all()
        total_attempts = db.scalar(
            select(func.count(DeliveryAttempt.id)).where(
                DeliveryAttempt.subscriber_id == subscriber_id
            )
        )

    missing = EVENT_COUNT - len(per_event)
    dup_success = sum(1 for _, c in per_event if c > 1)
    log(f"db: delivered={len(per_event)}/{EVENT_COUNT} missing={missing} "
        f"duplicate_successes={dup_success} total_attempts={total_attempts}")
    if missing or dup_success or total_attempts != EVENT_COUNT:
        ok = False

    # 3. Receiver view: the real duplicate check
    received = http.get(f"{DEMO_URL}/received").json()
    log(f"receiver: {received}")
    if received["total"] != EVENT_COUNT or received["duplicates"] != 0:
        ok = False

    # 4. Nothing stuck in the pending list
    pending = r.xpending(STREAM, GROUP)["pending"]
    log(f"stream: pending={pending}")
    if pending != 0:
        ok = False

    # 5. Work was actually shared
    counts = {k: int(v) for k, v in r.hgetall(COUNTER_KEY).items()}
    log(f"messages per consumer: {counts}")
    if sum(counts.values()) > EVENT_COUNT:
        log("note: more messages processed than events fired (stale stream entries); "
            "the db and receiver checks above are authoritative")
    if EXPECT_WORKERS > 1 and len(counts) < 2:
        log("only one consumer did any work: replicas are not sharing the group")
        ok = False

    log("RESULT: PASS - no double processing" if ok else "RESULT: FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()