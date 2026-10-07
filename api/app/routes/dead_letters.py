from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dead_letter import DeadLetter
from app.redis.publisher import publish_event
from app.schemas.dead_letter import DeadLetterResponse


router = APIRouter(prefix="/dead-letters", tags=["Dead Letters"])


@router.get("", response_model=list[DeadLetterResponse])
def list_dead_letters(
    db: Session = Depends(get_db),
):
    dead_letters = db.scalars(
        select(DeadLetter)
        .order_by(DeadLetter.moved_at.desc())
    ).all()

    return dead_letters


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