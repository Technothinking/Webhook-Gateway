from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EventCreate(BaseModel):
    event_type: str
    payload: dict
    idempotency_key: str


class EventResponse(BaseModel):
    id: UUID
    idempotency_key: str
    event_type: str
    payload: dict
    created_at: datetime
    status: str

    model_config = ConfigDict(from_attributes=True)