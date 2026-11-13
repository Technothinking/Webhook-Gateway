import uuid


def test_root(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Webhook Reliability Gateway API is running."
    }


def test_event_idempotency(client):
    idempotency_key = f"test-idempotency-{uuid.uuid4()}"

    payload = {
        "event_type": "order.created",
        "payload": {
            "order_id": 123
        },
        "idempotency_key": idempotency_key
    }

    # First request should create the event
    response1 = client.post("/events/", json=payload)

    assert response1.status_code == 201

    event1 = response1.json()

    # Second request with the same idempotency key
    response2 = client.post("/events/", json=payload)

    assert response2.status_code == 200

    event2 = response2.json()

    # Both responses should refer to the same event
    assert event1["id"] == event2["id"]
    assert event1["idempotency_key"] == event2["idempotency_key"]

def test_event_is_kept_when_publish_fails(client, monkeypatch):
    async def redis_down(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.routes.events.publish_event", redis_down)

    response = client.post(
        "/events/",
        json={
            "event_type": "order.created",
            "payload": {"a": 1},
            "idempotency_key": f"test-publish-fail-{uuid.uuid4()}",
        },
    )

    assert response.status_code == 201
    assert client.get(f"/events/{response.json()['id']}").status_code == 200