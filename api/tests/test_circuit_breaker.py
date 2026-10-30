"""
Week 3 - Day 5: circuit breaker tests.

Exercises the real worker functions (check_circuit_breaker and
health_check_subscriber) against the real database. Only the outgoing HTTP
call of the health check is faked.

Run inside the API container:
    docker compose exec api pytest tests/test_circuit_breaker.py -v
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

# The worker lives outside the api package: /worker in Docker, ../../workers locally.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in ("/worker", os.path.join(_HERE, "..", "..", "workers")):
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

import worker  # noqa: E402

from app.models.delivery_attempt import DeliveryAttempt  # noqa: E402
from app.models.event import Event  # noqa: E402
from app.models.subscriber import Subscriber  # noqa: E402

THRESHOLD = worker.CIRCUIT_FAILURE_THRESHOLD


# ---------- helpers ----------

def make_subscriber(db, status="active"):
    subscriber = Subscriber(
        name=f"cb-{uuid.uuid4().hex[:10]}",
        endpoint_url="http://demo-subscriber:8001/webhook",
        secret="circuit-breaker-test-secret",
        status=status,
    )
    db.add(subscriber)
    db.commit()
    db.refresh(subscriber)
    return subscriber


def make_event(db):
    event = Event(
        idempotency_key=f"cb-test-{uuid.uuid4()}",
        event_type="order.created",
        payload={"test": "circuit-breaker"},
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def add_attempts(db, subscriber, statuses):
    """Insert attempts oldest -> newest, one per status."""
    event = make_event(db)
    base = datetime.now(timezone.utc) - timedelta(minutes=10)

    for number, status in enumerate(statuses, start=1):
        db.add(
            DeliveryAttempt(
                event_id=event.id,
                subscriber_id=subscriber.id,
                attempt_number=number,
                status=status,
                http_status_code=200 if status == "success" else 500,
                attempted_at=base + timedelta(seconds=number),
            )
        )
    db.commit()


def run_breaker(db, subscriber):
    asyncio.run(worker.check_circuit_breaker(db, subscriber))
    db.refresh(subscriber)


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient in the health check."""

    def __init__(self, status_code=None, error=None, calls=None):
        self.status_code = status_code
        self.error = error
        self.calls = calls if calls is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None, timeout=None):
        self.calls.append({"url": url, "content": content, "headers": headers})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status_code=self.status_code)


def patch_http(monkeypatch, **kwargs):
    calls = []
    monkeypatch.setattr(
        worker.httpx,
        "AsyncClient",
        lambda *a, **k: FakeAsyncClient(calls=calls, **kwargs),
    )
    return calls


# ---------- circuit opens ----------

def test_five_consecutive_failures_open_the_circuit(db):
    subscriber = make_subscriber(db)
    add_attempts(db, subscriber, ["failed"] * THRESHOLD)

    run_breaker(db, subscriber)

    assert subscriber.status == "degraded"


def test_pending_and_queued_retries_count_as_failures(db):
    subscriber = make_subscriber(db)
    add_attempts(
        db,
        subscriber,
        ["pending_retry", "retry_queued", "pending_retry", "retry_queued", "failed"],
    )

    run_breaker(db, subscriber)

    assert subscriber.status == "degraded"


def test_fewer_failures_than_threshold_keeps_circuit_closed(db):
    subscriber = make_subscriber(db)
    add_attempts(db, subscriber, ["failed"] * (THRESHOLD - 1))

    run_breaker(db, subscriber)

    assert subscriber.status == "active"


def test_a_success_in_the_window_keeps_circuit_closed(db):
    subscriber = make_subscriber(db)
    add_attempts(
        db, subscriber, ["failed", "failed", "success", "failed", "failed"]
    )

    run_breaker(db, subscriber)

    assert subscriber.status == "active"


def test_paused_subscriber_is_not_flipped_to_degraded(db):
    subscriber = make_subscriber(db, status="paused")
    add_attempts(db, subscriber, ["failed"] * THRESHOLD)

    run_breaker(db, subscriber)

    assert subscriber.status == "paused"


def test_failures_of_one_subscriber_do_not_affect_another(db):
    failing = make_subscriber(db)
    healthy = make_subscriber(db)
    add_attempts(db, failing, ["failed"] * THRESHOLD)
    add_attempts(db, healthy, ["success"] * THRESHOLD)

    run_breaker(db, failing)
    run_breaker(db, healthy)

    assert failing.status == "degraded"
    assert healthy.status == "active"


# ---------- circuit closes ----------

def test_successful_health_check_closes_the_circuit(db, monkeypatch):
    subscriber = make_subscriber(db, status="degraded")
    calls = patch_http(monkeypatch, status_code=200)

    result = asyncio.run(worker.health_check_subscriber(db, subscriber))
    db.refresh(subscriber)

    assert result is True
    assert subscriber.status == "active"

    # The probe is signed and flagged as a health check
    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert headers["X-Webhook-Health-Check"] == "true"
    assert headers["X-Webhook-Signature"] == worker.generate_signature(
        subscriber.secret, headers["X-Webhook-Timestamp"], calls[0]["content"]
    )


def test_failed_health_check_keeps_subscriber_degraded(db, monkeypatch):
    subscriber = make_subscriber(db, status="degraded")
    patch_http(monkeypatch, status_code=500)

    result = asyncio.run(worker.health_check_subscriber(db, subscriber))
    db.refresh(subscriber)

    assert result is False
    assert subscriber.status == "degraded"


def test_unreachable_endpoint_keeps_subscriber_degraded(db, monkeypatch):
    subscriber = make_subscriber(db, status="degraded")
    patch_http(monkeypatch, error=httpx.ConnectError("connection refused"))

    result = asyncio.run(worker.health_check_subscriber(db, subscriber))
    db.refresh(subscriber)

    assert result is False
    assert subscriber.status == "degraded"


# ---------- full cycle ----------

def test_full_cycle_open_then_recover(db, monkeypatch):
    """The Week 3 Day 5 requirement: 5 failures -> degraded -> success -> active."""
    subscriber = make_subscriber(db)
    add_attempts(db, subscriber, ["failed"] * THRESHOLD)

    run_breaker(db, subscriber)
    assert subscriber.status == "degraded"

    patch_http(monkeypatch, status_code=200)
    asyncio.run(worker.health_check_subscriber(db, subscriber))
    db.refresh(subscriber)

    assert subscriber.status == "active"