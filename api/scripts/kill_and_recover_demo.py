"""
Week 3 - Day 5: the "kill and recover" demo.

Scenario
    1. Register a FLAKY and a HEALTHY subscriber for a fresh event type.
    2. Take the flaky subscriber down (outage mode).
    3. Fire N events.  Retries queue up, the circuit breaker trips, the
       flaky subscriber becomes 'degraded' and later events are deferred.
    4. Bring the flaky subscriber back.  The health check closes the
       circuit and the backlog drains.
    5. Replay anything that was dead-lettered.
    6. Verify: every event reached BOTH subscribers exactly once, and the
       healthy subscriber never saw a retry or duplicate.

Run it inside the worker container (it has DB access and the Docker network):

    docker compose exec -e WEBHOOK_SECRET=<value from your .env> worker ^
        python /app/scripts/kill_and_recover_demo.py

Optional env: DEMO_EVENTS (default 100), WEBHOOK_SECRET_2, DEMO_API_URL,
DEMO_FLAKY_URL, DEMO_HEALTHY_URL, DEMO_TIMEOUT (seconds per wait, 300).
"""
import os
import sys
import time
import uuid

import httpx
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.dead_letter import DeadLetter
from app.models.delivery_attempt import DeliveryAttempt
from app.models.subscriber import Subscriber

API_URL = os.getenv("DEMO_API_URL", "http://api:8000")
FLAKY_URL = os.getenv("DEMO_FLAKY_URL", "http://demo-subscriber:8001")
HEALTHY_URL = os.getenv("DEMO_HEALTHY_URL", "http://demo-subscriber-2:8001")
FLAKY_SECRET = os.getenv("WEBHOOK_SECRET")
HEALTHY_SECRET = os.getenv("WEBHOOK_SECRET_2", "day5-healthy-secret")
EVENT_COUNT = int(os.getenv("DEMO_EVENTS", "100"))
TIMEOUT = int(os.getenv("DEMO_TIMEOUT", "300"))

RUN_ID = uuid.uuid4().hex[:8]
EVENT_TYPE = f"demo.{RUN_ID}"

client = httpx.Client(timeout=15.0)


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def set_outage(base_url, desired):
    """/toggle-outage only toggles, so read the state back and fix it."""
    state = client.post(f"{base_url}/toggle-outage").json()["outage_mode"]
    if state != desired:
        state = client.post(f"{base_url}/toggle-outage").json()["outage_mode"]
    assert state == desired, f"could not set outage={desired} on {base_url}"


def register(name, base_url, secret):
    response = client.post(
        f"{API_URL}/subscribers/",
        json={"name": name, "endpoint_url": f"{base_url}/webhook"},
    )
    response.raise_for_status()
    subscriber_id = response.json()["id"]

    # The demo receivers verify signatures with a fixed secret, so align it.
    with SessionLocal() as db:
        subscriber = db.get(Subscriber, uuid.UUID(subscriber_id))
        subscriber.secret = secret
        db.commit()

    client.post(
        f"{API_URL}/subscribers/{subscriber_id}/subscriptions",
        json={"event_type": EVENT_TYPE},
    ).raise_for_status()

    return uuid.UUID(subscriber_id)


def subscriber_status(subscriber_id):
    with SessionLocal() as db:
        return db.get(Subscriber, subscriber_id).status


def delivered_events(subscriber_id, event_ids):
    with SessionLocal() as db:
        return set(
            db.scalars(
                select(DeliveryAttempt.event_id).where(
                    DeliveryAttempt.subscriber_id == subscriber_id,
                    DeliveryAttempt.status == "success",
                    DeliveryAttempt.event_id.in_(event_ids),
                )
            ).all()
        )


def dead_letters(subscriber_id, event_ids):
    with SessionLocal() as db:
        return db.scalars(
            select(DeadLetter).where(
                DeadLetter.subscriber_id == subscriber_id,
                DeadLetter.event_id.in_(event_ids),
            )
        ).all()


def attempt_summary(subscriber_id, event_ids):
    with SessionLocal() as db:
        rows = db.execute(
            select(DeliveryAttempt.status, func.count())
            .where(
                DeliveryAttempt.subscriber_id == subscriber_id,
                DeliveryAttempt.event_id.in_(event_ids),
            )
            .group_by(DeliveryAttempt.status)
        ).all()
    return dict(rows)


