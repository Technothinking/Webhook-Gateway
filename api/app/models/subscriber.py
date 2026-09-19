import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Subscriber(Base):
    __tablename__ = "subscriber"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(30), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="subscriber")
    delivery_attempts: Mapped[list["DeliveryAttempt"]] = relationship(back_populates="subscriber")
    dead_letters: Mapped[list["DeadLetter"]] = relationship(back_populates="subscriber")

    def __repr__(self):
        return (
            f"Subscriber("
            f"id={self.id!r}, "
            f"name={self.name!r}, "
            f"endpoint_url={self.endpoint_url!r}, "
            f"status={self.status!r}"
            f")"
        )

