from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.delivery_attempt import DeliveryAttempt
from app.models.event import Event
from app.models.subscriber import Subscriber
from app.schemas.delivery_attempt import DeliveryAttemptFeedItem

router = APIRouter(prefix="/delivery-attempts", tags=["Delivery Attempts"])


@router.get("", response_model=list[DeliveryAttemptFeedItem])
def list_delivery_attempts(
    since: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = (
        select(DeliveryAttempt, Subscriber.name, Event.event_type)
        .join(Subscriber, DeliveryAttempt.subscriber_id == Subscriber.id)
        .join(Event, DeliveryAttempt.event_id == Event.id)
        .order_by(DeliveryAttempt.attempted_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(DeliveryAttempt.attempted_at > since)

    return [
        DeliveryAttemptFeedItem(
            id=a.id,
            event_id=a.event_id,
            event_type=event_type,
            subscriber_id=a.subscriber_id,
            subscriber_name=name,
            attempt_number=a.attempt_number,
            status=a.status,
            http_status_code=a.http_status_code,
            latency_ms=a.latency_ms,
            attempted_at=a.attempted_at,
            next_retry_at=a.next_retry_at,
        )
        for a, name, event_type in db.execute(stmt).all()
    ]