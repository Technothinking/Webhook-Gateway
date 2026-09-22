from pydantic import BaseModel, HttpUrl
from uuid import UUID
from datetime import datetime
from typing import Literal

class SubscriberCreate(BaseModel):
    name: str
    endpoint_url: HttpUrl


class SubscriberResponse(BaseModel):
    id: UUID
    name: str
    endpoint_url: str
    status: str
    created_at: datetime


class SubscriberCreateResponse(SubscriberResponse):
    secret: str


class SubscriberUpdate(BaseModel):
    status: Literal["active", "paused"]