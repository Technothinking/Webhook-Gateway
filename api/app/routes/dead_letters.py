from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dead_letter import DeadLetter
from app.models.delivery_attempt import DeliveryAttempt as A
from app.models.event import Event
from app.models.subscriber import Subscriber
from app.redis.publisher import publish_event
from app.schemas.dead_letter import DeadLetterFeedItem


router = APIRouter(prefix="/dead-letters", tags=["Dead Letters"])

DEFERRED_ATTEMPT_NUMBER = -1


@router.get("", response_model=list[DeadLetterFeedItem])
def list_dead_letters(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(DeadLetter, Subscriber.name, Event.event_type)
        .join(Subscriber, DeadLetter.subscriber_id == Subscriber.id)
        .join(Event, DeadLetter.event_id == Event.id)
        .order_by(DeadLetter.moved_at.desc())
        .limit(limit)
    ).all()

    if not rows:
        return []

    event_ids = {d.event_id for d, _, _ in rows}

    # All non-placeholder attempts for these events, grouped per
    # (event, subscriber): replays are attempt_number <= 0 (excluding -1).
    attempts = db.execute(
        select(A.event_id, A.subscriber_id, A.attempt_number, A.status)
        .where(
            A.event_id.in_(event_ids),
            A.attempt_number != DEFERRED_ATTEMPT_NUMBER,
        )
    ).all()

    replay_counts: dict[tuple, int] = {}
    delivered: set[tuple] = set()

    for event_id, subscriber_id, number, status in attempts:
        key = (event_id, subscriber_id)
        if number <= 0:
            replay_counts[key] = replay_counts.get(key, 0) + 1
        if status == "success":
            delivered.add(key)

    return [
        DeadLetterFeedItem(
            id=d.id,
            event_id=d.event_id,
            event_type=event_type,
            subscriber_id=d.subscriber_id,
            subscriber_name=name,
            failed_reason=d.failed_reason,
            moved_at=d.moved_at,
            replay_count=replay_counts.get((d.event_id, d.subscriber_id), 0),
            delivered=(d.event_id, d.subscriber_id) in delivered,
        )
        for d, name, event_type in rows
    ]


@router.post("/{dead_letter_id}/replay")
async def replay_dead_letter(
    dead_letter_id: UUID,
    db: Session = Depends(get_db),
):
    dead_letter = db.scalar(
        select(DeadLetter).where(DeadLetter.id == dead_letter_id)
    )

    if dead_letter is None:
        raise HTTPException(
            status_code=404,
            detail="Dead letter not found",
        )

    await publish_event(
        dead_letter.event_id,
        subscriber_id=dead_letter.subscriber_id,
        replay=True,
    )

    return {
        "message": "Dead letter replay queued successfully.",
        "dead_letter_id": dead_letter.id,
        "event_id": dead_letter.event_id,
        "subscriber_id": dead_letter.subscriber_id,
    }