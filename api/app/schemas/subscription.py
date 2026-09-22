from pydantic import BaseModel
from uuid import UUID


class SubscriptionCreate(BaseModel):
    event_type: str


class SubscriptionResponse(BaseModel):
    id: UUID
    subscriber_id: UUID
    event_type: str
    is_active: bool