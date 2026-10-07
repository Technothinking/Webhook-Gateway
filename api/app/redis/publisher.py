from app.redis.client import redis_client


async def publish_event(
    event_id,
    subscriber_id=None,
    replay=False,
):
    message = {
        "event_id": str(event_id),
    }

    if subscriber_id is not None:
        message["subscriber_id"] = str(subscriber_id)

    if replay:
        message["replay"] = "true"

    await redis_client.xadd(
        "webhook_events",
        message,
    )