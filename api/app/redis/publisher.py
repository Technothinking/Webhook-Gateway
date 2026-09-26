from app.redis.client import redis_client


EVENT_STREAM = "webhook_events"


async def publish_event(event_id: str) -> str:
    message_id = await redis_client.xadd(
        EVENT_STREAM,
        {
            "event_id": event_id
        }
    )

    return message_id