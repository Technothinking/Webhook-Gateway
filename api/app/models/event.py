import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Event(Base):
    __tablename__ = "event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    delivery_attempts: Mapped[list["DeliveryAttempt"]] = relationship(back_populates="event")
    dead_letters: Mapped[list["DeadLetter"]] = relationship(back_populates="event")

    def __repr__(self):
        return (
            f"Event("
            f"id={self.id!r}, "
            f"idempotency_key={self.idempotency_key!r}, "
            f"event_type={self.event_type!r}, "
            f"status={self.status!r}"
            f")"
        )