import logging
from uuid import UUID

from fastapi import APIRouter, Depends, status, Response, HTTPException
from fastapi import Query
from fastapi.concurrency import run_in_threadpool

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.event import Event
from app.schemas.event import EventCreate, EventResponse

from app.redis.publisher import publish_event

from datetime import datetime, timezone
from sqlalchemy import update
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["Events"])

def _insert_event(db: Session, event: EventCreate) -> tuple[EventResponse, bool]:
    """
    All blocking DB work for ingest. Runs in a worker thread so it never
    stalls the event loop, and releases its connection before returning so
    nothing is held while the caller talks to Redis.
    Returns (event, created).
    """
    try:
        existing = db.scalar(
            select(Event).where(Event.idempotency_key == event.idempotency_key)
        )
        if existing is not None:
            return EventResponse.model_validate(existing), False

        db_event = Event(**event.model_dump())
        db.add(db_event)

        try:
            db.commit()
            db.refresh(db_event)
        except IntegrityError:
            # A concurrent request inserted the same key first.
            db.rollback()
            existing = db.scalar(
                select(Event).where(Event.idempotency_key == event.idempotency_key)
            )
            return EventResponse.model_validate(existing), False

        return EventResponse.model_validate(db_event), True
    finally:
        db.close()

def _mark_published(event_id) -> None:
    with SessionLocal() as db:
        db.execute(
            update(Event)
            .where(Event.id == event_id)
            .values(published_at=datetime.now(timezone.utc))
        )
        db.commit()


@router.post("/", response_model=EventResponse)
async def create_event(
    event: EventCreate,
    response: Response,
    db: Session = Depends(get_db)
):
    result, created = await run_in_threadpool(_insert_event, db, event)

    if not created:
        response.status_code = status.HTTP_200_OK
        return result

    # The event is already durable. If the publish fails, do not fail the
    # request: the reconciler in the retry scheduler republishes it.
    try:
        await publish_event(str(result.id))
        await run_in_threadpool(_mark_published, result.id)
    except Exception:
        logger.exception("publish failed for event %s; reconciler will retry", result.id)

    response.status_code = status.HTTP_201_CREATED
    return result

@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: UUID,
    db: Session = Depends(get_db)
):
    stmt = select(Event).where(Event.id == event_id)

    result = db.execute(stmt)
    event = result.scalar_one_or_none()

    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found"
        )

    return event


@router.get("/", response_model=list[EventResponse])
def get_events(
    event_type: str | None = None,
    event_status: str | None = Query(default=None, alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    stmt = select(Event)

    if event_type is not None:
        stmt = stmt.where(Event.event_type == event_type)

    if event_status is not None:
        stmt = stmt.where(Event.status == event_status)

    stmt = (
        stmt
        .order_by(Event.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    result = db.execute(stmt)

    return result.scalars().all()