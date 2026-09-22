import secrets

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.subscriber import Subscriber
from app.schemas.subscriber import SubscriberCreate, SubscriberCreateResponse


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