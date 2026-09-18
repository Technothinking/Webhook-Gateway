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


