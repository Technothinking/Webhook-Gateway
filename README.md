# Webhook Gateway

A reliable webhook delivery platform built with FastAPI, PostgreSQL,
Redis, Next.js, and background workers.

## Architecture

- FastAPI — API and webhook ingestion
- PostgreSQL — persistent application data
- Redis — queues, caching, and coordination
- Workers — webhook delivery and retry processing
- Next.js — management dashboard
- Demo Subscriber — local webhook receiver for testing

## Development

Start the stack:

```bash
docker compose up --build
```

**API:**

http://localhost:8000

**Dashboard:**

http://localhost:3000

**API documentation:**

http://localhost:8000/docs


## Notes

- Scaling workers means --scale worker=N with no code change. Redis delivers each stream entry to exactly one consumer in the group.
- Each worker also runs the health-check and reclaim loops. Health probes are de-duplicated by a short Redis lock, and XAUTOCLAIM is safe to run from several consumers.
- The retry scheduler is intentionally a single instance. Making it scale would need row claiming with SELECT ... FOR UPDATE SKIP LOCKED, a good “what I’d change at 10x” item.
- Delivery is still at-least-once. The fan-out guard and unique constraint remove duplicates in normal operation, but a crash between the HTTP call and the DB commit can still redeliver. Receivers should dedupe, which is why this check is measured at the receiver.
