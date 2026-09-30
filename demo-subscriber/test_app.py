import hashlib
import hmac

from fastapi.testclient import TestClient

import app


client = TestClient(app.app)

TEST_SECRET = "test-webhook-secret"


def generate_signature(
    secret: str,
    timestamp: str,
    payload: str,
) -> str:
    signed_payload = f"{timestamp}.{payload}"

    return hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def test_webhook_accepts_valid_signature(monkeypatch):
    monkeypatch.setattr(
        app,
        "WEBHOOK_SECRET",
        TEST_SECRET,
    )

    payload = '{"order_id":"ORD-1001","amount":1499}'
    timestamp = "1770000000"

    signature = generate_signature(
        TEST_SECRET,
        timestamp,
        payload,
    )

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": timestamp,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "signature_valid": True,
    }


def test_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(
        app,
        "WEBHOOK_SECRET",
        TEST_SECRET,
    )

    payload = '{"order_id":"ORD-1001","amount":1499}'
    timestamp = "1770000000"

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": "invalid-signature",
            "X-Webhook-Timestamp": timestamp,
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Invalid webhook signature"
    }


def test_webhook_rejects_missing_signature(monkeypatch):
    monkeypatch.setattr(
        app,
        "WEBHOOK_SECRET",
        TEST_SECRET,
    )

    payload = '{"order_id":"ORD-1001","amount":1499}'
    timestamp = "1770000000"

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": timestamp,
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Missing X-Webhook-Signature"
    }


def test_webhook_rejects_missing_timestamp(monkeypatch):
    monkeypatch.setattr(
        app,
        "WEBHOOK_SECRET",
        TEST_SECRET,
    )

    payload = '{"order_id":"ORD-1001","amount":1499}'

    signature = generate_signature(
        TEST_SECRET,
        "",
        payload,
    )

    response = client.post(
        "/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
        },
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Missing X-Webhook-Timestamp"
    }