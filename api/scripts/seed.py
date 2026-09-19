from app.db.session import SessionLocal
from app.models.subscriber import Subscriber
from app.models.subscription import Subscription


def seed_data():
    session = SessionLocal()

    try:
        subscriber_1 = Subscriber(
            name="Order Service",
            endpoint_url="https://example.com/webhooks/orders",
            secret="demo-secret-orders",
            status="active",
        )

        subscriber_2 = Subscriber(
            name="Analytics Service",
            endpoint_url="https://example.com/webhooks/analytics",
            secret="demo-secret-analytics",
            status="active",
        )

        subscriber_3 = Subscriber(
            name="Notification Service",
            endpoint_url="https://example.com/webhooks/notifications",
            secret="demo-secret-notifications",
            status="active",
        )

        session.add_all([
            subscriber_1,
            subscriber_2,
            subscriber_3
        ])

        subscription_1 = Subscription(
            subscriber=subscriber_1,
            event_type="order.created",
            is_active=True,
        )

        subscription_2 = Subscription(
            subscriber=subscriber_1,
            event_type="payment.completed",
            is_active=True,
        )

        subscription_3 = Subscription(
            subscriber=subscriber_2,
            event_type="order.created",
            is_active=True,
        )

        subscription_4 = Subscription(
            subscriber=subscriber_3,
            event_type="user.created",
            is_active=True,
        )

        session.add_all([
            subscription_1,
            subscription_2,
            subscription_3,
            subscription_4
        ])

        session.commit()

        print("Seed data inserted successfully.")

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


if __name__ == "__main__":
    seed_data()