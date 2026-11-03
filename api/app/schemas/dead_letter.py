from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DeadLetterResponse(BaseModel):
    id: UUID
    event_id: UUID
    subscriber_id: UUID
    failed_reason: str
    moved_at: datetime

    model_config = {
        "from_attributes": True
    }


class DeadLetterFeedItem(BaseModel):
    id: UUID
    event_id: UUID
    event_type: str
    subscriber_id: UUID
    subscriber_name: str
    failed_reason: str
    moved_at: datetime
    replay_count: int
    delivered: bool