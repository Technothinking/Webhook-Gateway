"""
Tests for the Week 3 Day 5 reliability fixes in the delivery worker.

Runs the real deliver_event() against the real database; only the outgoing
HTTP call is faked.

    docker compose exec worker pytest /app/tests/test_worker_reliability.py -v
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from sqlalchemy import select

_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in ("/worker", os.path.join(_HERE, "..", "..", "workers")):
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

import worker  # noqa: E402

from app.models.delivery_attempt import DeliveryAttempt  # noqa: E402
from app.models.event import Event  # noqa: E402
from app.models.subscriber import Subscriber  # noqa: E402
from app.models.subscription import Subscription  # noqa: E402

DEFERRED = worker.DEFERRED_ATTEMPT_NUMBER


# ---------- helpers ----------

def unique_type():
    return f"t.{uuid.uuid4().hex[:12]}"


def make_subscriber(db, status="active"):
    subscriber = Subscriber(
        name=f"rel-{uuid.uuid4().hex[:10]}",
        endpoint_url="http://demo-subscriber:8001/webhook",
        secret="reliability-test-secret",
        status=status,
    )
    db.add(subscriber)
    db.commit()
    db.refresh(subscriber)
    return subscriber


def make_event(db, event_type):
    event = Event(
        idempotency_key=f"rel-test-{uuid.uuid4()}",
        event_type=event_type,
        payload={"test": "reliability"},
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def subscribe(db, subscriber, event_type):
    db.add(
        Subscription(
            subscriber_id=subscriber.id,
            event_type=event_type,
            is_active=True,
        )
    )
    db.commit()


def setup(db, status="active"):
    event_type = unique_type()
    subscriber = make_subscriber(db, status)
    subscribe(db, subscriber, event_type)
    event = make_event(db, event_type)
    return event, subscriber


def add_attempt(db, event, subscriber, number, status):
    db.add(
        DeliveryAttempt(
            event_id=event.id,
            subscriber_id=subscriber.id,
            attempt_number=number,
            status=status,
            attempted_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            next_retry_at=None,
        )
    )
    db.commit()


def attempts(db, event, subscriber):
    db.expire_all()
    return db.scalars(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.event_id == event.id,
            DeliveryAttempt.subscriber_id == subscriber.id,
        )
        .order_by(DeliveryAttempt.attempt_number)
    ).all()


class FakeAsyncClient:
    def __init__(self, calls, status_code=200, error=None):
        self.calls = calls
        self.status_code = status_code
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None, timeout=None):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status_code=self.status_code, text="ok")


def patch_http(monkeypatch, status_code=200, error=None):
    calls = []
    monkeypatch.setattr(
        worker.httpx,
        "AsyncClient",
        lambda *a, **k: FakeAsyncClient(calls, status_code, error),
    )
    return calls


def deliver(event, subscriber=None, replay=False):
    asyncio.run(
        worker.deliver_event(
            str(event.id),
            str(subscriber.id) if subscriber is not None else None,
            replay=replay,
        )
    )


# ---------- events while the circuit is open ----------

def test_event_for_degraded_subscriber_is_deferred_not_lost(db, monkeypatch):
    event, subscriber = setup(db, status="degraded")
    calls = patch_http(monkeypatch)

    deliver(event)

    assert calls == []

    rows = attempts(db, event, subscriber)
    assert len(rows) == 1
    assert rows[0].attempt_number == DEFERRED
    assert rows[0].status == "deferred"
    assert rows[0].next_retry_at is not None

    db.refresh(event)
    assert event.status == "retrying"


def test_deferred_row_does_not_consume_retry_budget(db):
    event, subscriber = setup(db, status="degraded")
    add_attempt(db, event, subscriber, DEFERRED, "deferred")

    next_number = asyncio.run(
        worker.get_next_attempt_number(db, event.id, subscriber.id)
    )

    assert next_number == 1


def test_paused_subscriber_gets_nothing(db, monkeypatch):
    event, subscriber = setup(db, status="paused")
    calls = patch_http(monkeypatch)

    deliver(event)

    assert calls == []
    assert attempts(db, event, subscriber) == []

    db.refresh(event)
    assert event.status == "no_subscribers"


# ---------- retries while the circuit is open ----------

def test_queued_retry_is_put_back_when_subscriber_is_degraded(db, monkeypatch):
    event, subscriber = setup(db, status="degraded")
    add_attempt(db, event, subscriber, 1, "retry_queued")
    calls = patch_http(monkeypatch)

    deliver(event, subscriber)

    assert calls == []
    rows = attempts(db, event, subscriber)
    assert len(rows) == 1
    assert rows[0].status == "pending_retry"
    assert rows[0].next_retry_at is not None


def test_queued_deferred_row_goes_back_to_deferred(db, monkeypatch):
    event, subscriber = setup(db, status="degraded")
    add_attempt(db, event, subscriber, DEFERRED, "retry_queued")
    calls = patch_http(monkeypatch)

    deliver(event, subscriber)

    assert calls == []
    rows = attempts(db, event, subscriber)
    assert rows[0].status == "deferred"


def test_retry_proceeds_with_next_attempt_number_when_active(db, monkeypatch):
    event, subscriber = setup(db)
    add_attempt(db, event, subscriber, 1, "retry_queued")
    calls = patch_http(monkeypatch, status_code=200)

    deliver(event, subscriber)

    assert len(calls) == 1
    rows = attempts(db, event, subscriber)
    assert [r.attempt_number for r in rows] == [1, 2]
    assert rows[1].status == "success"

    db.refresh(event)
    assert event.status == "delivered"


def test_retry_for_already_delivered_event_is_skipped(db, monkeypatch):
    event, subscriber = setup(db)
    add_attempt(db, event, subscriber, 1, "success")
    calls = patch_http(monkeypatch)

    deliver(event, subscriber)

    assert calls == []
    assert len(attempts(db, event, subscriber)) == 1


# ---------- replay ----------

def test_replay_bypasses_open_circuit(db, monkeypatch):
    event, subscriber = setup(db, status="degraded")
    calls = patch_http(monkeypatch, status_code=200)

    deliver(event, subscriber, replay=True)

    assert len(calls) == 1
    rows = attempts(db, event, subscriber)
    assert [(r.attempt_number, r.status) for r in rows] == [(0, "success")]


def test_replaying_twice_does_not_crash_or_collide(db, monkeypatch):
    event, subscriber = setup(db)
    calls = patch_http(monkeypatch, status_code=200)

    deliver(event, subscriber, replay=True)
    deliver(event, subscriber, replay=True)
    deliver(event, subscriber, replay=True)

    assert len(calls) == 3
    rows = attempts(db, event, subscriber)
    assert sorted(r.attempt_number for r in rows) == [-3, -2, 0]


# ---------- idempotency / crash safety ----------

def test_reprocessing_the_same_event_does_not_deliver_twice(db, monkeypatch):
    event, subscriber = setup(db)
    calls = patch_http(monkeypatch, status_code=200)

    deliver(event)
    deliver(event)

    assert len(calls) == 1
    assert len(attempts(db, event, subscriber)) == 1


def test_network_error_is_recorded_for_retry_instead_of_crashing(db, monkeypatch):
    event, subscriber = setup(db)
    patch_http(monkeypatch, error=httpx.ConnectError("boom"))

    deliver(event)

    rows = attempts(db, event, subscriber)
    assert len(rows) == 1
    assert rows[0].attempt_number == 1
    assert rows[0].status == "pending_retry"
    assert rows[0].http_status_code is None
    assert rows[0].latency_ms is None
    assert rows[0].next_retry_at is not None


# ---------- circuit breaker window ----------

def test_breaker_ignores_deferred_and_replay_rows(db):
    subscriber = make_subscriber(db)
    event = make_event(db, unique_type())

    for number in range(1, 5):
        add_attempt(db, event, subscriber, number, "failed")

    add_attempt(db, event, subscriber, DEFERRED, "deferred")
    add_attempt(db, event, subscriber, 0, "success")

    asyncio.run(worker.check_circuit_breaker(db, subscriber))
    db.refresh(subscriber)

    # only 4 real attempts exist, below the threshold of 5
    assert subscriber.status == "active"