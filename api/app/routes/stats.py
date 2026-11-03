from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dead_letter import DeadLetter
from app.models.delivery_attempt import DeliveryAttempt as A
from app.models.event import Event
from app.models.subscriber import Subscriber

router = APIRouter(prefix="/stats", tags=["Stats"])

# attempt_number > 0 = real HTTP attempts. Replays (<=0) and the deferred
# placeholder (-1) are excluded, and superseded 'retry_queued' rows still
# count as the failures they were.
REAL = A.attempt_number > 0


@router.get("")
def overall_stats(db: Session = Depends(get_db)):
    total_events = db.scalar(select(func.count(Event.id)))
    attempts = db.scalar(select(func.count(A.id)).where(REAL))
    successes = db.scalar(
        select(func.count(A.id)).where(REAL, A.status == "success")
    )
    p50, p99 = db.execute(
        select(
            func.percentile_cont(0.5).within_group(A.latency_ms),
            func.percentile_cont(0.99).within_group(A.latency_ms),
        ).where(REAL, A.latency_ms.is_not(None))
    ).one()

    return {
        "total_events": total_events,
        "total_attempts": attempts,
        "success_rate": (successes / attempts) if attempts else None,
        "p50_latency_ms": round(p50) if p50 is not None else None,
        "p99_latency_ms": round(p99) if p99 is not None else None,
        "dead_letters": db.scalar(select(func.count(DeadLetter.id))),
    }


@router.get("/subscribers")
def subscriber_stats(
    window_minutes: int = Query(default=60, ge=1, le=1440),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

    rows = db.execute(
        select(
            Subscriber,
            func.count(A.id).label("total"),
            func.count(A.id).filter(A.status == "success").label("ok"),
        )
        .outerjoin(
            A,
            and_(A.subscriber_id == Subscriber.id, REAL, A.attempted_at >= since),
        )
        .group_by(Subscriber.id)
        .order_by(Subscriber.created_at.desc())
    ).all()

    return [
        {
            "id": s.id,
            "name": s.name,
            "endpoint_url": s.endpoint_url,
            "status": s.status,
            "attempts": total,
            "success_rate": (ok / total) if total else None,
        }
        for s, total, ok in rows
    ]