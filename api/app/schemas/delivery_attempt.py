from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class DeliveryAttemptFeedItem(BaseModel):
    id: UUID
    event_id: UUID
    event_type: str
    subscriber_id: UUID
    subscriber_name: str
    attempt_number: int
    status: str
    http_status_code: int | None
    latency_ms: int | None
    attempted_at: datetime
    next_retry_at: datetime | None