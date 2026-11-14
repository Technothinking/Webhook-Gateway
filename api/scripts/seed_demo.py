"""
Seed the live instance with the two demo subscribers. Safe to run repeatedly.

    docker compose -f docker-compose.prod.yml --env-file .env.prod \
        exec api python scripts/seed_demo.py

Do not run scripts/seed.py in production: it points at example.com URLs that
return errors and would trip circuit breakers.
"""
import os

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.subscriber import Subscriber
from app.models.subscription import Subscription

EVENT_TYPES = ["order.created", "payment.completed"]

DEMOS = [
    ("Flaky Service (demo)", "http://demo-subscriber:8001/webhook", os.environ["WEBHOOK_SECRET"]),
    ("Healthy Service (demo)", "http://demo-subscriber-2:8001/webhook", os.environ["WEBHOOK_SECRET_2"]),
]


def main():
    with SessionLocal() as db:
        for name, url, secret in DEMOS:
            sub = db.scalar(select(Subscriber).where(Subscriber.name == name))
            if sub is None:
                sub = Subscriber(name=name, endpoint_url=url, secret=secret, status="active")
                db.add(sub)
                db.flush()
            else:
                sub.endpoint_url, sub.secret, sub.status = url, secret, "active"

            have = set(db.scalars(
                select(Subscription.event_type).where(Subscription.subscriber_id == sub.id)
            ).all())
            for event_type in EVENT_TYPES:
                if event_type not in have:
                    db.add(Subscription(subscriber_id=sub.id, event_type=event_type, is_active=True))

            print(f"ready: {name} -> {url}")
        db.commit()


if __name__ == "__main__":
    main()