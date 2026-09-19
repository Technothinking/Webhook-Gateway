import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Subscription(Base):
    __tablename__ = "subscription"

    __table_args__ = (
        UniqueConstraint(
            "subscriber_id",
            "event_type",
            name="uq_subscription_subscriber_event_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subscriber.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    
    subscriber: Mapped["Subscriber"] = relationship(back_populates="subscriptions")

    def __repr__(self):
        return (
            f"Subscription("
            f"id={self.id!r}, "
            f"subscriber_id={self.subscriber_id!r}, "
            f"event_type={self.event_type!r}, "
            f"is_active={self.is_active!r}"
            f")"
        )