def wait_for(description, predicate, timeout=TIMEOUT, interval=2.0):
    log(f"waiting for: {description}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    log(f"TIMED OUT waiting for: {description}")
    return False


def main():
    if not FLAKY_SECRET:
        sys.exit(
            "WEBHOOK_SECRET is not set. Pass the same value the demo-subscriber "
            "container uses: docker compose exec -e WEBHOOK_SECRET=... worker ..."
        )

    log(f"run {RUN_ID}: {EVENT_COUNT} events of type {EVENT_TYPE}")

    flaky = register(f"demo-flaky-{RUN_ID}", FLAKY_URL, FLAKY_SECRET)
    healthy = register(f"demo-healthy-{RUN_ID}", HEALTHY_URL, HEALTHY_SECRET)
    set_outage(HEALTHY_URL, False)
    set_outage(FLAKY_URL, False)

    ok = True
    event_ids = []

    try:
        # 1. kill
        log("KILL: flaky subscriber goes into outage mode")
        set_outage(FLAKY_URL, True)

        # 2. fire
        log(f"firing {EVENT_COUNT} events")
        for i in range(EVENT_COUNT):
            response = client.post(
                f"{API_URL}/events/",
                json={
                    "event_type": EVENT_TYPE,
                    "payload": {"demo_run": RUN_ID, "n": i},
                    "idempotency_key": f"{RUN_ID}-{i}",
                },
            )
            response.raise_for_status()
            event_ids.append(uuid.UUID(response.json()["id"]))

        # 3. breaker trips
        if not wait_for(
            "circuit breaker to open (flaky -> degraded)",
            lambda: subscriber_status(flaky) == "degraded",
        ):
            ok = False
        else:
            log("circuit OPEN. flaky attempts so far: "
                f"{attempt_summary(flaky, event_ids)}")

        if not wait_for(
            "all events to reach the healthy subscriber",
            lambda: len(delivered_events(healthy, event_ids)) == EVENT_COUNT,
        ):
            ok = False
        else:
            log("healthy subscriber received every event while the flaky one was down")

        # 4. recover
        log("RECOVER: flaky subscriber comes back")
        set_outage(FLAKY_URL, False)

        if not wait_for(
            "health check to close the circuit (flaky -> active)",
            lambda: subscriber_status(flaky) == "active",
        ):
            ok = False

        # 5. drain backlog, then replay dead letters
        def drained():
            delivered = delivered_events(flaky, event_ids)
            dead = {d.event_id for d in dead_letters(flaky, event_ids)}
            return len(delivered) + len(dead - delivered) >= EVENT_COUNT

        wait_for("flaky backlog to drain", drained)

        dead = [
            d for d in dead_letters(flaky, event_ids)
            if d.event_id not in delivered_events(flaky, event_ids)
        ]
        if dead:
            log(f"replaying {len(dead)} dead-lettered deliveries")
            for letter in dead:
                client.post(
                    f"{API_URL}/dead-letters/{letter.id}/replay"
                ).raise_for_status()

        if not wait_for(
            "flaky subscriber to have every event",
            lambda: len(delivered_events(flaky, event_ids)) == EVENT_COUNT,
        ):
            ok = False

        # 6. verify exactly-once success per (event, subscriber)
        time.sleep(3)  # let any stray duplicate show up
        for label, subscriber_id in (("flaky", flaky), ("healthy", healthy)):
            with SessionLocal() as db:
                per_event = db.execute(
                    select(DeliveryAttempt.event_id, func.count())
                    .where(
                        DeliveryAttempt.subscriber_id == subscriber_id,
                        DeliveryAttempt.status == "success",
                        DeliveryAttempt.event_id.in_(event_ids),
                    )
                    .group_by(DeliveryAttempt.event_id)
                ).all()
            missing = EVENT_COUNT - len(per_event)
            duplicates = sum(1 for _, count in per_event if count > 1)
            log(f"{label}: delivered={len(per_event)}/{EVENT_COUNT} "
                f"missing={missing} duplicate_successes={duplicates} "
                f"attempts={attempt_summary(subscriber_id, event_ids)}")
            if missing or duplicates:
                ok = False

        healthy_summary = attempt_summary(healthy, event_ids)
        if set(healthy_summary) - {"success"}:
            log("healthy subscriber saw failures/retries: "
                f"{healthy_summary}")
            ok = False

    finally:
        set_outage(FLAKY_URL, False)

    log("RESULT: PASS - zero loss, zero duplicates" if ok else "RESULT: FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()