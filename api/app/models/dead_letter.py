import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DeadLetter(Base):
    __tablename__ = "dead_letter"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("event.id"), nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subscriber.id"), nullable=False)
    failed_reason: Mapped[str] = mapped_column(Text, nullable=False)
    moved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    event: Mapped["Event"] = relationship(back_populates="dead_letters")
    subscriber: Mapped["Subscriber"] = relationship(back_populates="dead_letters")

    def __repr__(self):
        return (
            f"DeadLetter("
            f"id={self.id!r}, "
            f"event_id={self.event_id!r}, "
            f"subscriber_id={self.subscriber_id!r}, "
            f"failed_reason={self.failed_reason!r}"
            f")"
        )