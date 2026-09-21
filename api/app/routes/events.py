from fastapi import APIRouter, Depends, status, Response, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from uuid import UUID
from fastapi import Query

from app.db.session import SessionLocal
from app.models.event import Event
from app.schemas.event import EventCreate, EventResponse

eventRouter = APIRouter(prefix="/events", tags=["Events"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@eventRouter.post("/",response_model=EventResponse,)
def create_event(event: EventCreate, response: Response, db: Session = Depends(get_db)):
    # First check for an existing event
    stmt = select(Event).where(
        Event.idempotency_key == event.idempotency_key
    )

    result = db.execute(stmt)
    existing_event = result.scalar_one_or_none()

    # Duplicate request
    if existing_event:
        response.status_code = status.HTTP_200_OK
        return existing_event

    # Create new event
    db_event = Event(**event.model_dump())
    db.add(db_event)

    try:
        db.commit()
        db.refresh(db_event)

        response.status_code = status.HTTP_201_CREATED
        return db_event

    except IntegrityError:
        # Another concurrent request inserted the same
        # idempotency key before this transaction committed.
        db.rollback()

        stmt = select(Event).where(
            Event.idempotency_key == event.idempotency_key
        )

        result = db.execute(stmt)
        existing_event = result.scalar_one()

        response.status_code = status.HTTP_200_OK
        return existing_event


@eventRouter.get("/{event_id}", response_model=EventResponse)
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


@eventRouter.get("/", response_model=list[EventResponse])
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