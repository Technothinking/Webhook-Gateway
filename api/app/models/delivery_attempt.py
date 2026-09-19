import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempt"

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "subscriber_id",
            "attempt_number",
            name="uq_delivery_attempt_event_subscriber_number"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("event.id"), nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subscriber.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped["Event"] = relationship(back_populates="delivery_attempts")
    subscriber: Mapped["Subscriber"] = relationship(back_populates="delivery_attempts")

    def __repr__(self):
        return (
            f"DeliveryAttempt("
            f"id={self.id!r}, "
            f"event_id={self.event_id!r}, "
            f"subscriber_id={self.subscriber_id!r}, "
            f"attempt_number={self.attempt_number!r}, "
            f"status={self.status!r}, "
            f"http_status_code={self.http_status_code!r}"
            f")"
        )