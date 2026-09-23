def test_create_subscriber(client):
    payload = {
        "name": "Test Subscriber",
        "endpoint_url": "https://example.com/webhook"
    }

    response = client.post("/subscribers/", json=payload)

    assert response.status_code == 201

    data = response.json()

    assert data["name"] == "Test Subscriber"
    assert data["endpoint_url"] == "https://example.com/webhook"
    assert data["status"] == "active"
    assert data["secret"] is not None
    assert len(data["secret"]) > 0
    assert "id" in data
    assert "created_at" in data


def test_list_subscribers(client):
    payload = {
        "name": "List Test Subscriber",
        "endpoint_url": "https://example.com/list-webhook"
    }

    create_response = client.post("/subscribers/", json=payload)

    assert create_response.status_code == 201

    response = client.get("/subscribers/")

    assert response.status_code == 200

    subscribers = response.json()

    assert isinstance(subscribers, list)
    assert len(subscribers) >= 1

    subscriber = next(
        subscriber
        for subscriber in subscribers
        if subscriber["id"] == create_response.json()["id"]
    )

    assert subscriber["name"] == "List Test Subscriber"
    assert subscriber["status"] == "active"

    # Secret must not be exposed by the list endpoint
    assert "secret" not in subscriber


def test_update_subscriber(client):
    payload = {
        "name": "Update Test Subscriber",
        "endpoint_url": "https://example.com/update-webhook"
    }

    create_response = client.post("/subscribers/", json=payload)

    assert create_response.status_code == 201

    subscriber_id = create_response.json()["id"]

    update_response = client.patch(
        f"/subscribers/{subscriber_id}",
        json={"status": "paused"}
    )

    assert update_response.status_code == 200

    updated_subscriber = update_response.json()

    assert updated_subscriber["id"] == subscriber_id
    assert updated_subscriber["status"] == "paused"