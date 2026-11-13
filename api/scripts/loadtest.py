"""
Week 5 - Day 2: load test helper.

    # 1. before the load: register a subscriber and reset counters
    python /app/scripts/loadtest.py setup

    # 2. run Locust on the host with the printed LOAD_EVENT_TYPE

    # 3. after the load: wait for the backlog, verify, save results
    python /app/scripts/loadtest.py report <event_type>

Run inside the worker container with WEBHOOK_SECRET set (the value the
demo-subscriber uses). Results are saved to api/loadtest_results/<type>.json.
"""
import json
import os
import sys
import time
import uuid

import httpx
import redis
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.delivery_attempt import DeliveryAttempt
from app.models.event import Event
from app.models.subscriber import Subscriber
from app.models.subscription import Subscription

API_URL = os.getenv("DEMO_API_URL", "http://api:8000")
DEMO_URL = os.getenv("DEMO_FLAKY_URL", "http://demo-subscriber:8001")
SECRET = os.getenv("WEBHOOK_SECRET")
DRAIN_TIMEOUT = int(os.getenv("LOAD_DRAIN_TIMEOUT", "900"))

STREAM = "webhook_events"
GROUP = "delivery_workers"
COUNTER_KEY = "worker_processed"
RESULTS_DIR = "/app/loadtest_results"


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def setup():
    if not SECRET:
        sys.exit("WEBHOOK_SECRET is not set.")

    http = httpx.Client(timeout=15.0)
    r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

    if http.get(f"{DEMO_URL}/outage").json()["outage_mode"]:
        http.post(f"{DEMO_URL}/toggle-outage").raise_for_status()
    http.post(f"{DEMO_URL}/received/reset").raise_for_status()
    r.delete(COUNTER_KEY)

    event_type = f"load.{uuid.uuid4().hex[:8]}"

    response = http.post(
        f"{API_URL}/subscribers/",
        json={"name": f"load-{event_type[5:]}", "endpoint_url": f"{DEMO_URL}/webhook"},
    )
    response.raise_for_status()
    subscriber_id = uuid.UUID(response.json()["id"])

    with SessionLocal() as db:
        db.get(Subscriber, subscriber_id).secret = SECRET
        db.commit()

    http.post(
        f"{API_URL}/subscribers/{subscriber_id}/subscriptions",
        json={"event_type": event_type},
    ).raise_for_status()

    log("ready. On your host run:")
    print(f"    set LOAD_EVENT_TYPE={event_type}")
    print(f"then after Locust finishes:")
    print(f"    ... loadtest.py report {event_type}")


def success_query(event_type):
    return (
        select(DeliveryAttempt)
        .join(Event, Event.id == DeliveryAttempt.event_id)
        .where(Event.event_type == event_type, DeliveryAttempt.status == "success")
    )


def delivered_count(db, event_type):
    return db.scalar(
        select(func.count(func.distinct(DeliveryAttempt.event_id)))
        .join(Event, Event.id == DeliveryAttempt.event_id)
        .where(Event.event_type == event_type, DeliveryAttempt.status == "success")
    )


def report(event_type):
    http = httpx.Client(timeout=15.0)
    r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

    with SessionLocal() as db:
        total, first_created, last_created = db.execute(
            select(func.count(Event.id), func.min(Event.created_at), func.max(Event.created_at))
            .where(Event.event_type == event_type)
        ).one()

    if not total:
        sys.exit(f"no events found for {event_type}")

    log(f"{total} events ingested; waiting for deliveries to drain")
    deadline = time.time() + DRAIN_TIMEOUT
    while time.time() < deadline:
        with SessionLocal() as db:
            done = delivered_count(db, event_type)
        if done >= total:
            break
        time.sleep(1)

    time.sleep(3)  # let any stray duplicate show up

    latency = func.extract("epoch", DeliveryAttempt.attempted_at - Event.created_at)

    with SessionLocal() as db:
        delivered = delivered_count(db, event_type)

        successes, p50, p95, p99, worst, last_success = db.execute(
            select(
                func.count(DeliveryAttempt.id),
                func.percentile_cont(0.5).within_group(latency),
                func.percentile_cont(0.95).within_group(latency),
                func.percentile_cont(0.99).within_group(latency),
                func.max(latency),
                func.max(DeliveryAttempt.attempted_at),
            )
            .join(Event, Event.id == DeliveryAttempt.event_id)
            .where(Event.event_type == event_type, DeliveryAttempt.status == "success")
        ).one()

        dup_events = db.scalar(
            select(func.count()).select_from(
                select(DeliveryAttempt.event_id)
                .join(Event, Event.id == DeliveryAttempt.event_id)
                .where(Event.event_type == event_type, DeliveryAttempt.status == "success")
                .group_by(DeliveryAttempt.event_id)
                .having(func.count() > 1)
                .subquery()
            )
        )

        statuses = dict(
            db.execute(
                select(DeliveryAttempt.status, func.count())
                .join(Event, Event.id == DeliveryAttempt.event_id)
                .where(Event.event_type == event_type)
                .group_by(DeliveryAttempt.status)
            ).all()
        )

    receiver = http.get(f"{DEMO_URL}/received").json()
    pending = r.xpending(STREAM, GROUP)["pending"]
    consumers = {k: int(v) for k, v in r.hgetall(COUNTER_KEY).items()}

    ingest_window = max((last_created - first_created).total_seconds(), 1e-9)
    drain_seconds = max((last_success - last_created).total_seconds(), 0) if last_success else None
    delivery_window = (last_success - first_created).total_seconds() if last_success else None
    ms = lambda v: round(float(v) * 1000, 1) if v is not None else None

    result = {
        "event_type": event_type,
        "events": total,
        "delivered": delivered,
        "duplicate_success_events": dup_events,
        "attempt_statuses": statuses,
        "server_side_ingest_rps": round(total / ingest_window, 1),
        "e2e_latency_ms": {"p50": ms(p50), "p95": ms(p95), "p99": ms(p99), "max": ms(worst)},
        "drain_seconds_after_last_ingest": round(drain_seconds, 2) if drain_seconds is not None else None,
        "delivery_throughput_per_s": round(delivered / delivery_window, 1) if delivery_window else None,
        "receiver": receiver,
        "stream_pending": pending,
        "messages_per_consumer": consumers,
    }

    ok = (
        delivered == total
        and dup_events == 0
        and set(statuses) <= {"success"}
        and receiver["total"] == total
        and receiver["duplicates"] == 0
        and pending == 0
    )
    result["pass"] = ok

    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = f"{RESULTS_DIR}/{event_type}.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))
    log(f"saved {path}")
    log("RESULT: PASS - zero loss, zero duplicates under load" if ok else "RESULT: FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "setup":
        setup()
    elif len(sys.argv) >= 3 and sys.argv[1] == "report":
        report(sys.argv[2])
    else:
        sys.exit("usage: loadtest.py setup | loadtest.py report <event_type>")