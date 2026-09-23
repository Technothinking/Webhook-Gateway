def test_create_subscription(client):
    subscriber_payload = {
        "name": "Subscription Test Subscriber",
        "endpoint_url": "https://example.com/subscription-webhook"
    }

    subscriber_response = client.post(
        "/subscribers/",
        json=subscriber_payload
    )

    assert subscriber_response.status_code == 201

    subscriber_id = subscriber_response.json()["id"]

    subscription_response = client.post(
        f"/subscribers/{subscriber_id}/subscriptions",
        json={
            "event_type": "order.created"
        }
    )

    assert subscription_response.status_code == 201

    subscription = subscription_response.json()

    assert subscription["subscriber_id"] == subscriber_id
    assert subscription["event_type"] == "order.created"
    assert subscription["is_active"] is True


def test_duplicate_subscription_rejected(client):
    subscriber_payload = {
        "name": "Duplicate Subscription Test",
        "endpoint_url": "https://example.com/duplicate-webhook"
    }

    subscriber_response = client.post(
        "/subscribers/",
        json=subscriber_payload
    )

    assert subscriber_response.status_code == 201

    subscriber_id = subscriber_response.json()["id"]

    subscription_payload = {
        "event_type": "payment.completed"
    }

    first_response = client.post(
        f"/subscribers/{subscriber_id}/subscriptions",
        json=subscription_payload
    )

    assert first_response.status_code == 201

    second_response = client.post(
        f"/subscribers/{subscriber_id}/subscriptions",
        json=subscription_payload
    )

    assert second_response.status_code == 409
    assert second_response.json()["detail"] == (
        "Subscriber is already subscribed to this event type"
    )