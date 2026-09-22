import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.subscriber import Subscriber
from app.schemas.subscriber import SubscriberCreate, SubscriberCreateResponse

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.subscription import Subscription
from app.schemas.subscription import SubscriptionCreate, SubscriptionResponse

router = APIRouter(
    prefix="/subscribers",
    tags=["Subscribers"],
)


@router.post(
    "/",
    response_model=SubscriberCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_subscriber(
    subscriber_data: SubscriberCreate,
    db: Session = Depends(get_db),
):
    secret = secrets.token_urlsafe(32)

    subscriber = Subscriber(
        name=subscriber_data.name,
        endpoint_url=str(subscriber_data.endpoint_url),
        secret=secret,
        status="active",
    )

    db.add(subscriber)
    db.commit()
    db.refresh(subscriber)

    return SubscriberCreateResponse(
        id=subscriber.id,
        name=subscriber.name,
        endpoint_url=subscriber.endpoint_url,
        status=subscriber.status,
        created_at=subscriber.created_at,
        secret=secret,
    )

@router.post(
    "/{subscriber_id}/subscriptions",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_subscription(
    subscriber_id: UUID,
    subscription_data: SubscriptionCreate,
    db: Session = Depends(get_db),
):
    subscriber = (
        db.query(Subscriber)
        .filter(Subscriber.id == subscriber_id)
        .first()
    )

    if subscriber is None:
        raise HTTPException(
            status_code=404,
            detail="Subscriber not found",
        )

    subscription = Subscription(
        subscriber_id=subscriber_id,
        event_type=subscription_data.event_type,
        is_active=True,
    )

    db.add(subscription)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Subscriber is already subscribed to this event type",
        )

    db.refresh(subscription)

    return subscription