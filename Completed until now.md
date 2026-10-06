# Webhook Reliability Gateway — Current Implementation Status

## Project Overview

The **Webhook Reliability Gateway** is a self-hostable, at-least-once webhook delivery system.

The goal is to reliably accept events from producers and deliver them to subscribed external services while handling:

* Idempotency
* Reliable persistence
* Retries
* Failure handling
* Circuit breaking
* Delivery tracking
* Dead-letter handling
* Observability/live dashboard

### Tech Stack

* **Backend:** FastAPI
* **Database:** PostgreSQL
* **ORM:** SQLAlchemy 2.x
* **Migrations:** Alembic
* **Message/queue layer:** Redis Streams
* **Workers:** Celery/asyncio workers
* **Frontend:** Next.js
* **Infrastructure:** Docker Compose

The project follows a **correctness-first** approach:

> First make one event reliably persist → then reliably deliver it to one subscriber → then add retries → then add scale/concurrency → then add observability/polish.

---

# Phase 0 — Setup

## Completed

The initial project infrastructure was created with Docker Compose.

The intended structure is:

```text
webhook-gateway/
├── api/                 # FastAPI backend
├── workers/             # delivery workers
├── dashboard/           # Next.js frontend
├── demo-subscriber/     # dummy webhook receiver
├── docker-compose.yml
└── README.md
```

Docker Compose is configured around:

* PostgreSQL
* Redis
* FastAPI API
* Next.js dashboard

The initial FastAPI and Next.js applications were successfully booted.

Alembic was also configured for database migrations.

---

# Week 1 — Day 1–2: Schema + Models

## Status: COMPLETED ✅

All five database models have been implemented using **SQLAlchemy 2.x declarative models**.

The database layer currently contains:

```text
api/
├── app/
│   ├── db/
│   │   ├── base.py
│   │   ├── database.py
│   │   └── session.py
│   │
│   └── models/
│       ├── __init__.py
│       ├── subscriber.py
│       ├── subscription.py
│       ├── event.py
│       ├── delivery_attempt.py
│       └── dead_letter.py
│
└── scripts/
    └── seed.py
```

---

## Database Base

SQLAlchemy uses a declarative base:

```python
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

All models inherit from this `Base`.

Therefore:

```text
Model
  ↓
Base.metadata
  ↓
Alembic
  ↓
PostgreSQL schema
```

---

# Database Connection

`database.py` creates the SQLAlchemy engine using the `DATABASE_URL` environment variable.

The project uses PostgreSQL through SQLAlchemy/psycopg2.

`session.py` defines a `SessionLocal` session factory using SQLAlchemy's `sessionmaker`.

Conceptually:

```text
DATABASE_URL
     ↓
SQLAlchemy Engine
     ↓
SessionLocal
     ↓
Database Sessions
```

The database URL currently uses the Docker Compose PostgreSQL service hostname (`postgres`) when the application is running inside Docker.

Important Docker detail discovered during implementation:

* `postgres` resolves from containers on the Docker network.
* It does not resolve when running database-dependent Python scripts directly from Windows.
* Therefore Alembic and the seed script are run inside the backend container when using the Docker-oriented `DATABASE_URL`.

---

# Model 1 — Subscriber

Represents an external system that receives webhooks.

Important fields:

```text
id
name
endpoint_url
secret
status
created_at
```

Technical details:

* `id` → PostgreSQL UUID primary key
* `endpoint_url` → webhook destination
* `secret` → secret used later for webhook authentication/signing
* `status` → current subscriber state
* `created_at` → timezone-aware UTC timestamp

Relationships:

```text
Subscriber
 ├── Subscription(s)
 ├── DeliveryAttempt(s)
 └── DeadLetter(s)
```

---

# Model 2 — Subscription

Represents which event types a subscriber wants to receive.

Fields:

```text
id
subscriber_id
event_type
is_active
```

Relationship:

```text
Subscription ──→ Subscriber
```

There is also a composite unique constraint:

```text
(subscriber_id, event_type)
```

Constraint name:

```text
uq_subscription_subscriber_event_type
```

This prevents the same subscriber from being subscribed to the same event type more than once.

Example:

```text
Subscriber A → order.created
Subscriber A → order.created   ❌ duplicate
```

---

# Model 3 — Event

Represents an incoming webhook event accepted by the gateway.

Fields:

```text
id
idempotency_key
event_type
payload
created_at
status
```

Important technical details:

### `id`

PostgreSQL UUID primary key.

### `idempotency_key`

Has a database-level UNIQUE constraint.

This is one of the most important correctness mechanisms in the project.

It prevents the same producer event from being stored multiple times when a producer retries the request.

```text
Incoming event
      ↓
idempotency_key
      ↓
Database UNIQUE constraint
      ↓
Duplicate rejected/detected
```

### `payload`

Uses PostgreSQL `JSONB`.

This allows arbitrary structured webhook data to be stored efficiently.

### `status`

Currently represented as a string.

The actual event lifecycle/state machine will be defined when the ingest API is implemented.

---

# Model 4 — DeliveryAttempt

Represents an individual attempt to deliver an event to a subscriber.

Fields:

```text
id
event_id
subscriber_id
attempt_number
status
http_status_code
response_body
latency_ms
attempted_at
next_retry_at
```

Relationships:

```text
DeliveryAttempt
 ├── Event
 └── Subscriber
```

Important design decisions:

### Nullable HTTP information

These fields are nullable:

```text
http_status_code
response_body
latency_ms
```

because a delivery attempt can fail before an HTTP response is received.

For example:

```text
Gateway
   ↓
HTTP request
   X
Connection timeout
```

There may be no HTTP status code or response body.

### Retry information

`next_retry_at` is nullable because a successful attempt does not necessarily have a next retry.

### Composite unique constraint

The following combination is unique:

```text
(event_id, subscriber_id, attempt_number)
```

Constraint name:

```text
uq_delivery_attempt_event_subscriber_number
```

This prevents duplicate attempt numbers for the same event/subscriber.

Example:

```text
Event A + Subscriber X + Attempt 1
Event A + Subscriber X + Attempt 2
Event A + Subscriber X + Attempt 3
```

but not:

```text
Event A + Subscriber X + Attempt 3
Event A + Subscriber X + Attempt 3   ❌
```

---

# Model 5 — DeadLetter

Represents an event/subscriber delivery that has permanently failed and has been moved to the dead-letter state.

Fields:

```text
id
event_id
subscriber_id
failed_reason
moved_at
```

Relationships:

```text
DeadLetter
 ├── Event
 └── Subscriber
```

`failed_reason` stores why the delivery was moved to the dead-letter state.

The actual logic deciding **when** an event becomes a dead letter has not been implemented yet. That belongs to the delivery/retry system later.

---

# Model Relationships

The current database relationship structure is approximately:

```text
                 ┌───────────────┐
                 │   Subscriber  │
                 └───────┬───────┘
                         │
             ┌───────────┼────────────┐
             │           │            │
             ▼           ▼            ▼
       Subscription  DeliveryAttempt  DeadLetter
                         ▲            ▲
                         │            │
                         └──── Event ─┘
```

More precisely:

```text
Subscriber
    │
    ├──< Subscription
    │
    ├──< DeliveryAttempt
    │
    └──< DeadLetter

Event
    │
    ├──< DeliveryAttempt
    │
    └──< DeadLetter
```

---

# Alembic

Alembic has been successfully configured to use:

```python
target_metadata = Base.metadata
```

All five models are imported through:

```text
app/models/__init__.py
```

so Alembic can discover all model tables during autogeneration.

The initial migration has been generated:

```text
331ca01dd5b2
```

Migration message:

```text
create initial webhook tables
```

The migration creates:

```text
event
subscriber
dead_letter
delivery_attempt
subscription
```

It also creates:

* Primary keys
* Foreign keys
* `event.idempotency_key` UNIQUE constraint
* Subscription composite UNIQUE constraint
* DeliveryAttempt composite UNIQUE constraint

---

# PostgreSQL Verification

The migration was successfully applied using:

```bash
alembic upgrade head
```

The PostgreSQL database was then inspected through `psql`.

All five tables are confirmed to exist:

```text
dead_letter
delivery_attempt
event
subscriber
subscription
```

Therefore the following pipeline has been verified:

```text
SQLAlchemy Models
       ↓
Base.metadata
       ↓
Alembic Autogeneration
       ↓
Migration
       ↓
PostgreSQL
```

---

# Demo Seed Data

A seed script has also been implemented:

```text
scripts/seed.py
```

It uses the SQLAlchemy ORM and existing `SessionLocal`.

The script creates three demo subscribers:

```text
Order Service
Analytics Service
Notification Service
```

and four subscriptions:

```text
Order Service
 ├── order.created
 └── payment.completed

Analytics Service
 └── order.created

Notification Service
 └── user.created
```

The seed data was successfully inserted into PostgreSQL.

The following tables are intentionally still empty:

```text
event
delivery_attempt
dead_letter
```

because those should be populated by the actual application workflow rather than the initial seed process.

---

# Current Completion Status

```text
Phase 0 — Setup
    ✅ Docker Compose
    ✅ PostgreSQL
    ✅ Redis
    ✅ FastAPI
    ✅ Next.js
    ✅ Alembic

Week 1 — Day 1–2
    ✅ SQLAlchemy database base
    ✅ Database engine
    ✅ Session factory
    ✅ Subscriber model
    ✅ Subscription model
    ✅ Event model
    ✅ DeliveryAttempt model
    ✅ DeadLetter model
    ✅ Relationships
    ✅ Unique constraints
    ✅ Alembic migration
    ✅ Migration applied to PostgreSQL
    ✅ Database schema verified
    ✅ Demo seed data
```

## Current position

**The project is currently finished through Week 1 — Day 1–2: Schema + Models.**

The next planned step is:

# Week 1 — Next: Ingest API + Outbox Pattern + Idempotency

The next milestone is to implement an API that can:

```text
Producer
   │
   │ POST event
   ▼
FastAPI Ingest API
   │
   ├── Validate request
   ├── Check idempotency
   ├── Store Event
   └── Commit transaction
            │
            ▼
       PostgreSQL
```

At this stage **there should still be no actual webhook delivery**.

The immediate objective is:

> A producer can POST an event, the gateway stores it durably, duplicate requests with the same idempotency key are handled correctly, and the stored event can be queried.

After that, the project will introduce the outbox/queue mechanism and eventually the delivery worker.

---

## Important continuation point

The person continuing this project should **not redesign the existing database schema unless a concrete requirement appears**. The current schema and migration have already been successfully tested against PostgreSQL.

The next work should start from:

**Week 1 → Ingest API + Outbox Pattern + Idempotency**, while preserving the correctness-first approach.

# Week 1 — Day 3: Event Ingest API

## Status: COMPLETED ✅

The event ingestion API has been implemented and tested.

The system can now:

* Accept webhook events through `POST /events`
* Validate incoming event data using Pydantic
* Persist events in PostgreSQL
* Automatically assign the initial event status
* Prevent duplicate events using idempotency keys
* Handle concurrent duplicate requests safely
* Retrieve an individual event using `GET /events/{id}`
* Retrieve multiple events using `GET /events`
* Filter events by `event_type`
* Filter events by `status`
* Paginate event results
* Return events in deterministic newest-first order

No event delivery has been implemented yet. Events are only persisted and queryable at this stage.

---

# Event Request Schema

A dedicated Pydantic request schema was created for event creation:

```python
class EventCreate(BaseModel):
    event_type: str
    payload: dict
    idempotency_key: str
```

The client provides only the information required to create an event.

Server-controlled fields such as:

* `id`
* `created_at`
* `status`

are not accepted from the client.

---

# Event Response Schema

A separate response schema was added:

```python
class EventResponse(BaseModel):
    id: UUID
    idempotency_key: str
    event_type: str
    payload: dict
    created_at: datetime
    status: str
```

The response schema uses SQLAlchemy ORM attribute support so database model instances can be returned directly through FastAPI.

Conceptually:

```text
Client Request
      ↓
EventCreate
      ↓
FastAPI Route
      ↓
SQLAlchemy Event
      ↓
PostgreSQL
      ↓
EventResponse
      ↓
Client
```

---

# POST /events

The event ingestion endpoint accepts:

```text
POST /events
```

with a request body:

```json
{
  "event_type": "order.created",
  "payload": {
    "order_id": 123,
    "amount": 499
  },
  "idempotency_key": "test-001"
}
```

The event is converted from the Pydantic request model into the SQLAlchemy `Event` model and persisted using the database session.

The basic persistence flow is:

```text
POST /events
      ↓
Validate request
      ↓
Check idempotency key
      ↓
Create Event
      ↓
db.add()
      ↓
db.commit()
      ↓
db.refresh()
      ↓
Return EventResponse
```

A newly created event returns:

```text
201 Created
```

---

# Event Status Default

The `Event.status` column is `NOT NULL`.

During initial testing, an insertion failed because the API did not explicitly provide a status:

```text
NotNullViolation:
null value in column "status" of relation "event"
```

The SQLAlchemy model was therefore updated with an initial default status:

```python
status = mapped_column(
    String,
    nullable=False,
    default="pending"
)
```

Therefore newly created events automatically begin with:

```text
status = pending
```

The client does not control this value.

---

# Idempotency

Idempotency prevents the same logical event from being inserted multiple times when a producer retries the same request.

The `idempotency_key` column already has a database-level `UNIQUE` constraint.

The request flow is:

```text
POST /events
      ↓
Search idempotency_key
      ↓
 ┌────┴─────┐
 │          │
Exists     New
 │          │
 ▼          ▼
Return     Create
existing   event
event       │
 │          ▼
 ▼        Commit
200         │
            ▼
           201
```

For example, submitting:

```json
{
  "event_type": "order.created",
  "payload": {
    "order_id": 123
  },
  "idempotency_key": "test-001"
}
```

multiple times does not create multiple events.

The first request creates the event:

```text
201 Created
```

A subsequent request with the same idempotency key returns the existing event:

```text
200 OK
```

The existing event retains the same event ID.

---

# Concurrency-Safe Idempotency

A simple application-level lookup is not sufficient when multiple identical requests arrive simultaneously.

Potential race condition:

```text
Request A                  Request B
    │                          │
    ├─ check key ──┐           │
    │              │           ├─ check key
    │           not found      │
    │              │           │
    │              │        not found
    │              │           │
    ├── INSERT ────────────────┤
    │                          │
    │                       INSERT
    │                          │
    ▼                          ▼
success                 UNIQUE constraint
                            violation
```

The database `UNIQUE` constraint on `idempotency_key` acts as the final correctness guarantee.

The implementation catches `IntegrityError`:

```text
IntegrityError
      ↓
db.rollback()
      ↓
Fetch event using idempotency_key
      ↓
Return existing event
      ↓
200 OK
```

The rollback is necessary because after a database integrity error, the SQLAlchemy transaction must be rolled back before another query can be executed using the same session.

Therefore, concurrent duplicate requests resolve to the same persisted event instead of creating duplicate rows or returning an unexpected `500` error.

---

# GET /events/{id}

An individual event can be retrieved using:

```text
GET /events/{id}
```

The endpoint searches PostgreSQL using the event UUID.

Flow:

```text
GET /events/{id}
      ↓
Query Event by ID
      ↓
 ┌────┴─────┐
 │          │
Found     Not Found
 │          │
 ▼          ▼
Return     404
event
```

A valid event returns:

```text
200 OK
```

If the event does not exist:

```text
404 Not Found
```

FastAPI also validates the UUID path parameter.

---

# GET /events

The event listing endpoint supports:

```text
GET /events/
```

It returns a list of events using the `EventResponse` schema.

---

## Event Type Filtering

Events can be filtered using:

```text
GET /events/?event_type=order.created
```

This returns only events whose:

```text
event_type = order.created
```

---

## Status Filtering

Events can also be filtered using:

```text
GET /events/?status=pending
```

This returns events whose:

```text
status = pending
```

---

## Combined Filtering

Both filters can be used together:

```text
GET /events/?event_type=order.created&status=pending
```

The query applies both conditions.

Conceptually:

```text
event_type = order.created
        AND
status = pending
```

---

# Pagination

The `GET /events` endpoint supports offset-based pagination.

Parameters:

```text
skip
limit
```

Example:

```text
GET /events/?skip=0&limit=10
```

The next page can be retrieved using:

```text
GET /events/?skip=10&limit=10
```

Validation was added so:

* `skip >= 0`
* `limit >= 1`
* `limit <= 100`

This prevents invalid or excessively large requests.

---

# Deterministic Ordering

Events are ordered by:

```text
created_at DESC
```

Therefore the newest events appear first.

Conceptually:

```text
Newest Event
     ↓
   Event
     ↓
   Event
     ↓
   Event
     ↓
Oldest Event
```

Deterministic ordering is important when using offset pagination so that successive requests have a predictable ordering.

---

# API Endpoints Completed

The Week 1 Day 3 event API now contains:

```text
POST /events/
    ├── Request validation
    ├── Event persistence
    ├── Status initialization
    ├── Idempotency
    └── Concurrency-safe duplicate handling

GET /events/{id}
    └── Retrieve a single event

GET /events/
    ├── Pagination
    ├── event_type filtering
    ├── status filtering
    └── created_at DESC ordering
```

---

# Testing Completed

The following flows were manually tested successfully:

* Creating a new event
* Verifying the event in PostgreSQL
* Submitting the same idempotency key again
* Confirming no duplicate event was created
* Confirming the existing event ID is returned
* Testing concurrent duplicate handling
* Retrieving an event by ID
* Requesting a non-existent event
* Listing events
* Filtering by `event_type`
* Filtering by `status`
* Combining both filters
* Testing pagination with `skip` and `limit`
* Verifying newest-first ordering

---

# Day 3 Checkpoint

```text
Week 1 — Day 3 ✅

Event Ingestion
      │
      ├── POST /events
      │
      ├── PostgreSQL persistence
      │
      ├── Idempotency
      │
      └── Concurrency-safe deduplication
      │
      ▼
Event Querying
      │
      ├── GET /events/{id}
      │
      ├── GET /events
      ├── Filtering
      └── Pagination
```

The system can now **accept an event, persist it reliably, deduplicate repeated submissions, and query it back**.

No event is delivered to subscribers yet. Delivery begins in the subsequent phases.

# Webhook Reliability Gateway — Week 1 Day 4

# Week 1 — Day 4: Subscriber Management

## Status: COMPLETED ✅

Subscriber management has been implemented and tested.

The system can now:

* Create webhook subscribers
* Generate a secure HMAC secret for each subscriber
* Return the generated secret only when the subscriber is created
* Prevent the secret from being exposed through normal subscriber responses
* Subscribe a subscriber to specific event types
* Prevent duplicate subscriptions
* List all registered subscribers
* Pause a subscriber
* Resume a subscriber

No actual webhook delivery has been implemented yet. Subscriber management only prepares the configuration that will later be used by the delivery system.

---

# Subscriber Schemas

Two dedicated schema files were created/updated:

```text
api/app/schemas/
├── subscriber.py
└── subscription.py
```

---

## SubscriberCreate

The request schema for creating a subscriber is:

```python
class SubscriberCreate(BaseModel):
    name: str
    endpoint_url: HttpUrl
```

The client provides:

```text
name
endpoint_url
```

Server-controlled values such as:

```text
id
secret
status
created_at
```

are not accepted from the client.

---

## SubscriberResponse

The normal subscriber response schema is:

```python
class SubscriberResponse(BaseModel):
    id: UUID
    name: str
    endpoint_url: str
    status: str
    created_at: datetime
```

Notice that the `secret` field is intentionally not included.

This prevents the secret from being exposed when subscribers are listed or updated.

---

## SubscriberCreateResponse

A separate response schema was created for subscriber creation:

```python
class SubscriberCreateResponse(SubscriberResponse):
    secret: str
```

This allows the secret to be returned when the subscriber is first created while keeping it hidden from all normal subscriber responses.

Conceptually:

```text
POST /subscribers
       ↓
Create subscriber
       ↓
Generate secret
       ↓
Return secret once
       ↓
Future responses
       ↓
Secret not included
```

---

## SubscriberUpdate

The update schema is:

```python
class SubscriberUpdate(BaseModel):
    status: Literal["active", "paused"]
```

Therefore the subscriber status can only be changed to:

```text
active
paused
```

Invalid status values are rejected by Pydantic validation.

---

# Subscription Schemas

The subscription schemas are defined in:

```text
api/app/schemas/subscription.py
```

---

## SubscriptionCreate

The request schema is:

```python
class SubscriptionCreate(BaseModel):
    event_type: str
```

The client only needs to provide the event type it wants to receive.

---

## SubscriptionResponse

The response schema is:

```python
class SubscriptionResponse(BaseModel):
    id: UUID
    subscriber_id: UUID
    event_type: str
    is_active: bool
```

This represents the relationship between a subscriber and an event type.

---

# Subscriber Routes

A new router was created in:

```text
api/app/routes/subscribers.py
```

The router uses:

```python
router = APIRouter(
    prefix="/subscribers",
    tags=["Subscribers"]
)
```

Therefore all subscriber management endpoints are grouped under:

```text
/subscribers
```

---

# POST /subscribers

The endpoint:

```text
POST /subscribers/
```

creates a new webhook subscriber.

The request contains:

```json
{
  "name": "Payment Service",
  "endpoint_url": "https://example.com/webhook"
}
```

---

## Secret Generation

When a subscriber is created, the gateway generates a secure secret using Python's `secrets` module:

```python
secret = secrets.token_urlsafe(32)
```

This secret is stored with the subscriber and will later be used for webhook authentication/signing.

The important security behavior is:

```text
Subscriber Creation
        ↓
Generate secret
        ↓
Store secret
        ↓
Return secret in creation response
        ↓
Do not expose secret again
```

The secret is therefore treated as a value that should be captured by the subscriber owner when the subscriber is created.

---

## Subscriber Creation Flow

The complete flow is:

```text
POST /subscribers/
        ↓
Validate request
        ↓
Generate secure secret
        ↓
Create Subscriber
        ↓
Set status = active
        ↓
Save to PostgreSQL
        ↓
Refresh database object
        ↓
Return SubscriberCreateResponse
```

A newly created subscriber starts with:

```text
status = active
```

---

# POST /subscribers/{id}/subscriptions

The endpoint:

```text
POST /subscribers/{subscriber_id}/subscriptions
```

attaches an event type to a subscriber.

Example request:

```json
{
  "event_type": "order.created"
}
```

The resulting relationship is:

```text
Subscriber
    │
    └── Subscription
            │
            └── order.created
```

---

## Subscriber Existence Check

Before creating the subscription, the API verifies that the subscriber exists.

Flow:

```text
POST subscription
        ↓
Find subscriber by ID
        ↓
 ┌──────┴──────┐
 │             │
Found       Not Found
 │             │
 ▼             ▼
Create        404
subscription
```

If the subscriber does not exist:

```text
404 Not Found
```

is returned.

---

## Creating the Subscription

A new `Subscription` is created with:

```text
subscriber_id
event_type
is_active = true
```

The subscription is then committed to PostgreSQL.

A successful creation returns the subscription information using `SubscriptionResponse`.

---

# Duplicate Subscription Handling

The database already contains a composite unique constraint:

```text
(subscriber_id, event_type)
```

with the constraint name:

```text
uq_subscription_subscriber_event_type
```

This prevents the same subscriber from being subscribed to the same event type more than once.

Example:

```text
Subscriber A → order.created
Subscriber A → order.created
                    ❌
```

The API also handles the resulting database `IntegrityError`.

Flow:

```text
Create subscription
        ↓
Database UNIQUE constraint
        ↓
Duplicate?
   ┌────┴─────┐
   │          │
  No         Yes
   │          │
   ▼          ▼
Commit     IntegrityError
             ↓
          Rollback
             ↓
           409
```

The duplicate subscription response is:

```text
409 Conflict
```

with the message:

```text
Subscriber is already subscribed to this event type
```

The transaction is rolled back before returning the error.

---

# GET /subscribers

The endpoint:

```text
GET /subscribers/
```

returns all registered subscribers.

The subscribers are ordered by:

```text
created_at DESC
```

so the newest subscribers are returned first.

The response uses:

```python
list[SubscriberResponse]
```

Importantly, `SubscriberResponse` does not contain the secret.

Therefore:

```text
GET /subscribers/
        ↓
SubscriberResponse
        ↓
No secret exposed
```

This ensures that the secret generated during subscriber creation is not returned through the listing endpoint.

---

# PATCH /subscribers/{id}

The endpoint:

```text
PATCH /subscribers/{subscriber_id}
```

is used to change a subscriber's status.

The request body is:

```json
{
  "status": "paused"
}
```

or:

```json
{
  "status": "active"
}
```

---

## Pause Subscriber

A subscriber can be paused by sending:

```json
{
  "status": "paused"
}
```

The database value becomes:

```text
status = paused
```

This provides the configuration needed for the later delivery system to stop treating the subscriber as active.

---

## Resume Subscriber

A paused subscriber can be resumed using:

```json
{
  "status": "active"
}
```

The database value becomes:

```text
status = active
```

---

## Subscriber Update Flow

```text
PATCH /subscribers/{id}
        ↓
Find subscriber
        ↓
 ┌──────┴──────┐
 │             │
Found       Not Found
 │             │
 ▼             ▼
Validate       404
status
 │
 ▼
Update status
 │
 ▼
Commit
 │
 ▼
Refresh
 │
 ▼
Return SubscriberResponse
```

If the subscriber does not exist:

```text
404 Not Found
```

is returned.

Only the following values are accepted:

```text
active
paused
```

---

# Secret Exposure Design

One of the important security decisions implemented on Day 4 is separating the creation response from the normal subscriber response.

The schemas intentionally behave as follows:

```text
POST /subscribers/
        │
        ▼
SubscriberCreateResponse
        │
        ├── id
        ├── name
        ├── endpoint_url
        ├── status
        ├── created_at
        └── secret   ← returned here
```

But:

```text
GET /subscribers/
        │
        ▼
SubscriberResponse
        │
        ├── id
        ├── name
        ├── endpoint_url
        ├── status
        └── created_at
              └── no secret
```

Similarly, the PATCH response does not expose the secret.

This establishes the intended behavior:

> The subscriber secret is returned during creation but is never included in normal subscriber responses.

---

# Updated API Structure

After Day 4, the API now contains event and subscriber routes:

```text
api/app/
├── db/
├── models/
├── routes/
│   ├── events.py
│   └── subscribers.py
├── schemas/
│   ├── event.py
│   ├── subscriber.py
│   └── subscription.py
└── main.py
```

The subscriber router is registered in `main.py`:

```python
from app.routes.subscribers import router as subscriber_router

app.include_router(subscriber_router)
```

Therefore FastAPI now exposes both:

```text
/events
/subscribers
```

---

# Day 4 API Summary

The subscriber management API now consists of:

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/subscribers/` | Create a subscriber and return its generated secret |
| POST | `/subscribers/{subscriber_id}/subscriptions` | Subscribe a subscriber to an event type |
| GET | `/subscribers/` | List all subscribers without exposing secrets |
| PATCH | `/subscribers/{subscriber_id}` | Pause or resume a subscriber |

---

# Day 4 Testing

All Day 4 endpoints were manually tested successfully.

## Subscriber Creation

Tested:

```text
POST /subscribers/
```

Result:

```text
Subscriber created successfully
```

The generated secret was returned in the creation response.

---

## Subscription Creation

Tested:

```text
POST /subscribers/{subscriber_id}/subscriptions
```

Result:

```text
Subscription created successfully
```

The subscription correctly connects the subscriber to the requested event type.

---

## Duplicate Subscription

The same subscriber was subscribed to the same event type again.

The API correctly detected the duplicate and returned the expected conflict behavior:

```text
409 Conflict
```

---

## Subscriber Listing

Tested:

```text
GET /subscribers/
```

Result:

```text
Subscribers returned successfully
```

The secret was not included in the response.

---

## Pause / Resume

Tested:

```text
PATCH /subscribers/{subscriber_id}
```

with:

```json
{
  "status": "paused"
}
```

and then:

```json
{
  "status": "active"
}
```

Both operations worked correctly.

---

# Current Completion Status

```text
Phase 0 — Setup
    ✅ Docker Compose
    ✅ PostgreSQL
    ✅ Redis
    ✅ FastAPI
    ✅ Next.js
    ✅ Alembic

Week 1 — Day 1–2
    ✅ SQLAlchemy database base
    ✅ Database engine
    ✅ Session factory
    ✅ Subscriber model
    ✅ Subscription model
    ✅ Event model
    ✅ DeliveryAttempt model
    ✅ DeadLetter model
    ✅ Relationships
    ✅ Unique constraints
    ✅ Alembic migration
    ✅ Migration applied to PostgreSQL
    ✅ Database schema verified
    ✅ Demo seed data

Week 1 — Day 3
    ✅ POST /events
    ✅ Event validation
    ✅ Event persistence
    ✅ Default pending status
    ✅ Idempotency handling
    ✅ Database UNIQUE constraint fallback
    ✅ Concurrency-safe duplicate handling
    ✅ GET /events/{id}
    ✅ GET /events
    ✅ Event type filtering
    ✅ Status filtering
    ✅ Pagination
    ✅ Newest-first ordering

Week 1 — Day 4
    ✅ SubscriberCreate schema
    ✅ SubscriberResponse schema
    ✅ SubscriberCreateResponse schema
    ✅ SubscriberUpdate schema
    ✅ SubscriptionCreate schema
    ✅ SubscriptionResponse schema
    ✅ POST /subscribers/
    ✅ Secure secret generation
    ✅ Secret returned only during creation
    ✅ Secret hidden from normal responses
    ✅ POST /subscribers/{id}/subscriptions
    ✅ Subscriber existence validation
    ✅ Duplicate subscription handling
    ✅ GET /subscribers/
    ✅ PATCH /subscribers/{id}
    ✅ Pause subscriber
    ✅ Resume subscriber
    ✅ Manual endpoint testing
```

---

# Current Position

**The project is currently finished through Week 1 — Day 4: Subscriber Management.**

The gateway can now:

```text
                    ┌──────────────────┐
                    │     Producer     │
                    └────────┬─────────┘
                             │
                             │ POST event
                             ▼
                    ┌──────────────────┐
                    │   Event Ingest   │
                    │      API         │
                    └────────┬─────────┘
                             │
                             ▼
                       PostgreSQL
                             │
                             │
                    ┌────────┴─────────┐
                    │                  │
                    ▼                  ▼
                Events            Subscribers
                                     │
                                     ▼
                                Subscriptions
```

At this point:

* Events can be persisted reliably.
* Duplicate events are handled using idempotency.
* Subscribers can be registered.
* Subscribers can subscribe to event types.
* Subscribers can be paused and resumed.
* Subscriber secrets are protected from normal API responses.

There is still **no actual webhook delivery**.

---

# Next Step — Week 1 Day 5

The next planned step is testing and documentation.

The Day 5 plan is:

```text
Week 1 — Day 5
    │
    ├── pytest idempotency dedup test
    ├── subscriber CRUD tests
    ├── subscription filtering logic tests
    └── docs/schema.md
```

The schema documentation will explain the important relationship:

```text
Event
  │
  └──< DeliveryAttempt
             │
             └── Subscriber
```

The immediate goal is to make sure the existing Week 1 functionality is covered by automated tests before moving into the delivery system.

---

# Important Continuation Point

The existing database schema and Day 3–4 API implementation have already been tested successfully.

The project should continue from the current implementation rather than redesigning the existing schema.

The next work should start with:

**Week 1 → Day 5: Tests + Documentation**

After Week 1 is complete, the project can move into the delivery pipeline, beginning with the Redis Streams publishing and worker flow.



# Week 1 — Day 5: Tests + Documentation

## Status: COMPLETED ✅

Week 1 Day 5 testing and documentation have been completed.

The project now has automated tests covering:

* Event idempotency
* Subscriber creation
* Subscriber listing
* Subscriber status updates
* Subscription creation
* Duplicate subscription prevention

The database schema and core relationships have also been documented in:

```text
docs/schema.md
```

No actual webhook delivery has been implemented yet. Delivery begins in Week 2.

---

# Testing Setup

A pytest-based test structure was added:

```text
tests/
├── __init__.py
├── conftest.py
├── test_events.py
├── test_subscribers.py
└── test_subscriptions.py
```

The test suite uses FastAPI's `TestClient` and overrides the application's database dependency during testing.

Tests can be executed inside the API container using:

```bash
docker compose exec api pytest -v
```

---

# Event Tests

## Root Endpoint Test

The basic FastAPI root endpoint is tested to ensure that the application is running correctly.

The test verifies:

```text
GET /
   ↓
200 OK
   ↓
Webhook Reliability Gateway API is running.
```

---

## Idempotency Test

The idempotency test submits the same event twice using the same idempotency key.

Flow:

```text
First Request
      │
      ▼
POST /events
      │
      ▼
201 Created
      │
      ▼
Event stored
```

The same request is then submitted again:

```text
Second Request
      │
      ▼
POST /events
      │
      ▼
Existing idempotency key
      │
      ▼
200 OK
```

The test verifies that:

```text
event1.id == event2.id
```

and:

```text
event1.idempotency_key == event2.idempotency_key
```

Therefore, repeated submissions do not create duplicate events.

A unique idempotency key is generated for each test execution so that repeated pytest runs do not depend on data left behind by previous test runs.

---

# Subscriber Tests

## Create Subscriber

The subscriber creation test verifies:

```text
POST /subscribers/
```

successfully creates a subscriber.

The test verifies:

* HTTP status `201`
* Subscriber name
* Endpoint URL
* Initial status is `active`
* A secret is generated
* The generated secret is not empty
* Subscriber ID is returned
* Creation timestamp is returned

Conceptually:

```text
POST /subscribers/
        │
        ▼
Create Subscriber
        │
        ├── Generate secret
        ├── status = active
        └── Store in PostgreSQL
        │
        ▼
201 Created
```

---

## List Subscribers

The subscriber listing test verifies:

```text
GET /subscribers/
```

returns a list of registered subscribers.

It also verifies that the subscriber secret is **not exposed** by the normal subscriber response.

Therefore:

```text
POST /subscribers/
        │
        └── secret returned during creation

GET /subscribers/
        │
        └── secret NOT returned
```

This preserves the intended secret-handling behavior implemented on Day 4.

---

## Update Subscriber

The subscriber update test verifies:

```text
PATCH /subscribers/{subscriber_id}
```

can change the subscriber status.

The test changes:

```text
active
   ↓
paused
```

and verifies that the returned subscriber contains:

```text
status = paused
```

---

# Subscription Tests

## Create Subscription

The subscription test creates a subscriber and then attaches an event type to it.

Example:

```text
Subscriber
    │
    └── order.created
```

The test verifies:

* HTTP status `201`
* Correct `subscriber_id`
* Correct `event_type`
* Subscription is active

The resulting relationship is:

```text
Subscriber
     │
     ▼
Subscription
     │
     └── event_type = order.created
```

---

## Duplicate Subscription Test

The database contains a unique constraint on:

```text
(subscriber_id, event_type)
```

The test verifies that attempting to create the same subscription twice is rejected.

Flow:

```text
First subscription
        │
        ▼
201 Created
```

Second subscription:

```text
Same subscriber
       +
Same event type
       │
       ▼
Duplicate detected
       │
       ▼
409 Conflict
```

The test verifies the expected error:

```text
Subscriber is already subscribed to this event type
```

This ensures that the same subscriber cannot accidentally register for the same event type multiple times.

---

# Complete Test Suite

The complete test suite currently contains:

```text
tests/test_events.py
    ├── test_root
    └── test_event_idempotency

tests/test_subscribers.py
    ├── test_create_subscriber
    ├── test_list_subscribers
    └── test_update_subscriber

tests/test_subscriptions.py
    ├── test_create_subscription
    └── test_duplicate_subscription_rejected
```

All tests were successfully executed:

```text
7 passed ✅
```

The full test suite can be executed using:

```bash
docker compose exec api pytest -v
```

---

# Schema Documentation

A dedicated schema documentation file was created:

```text
docs/schema.md
```

The documentation explains the five main database entities:

```text
Subscriber
Subscription
Event
DeliveryAttempt
DeadLetter
```

It also documents their relationships and the purpose of each table.

---

# Event → DeliveryAttempt Relationship

The core delivery relationship is:

```text
Event
  │
  └──< DeliveryAttempt
             │
             └── Subscriber
```

An `Event` represents a webhook event received by the gateway.

A `DeliveryAttempt` represents an individual attempt to deliver that event to a particular subscriber.

For example:

```text
Event: order.created
       │
       ├── Attempt 1 → Order Service
       │
       ├── Attempt 2 → Order Service
       │
       └── Attempt 3 → Order Service
```

This structure allows the system to record multiple attempts for the same event/subscriber pair when retry functionality is introduced.

---

# Subscriber → Subscription Relationship

A subscriber can have multiple subscriptions:

```text
Subscriber
    │
    ├── Subscription → order.created
    │
    ├── Subscription → payment.completed
    │
    └── Subscription → user.created
```

The `Subscription` table determines which event types a subscriber is interested in.

The database prevents duplicate subscriptions using:

```text
(subscriber_id, event_type)
```

as a unique combination.

---

# DeadLetter Relationship

A delivery that eventually reaches permanent failure handling can be represented by:

```text
Event
  │
  └──> DeadLetter
          │
          └── Subscriber
```

The `DeadLetter` table stores:

```text
event_id
subscriber_id
failed_reason
moved_at
```

The actual retry exhaustion and dead-letter processing will be implemented in later weeks.

---

# Overall Database Relationship

The overall relationship can be represented as:

```text
                         ┌──────────────┐
                         │     Event    │
                         └──────┬───────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
                    ▼                       ▼
          ┌──────────────────┐     ┌────────────────┐
          │ DeliveryAttempt  │     │   DeadLetter   │
          └────────┬─────────┘     └───────┬────────┘
                   │                       │
                   ▼                       ▼
              ┌───────────┐          ┌───────────┐
              │ Subscriber│          │ Subscriber│
              └─────┬─────┘          └───────────┘
                    │
                    ▼
              ┌─────────────┐
              │ Subscription│
              └─────────────┘
```

The schema separates the responsibilities of:

```text
Event
  → What happened

Subscriber
  → Who receives events

Subscription
  → Which event types a subscriber wants

DeliveryAttempt
  → What happened during delivery

DeadLetter
  → Which deliveries permanently failed
```

---

# Week 1 Day 5 Checkpoint

```text
Week 1 — Day 5 ✅

Automated Testing
      │
      ├── Event tests
      │     ├── Root endpoint
      │     └── Idempotency
      │
      ├── Subscriber tests
      │     ├── Create
      │     ├── List
      │     └── Update
      │
      └── Subscription tests
            ├── Create
            └── Duplicate prevention

            ↓

        7 tests passed

            ↓

Schema Documentation
      │
      └── docs/schema.md
```

---

# Week 1 Completion Status

```text
Week 1 — Day 1–2
    ✅ Database base
    ✅ Database engine
    ✅ Session factory
    ✅ Subscriber model
    ✅ Subscription model
    ✅ Event model
    ✅ DeliveryAttempt model
    ✅ DeadLetter model
    ✅ Relationships
    ✅ Unique constraints
    ✅ Alembic migration
    ✅ PostgreSQL schema
    ✅ Demo seed data

Week 1 — Day 3
    ✅ POST /events/
    ✅ Event validation
    ✅ Event persistence
    ✅ Default pending status
    ✅ Idempotency handling
    ✅ Concurrency-safe duplicate handling
    ✅ GET /events/{id}
    ✅ GET /events/
    ✅ Event type filtering
    ✅ Status filtering
    ✅ Pagination
    ✅ Newest-first ordering

Week 1 — Day 4
    ✅ Subscriber creation
    ✅ Secure secret generation
    ✅ Secret returned only during creation
    ✅ Secret hidden from normal responses
    ✅ Subscription creation
    ✅ Duplicate subscription prevention
    ✅ Subscriber listing
    ✅ Subscriber status update
    ✅ Pause subscriber
    ✅ Resume subscriber

Week 1 — Day 5
    ✅ Pytest setup
    ✅ Event idempotency test
    ✅ Subscriber CRUD tests
    ✅ Subscription tests
    ✅ Duplicate subscription test
    ✅ 7 tests passing
    ✅ docs/schema.md
```

---

# Current Completion Status

```text
Phase 0 — Setup
    ✅ Docker Compose
    ✅ PostgreSQL
    ✅ Redis
    ✅ FastAPI
    ✅ Next.js
    ✅ Alembic

Week 1 — Day 1–2
    ✅ Schema + Models

Week 1 — Day 3
    ✅ Event Ingest API

Week 1 — Day 4
    ✅ Subscriber Management

Week 1 — Day 5
    ✅ Tests + Documentation
```

## Current Position

**Week 1 is now COMPLETED ✅**

The gateway can currently:

```text
Register Subscriber
       │
       ▼
Create Subscription
       │
       ▼
Submit Event
       │
       ▼
Persist Event
       │
       ▼
Prevent Duplicate Events
       │
       ▼
Query Event
```

The project currently has **no actual webhook delivery yet**.

That is intentional and follows the implementation plan:

> End of Week 1 checkpoint: You can register a subscriber, submit an event, and query it back. Nothing is delivered yet — that's fine, that's Week 2.

---

# Next Step — Week 2

The next phase is:

```text
Week 2 — Delivery Pipeline + HMAC Signing
```

The first step will be publishing the event to a Redis Stream:

```text
POST /events
      │
      ▼
PostgreSQL
      │
      ▼
Redis Stream
      │
      ▼
Delivery Worker
      │
      ▼
Subscriber Endpoint
```

Week 2 will introduce the actual webhook delivery pipeline while preserving the correctness guarantees implemented during Week 1.

# Week 2 — Delivery Pipeline + HMAC Signing

## Week 2 — Day 1: Redis Stream Publishing

### Status: COMPLETED ✅

Week 2 Day 1 introduced Redis Streams into the event ingestion pipeline.

The goal was to ensure that every newly created event is published to a Redis Stream after it has been successfully persisted in PostgreSQL.

The resulting flow is:

```text
POST /events
      │
      ▼
PostgreSQL
      │
      │ Event persisted successfully
      ▼
Redis Stream
      │
      ▼
Delivery Worker
```

---

## Redis Configuration

Redis was already running as part of the Docker Compose infrastructure:

```yaml
redis:
  image: redis:7-alpine
  container_name: webhook-redis
  ports:
    - "6379:6379"
```

The API receives the Redis connection URL through the environment:

```env
REDIS_URL=redis://redis:6379
```

The Redis Python package was added to the API requirements.

Redis connectivity was verified successfully using:

```bash
docker compose exec redis redis-cli ping
```

which returned:

```text
PONG
```

The Redis Python package was also verified inside the API container.

---

## Redis Client

A Redis client was created at:

```text
api/app/redis/client.py
```

The client uses `redis.asyncio` so that Redis operations can be performed asynchronously from the FastAPI application.

```python
import os

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")

redis_client = redis.from_url(
    REDIS_URL,
    decode_responses=True
)
```

---

## Event Stream Publisher

A publisher was created at:

```text
api/app/redis/publisher.py
```

The Redis Stream name is:

```text
webhook_events
```

New events are published using:

```python
await redis_client.xadd(
    EVENT_STREAM,
    {
        "event_id": event_id
    }
)
```

Only the `event_id` is placed into the stream.

The complete event payload remains in PostgreSQL.

This keeps Redis responsible for **event notification/work distribution**, while PostgreSQL remains the source of truth for event data.

---

## POST /events Integration

The existing event ingestion endpoint was updated so that newly created events are published to Redis after successful database persistence.

The flow is:

```text
Create Event
     │
     ▼
db.commit()
     │
     ▼
db.refresh()
     │
     ▼
Publish event_id to Redis
     │
     ▼
Return 201 Created
```

The important part is that Redis publishing occurs only for **newly created events**.

Duplicate/idempotent requests do not publish the event again.

Therefore:

```text
New Event
    └──► PostgreSQL + Redis Stream

Duplicate Event
    └──► PostgreSQL lookup only
          └──► No duplicate Redis message
```

This preserves the idempotency behavior implemented during Week 1.

---

## Redis Stream Verification

The Redis Stream was verified using:

```bash
docker compose exec redis redis-cli XRANGE webhook_events - +
```

The stream contained messages with the structure:

```text
event_id
    │
    └── UUID of the persisted PostgreSQL event
```

The API was also tested by creating new events and confirming that their IDs appeared in the Redis Stream.

---

# Week 2 — Day 2–3: Delivery Worker

## Status: COMPLETED ✅

Week 2 Day 2–3 introduced the background delivery worker.

The worker consumes event IDs from the Redis Stream using a Redis Consumer Group and performs the first version of actual webhook delivery.

The implemented flow is:

```text
Redis Stream
      │
      ▼
Consumer Group
      │
      ▼
Delivery Worker
      │
      ▼
Load Event from PostgreSQL
      │
      ▼
Find Matching Subscriptions
      │
      ▼
Find Active Subscribers
      │
      ▼
Generate HMAC Signature
      │
      ▼
HTTP POST
      │
      ▼
Create DeliveryAttempt
      │
      ▼
ACK Redis Message
```

---

## Worker Location

The worker is intentionally kept separate from the FastAPI application:

```text
webhook-gateway/
├── api/
│   └── app/
│
├── workers/
│   └── worker.py
│
├── dashboard/
└── demo-subscriber/
```

The worker source file is:

```text
workers/worker.py
```

The `workers/` directory is at the project root rather than inside `api/`.

---

## Docker Worker Service

A dedicated worker service was added to Docker Compose.

The worker uses the API image so that it can access the existing application models and database configuration.

The important configuration is:

```yaml
worker:
  build:
    context: ./api
  container_name: webhook-worker
  environment:
    DATABASE_URL: ${DATABASE_URL}
    REDIS_URL: ${REDIS_URL}
    WORKER_NAME: worker-1
    PYTHONPATH: /app
  volumes:
    - ./api:/app
    - ./workers:/worker
  command: python -u /worker/worker.py
  depends_on:
    - postgres
    - redis
```

`PYTHONPATH=/app` allows the worker to import the existing application package:

```python
from app.db.session import SessionLocal
```

The worker uses:

```text
/app
```

for the FastAPI application code and:

```text
/worker
```

for the worker code.

The `-u` option is used so that worker output is written to Docker logs immediately.

---

# Redis Consumer Group

The worker uses the Redis Consumer Group:

```text
delivery_workers
```

The stream is:

```text
webhook_events
```

The worker creates the consumer group if it does not already exist.

If the group already exists, the worker continues normally.

The worker identifies itself as:

```text
worker-1
```

using the `WORKER_NAME` environment variable.

---

## Event Consumption

The worker uses:

```python
XREADGROUP
```

to consume messages from the Redis Stream.

The worker waits for new messages and receives the `event_id` stored in each Redis message.

For example:

```text
Received event: 00a1729e-9f6b-486b-9c64-16b563af297c
(message_id=1790573350622-0)
```

After successfully processing the event, the worker acknowledges the Redis message:

```text
ACKed message: 1790573350622-0
```

The worker was tested successfully with newly created events.

---

## Redis Idle Timeout Handling

`XREADGROUP` uses a blocking read:

```python
block=5000
```

When the stream remains idle, Redis/redis-py can raise a timeout exception.

The worker handles this using:

```python
from redis.exceptions import TimeoutError
```

and:

```python
except TimeoutError:
    continue
```

This prevents the worker from exiting when there are temporarily no new events.

---

# PostgreSQL Event Loading

After receiving an `event_id`, the worker loads the corresponding event from PostgreSQL using the existing SQLAlchemy models.

The worker uses:

```text
api/app/db/session.py
```

and:

```python
SessionLocal
```

to create a database session.

The event is retrieved using its UUID.

If the event cannot be found, the worker logs the situation and does not attempt delivery.

---

# Subscription Matching

After loading the event, the worker searches for subscriptions matching:

```text
Subscription.event_type == Event.event_type
```

Only subscriptions satisfying:

```text
is_active = true
```

are selected.

The subscriber must also have:

```text
status = active
```

Therefore, the delivery worker only sends events to subscribers that are currently eligible to receive them.

The relationship is:

```text
Event
  │
  │ event_type
  ▼
Subscription
  │
  │ subscriber_id
  ▼
Subscriber
```

---

# HMAC-SHA256 Signing

The worker generates a signature for every webhook request.

The signed content is:

```text
timestamp + "." + payload_json
```

The subscriber's secret is used as the HMAC key.

The algorithm is:

```text
HMAC-SHA256
```

The resulting hexadecimal signature is sent using:

```text
X-Webhook-Signature
```

The timestamp is sent using:

```text
X-Webhook-Timestamp
```

The request also contains:

```text
Content-Type: application/json
```

The signing process is:

```text
Subscriber Secret
       │
       ▼
HMAC-SHA256
       │
       │ timestamp + "." + payload_json
       ▼
Webhook Signature
```

The receiver will later verify this signature in the demo subscriber.

---

# HTTP Webhook Delivery

The worker uses `httpx.AsyncClient` to send the webhook request.

The request is sent using:

```text
POST
```

to the subscriber's configured:

```text
endpoint_url
```

The event payload is serialized as JSON before being sent.

A request timeout of 10 seconds is currently used.

---

# DeliveryAttempt Persistence

After attempting delivery, the worker creates a `DeliveryAttempt` record.

The existing `DeliveryAttempt` model is used without changing the database schema.

The recorded information includes:

```text
event_id
subscriber_id
attempt_number
status
http_status_code
response_body
latency_ms
attempted_at
```

For the current Week 2 implementation:

```text
attempt_number = 1
```

No retry scheduling has been implemented yet.

Retries are intentionally reserved for Week 3.

---

## Successful Delivery

A response with an HTTP status code in the range:

```text
200–299
```

is recorded as:

```text
status = success
```

The HTTP status code, response body, and delivery latency are also stored.

---

## Failed Delivery

A non-2xx HTTP response is recorded as:

```text
status = failed
```

The HTTP status code and response body are stored.

Network/request errors are also recorded as failed delivery attempts.

The worker does not crash when a subscriber cannot be reached.

---

# Delivery Verification

The worker was tested against an existing subscriber endpoint:

```text
https://example.com/webhooks/notifications
```

The worker successfully performed the delivery:

```text
Received event: 97e25653-2ca3-40c2-a7f9-0570367efbf2
(message_id=1790661483890-0)

Delivered event 97e25653-2ca3-40c2-a7f9-0570367efbf2
to https://example.com/webhooks/notifications
status=405
latency=96ms

ACKed message: 1790661483890-0
```

The `405 Method Not Allowed` response confirmed that the HTTP endpoint was reached and responded to the request.

The response was correctly recorded as a failed `DeliveryAttempt`.

The database entry was manually verified and contained the expected:

```text
event_id
subscriber_id
attempt_number = 1
status = failed
http_status_code = 405
latency_ms
```

---

# Current Week 2 Architecture

The implementation now has the following pipeline:

```text
                         ┌──────────────────┐
                         │     Producer     │
                         └────────┬─────────┘
                                  │
                                  │ POST /events
                                  ▼
                         ┌──────────────────┐
                         │   FastAPI API    │
                         └────────┬─────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
                    ▼                           ▼
             ┌──────────────┐           ┌──────────────┐
             │  PostgreSQL  │           │ Redis Stream │
             │              │           │webhook_events│
             └──────────────┘           └──────┬───────┘
                                               │
                                               ▼
                                      ┌─────────────────┐
                                      │ Consumer Group  │
                                      │delivery_workers │
                                      └────────┬────────┘
                                               │
                                               ▼
                                      ┌─────────────────┐
                                      │ Delivery Worker │
                                      └────────┬────────┘
                                               │
                                               ▼
                                      ┌─────────────────┐
                                      │   Subscription  │
                                      │    Matching    │
                                      └────────┬────────┘
                                               │
                                               ▼
                                      ┌─────────────────┐
                                      │    Subscriber   │
                                      │  HMAC Signing   │
                                      └────────┬────────┘
                                               │
                                               │ HTTP POST
                                               ▼
                                      ┌─────────────────┐
                                      │    Subscriber   │
                                      │    Endpoint     │
                                      └────────┬────────┘
                                               │
                                               ▼
                                      ┌─────────────────┐
                                      │DeliveryAttempt  │
                                      └─────────────────┘
```

---

# Week 2 — Day 1–3 Completion Checklist

```text
Week 2 — Day 1
    ✅ Redis Stream configuration
    ✅ Redis Python client
    ✅ Event publisher
    ✅ webhook_events stream
    ✅ Publish event_id after successful persistence
    ✅ Duplicate events do not create duplicate Redis messages
    ✅ Redis Stream verified

Week 2 — Day 2–3
    ✅ Delivery worker
    ✅ Redis Consumer Group
    ✅ delivery_workers consumer group
    ✅ Worker event consumption
    ✅ Redis ACK handling
    ✅ Redis idle timeout handling
    ✅ PostgreSQL event loading
    ✅ Active subscription matching
    ✅ Active subscriber filtering
    ✅ HMAC-SHA256 signing
    ✅ X-Webhook-Signature header
    ✅ X-Webhook-Timestamp header
    ✅ HTTP POST delivery
    ✅ Delivery latency measurement
    ✅ DeliveryAttempt persistence
    ✅ Successful delivery handling
    ✅ Failed delivery handling
    ✅ Network error handling
    ✅ End-to-end worker delivery verified
```

---

# Important Week 2 Checkpoint

The project has now progressed from simply accepting and storing events to actually processing them asynchronously.

The current flow is:

```text
Submit Event
     │
     ▼
Persist in PostgreSQL
     │
     ▼
Publish event_id
     │
     ▼
Redis Stream
     │
     ▼
Delivery Worker
     │
     ▼
Match Subscriber
     │
     ▼
Sign Payload
     │
     ▼
HTTP POST
     │
     ▼
Record DeliveryAttempt
     │
     ▼
ACK Message
```

At this point:

* Events are published asynchronously through Redis Streams.
* A dedicated worker consumes events using a Redis Consumer Group.
* Events are loaded from PostgreSQL before delivery.
* Active subscriptions determine which subscribers receive an event.
* Webhook requests are signed using HMAC-SHA256.
* Delivery attempts are persisted.
* HTTP failures are recorded.
* Redis messages are acknowledged after delivery processing.

**Retries, retry scheduling, circuit breaking, and dead-letter handling have not been implemented yet.**

These are intentionally reserved for **Week 3**.

---

# Current Position

```text
Phase 0 — Setup
    ✅ Docker Compose
    ✅ PostgreSQL
    ✅ Redis
    ✅ FastAPI
    ✅ Next.js
    ✅ Alembic

Week 1 — Day 1–2
    ✅ Schema + Models

Week 1 — Day 3
    ✅ Event Ingest API

Week 1 — Day 4
    ✅ Subscriber Management

Week 1 — Day 5
    ✅ Tests + Documentation

Week 2 — Day 1
    ✅ Redis Stream Publishing

Week 2 — Day 2–3
    ✅ Delivery Worker
    ✅ Redis Consumer Group
    ✅ Event Loading
    ✅ Subscription Matching
    ✅ HMAC Signing
    ✅ HTTP Delivery
    ✅ DeliveryAttempt Logging
```

## Current Position: Week 2 — Day 3 COMPLETED ✅

The next implementation step is:

**Week 2 → Day 4: Demo Subscriber + HMAC Verification**

The demo subscriber will provide a controlled endpoint for verifying the complete signed webhook flow and will also include an outage toggle for later retry/circuit-breaker testing.

# Week 2 — Day 4: Demo Subscriber + HMAC Verification

## Status: COMPLETED ✅

Week 2 Day 4 introduced a controlled demo subscriber service for testing the complete webhook delivery flow.

The demo subscriber provides a real HTTP endpoint that receives webhook requests from the delivery worker and verifies the HMAC signature generated by the gateway.

The resulting flow is:

```text
Webhook Reliability Gateway
          │
          │ HTTP POST
          │
          ▼
┌─────────────────────────┐
│    Demo Subscriber      │
│                         │
│  Receive Webhook        │
│         │               │
│         ▼               │
│  Read Raw Request Body  │
│         │               │
│         ▼               │
│  Generate Expected HMAC │
│         │               │
│         ▼               │
│  Compare Signatures     │
│         │               │
│         ▼               │
│  Accept / Reject        │
└─────────────────────────┘
```

---

## Demo Subscriber Service

A separate FastAPI application was created:

```text
demo-subscriber/
├── app.py
├── requirements.txt
├── Dockerfile
└── test_app.py
```

The service runs independently from the main API and worker.

It is exposed through Docker Compose on:

```text
http://localhost:8001
```

Inside the Docker network, the worker communicates with it using:

```text
http://demo-subscriber:8001/webhook
```

This keeps the demo subscriber isolated from the main API while still allowing it to participate in the complete delivery pipeline.

---

## Webhook Endpoint

The demo subscriber provides:

```text
POST /webhook
```

The endpoint receives:

```text
Request Body
X-Webhook-Signature
X-Webhook-Timestamp
```

The application reads the raw request body before performing signature verification.

This is important because the signature generated by the worker is based on the exact serialized payload.

---

## HMAC Verification

The demo subscriber uses the same signing procedure as the delivery worker.

The signed data is constructed as:

```text
timestamp + "." + payload
```

The expected signature is generated using:

```text
HMAC-SHA256(
    WEBHOOK_SECRET,
    timestamp + "." + payload
)
```

The resulting hexadecimal digest is compared with:

```text
X-Webhook-Signature
```

The comparison uses:

```python
hmac.compare_digest()
```

to perform the signature comparison safely.

The same shared secret is configured through:

```env
WEBHOOK_SECRET=day4-demo-secret
```

---

## Request Validation

The demo subscriber validates the webhook request before accepting it.

The following cases are handled:

```text
Missing X-Webhook-Signature
        │
        ▼
     401

Missing X-Webhook-Timestamp
        │
        ▼
     401

Invalid HMAC Signature
        │
        ▼
     401

Valid HMAC Signature
        │
        ▼
     200
```

A successful request returns:

```json
{
  "status": "received",
  "signature_valid": true
}
```

---

## Health Endpoint

A basic health endpoint was also added:

```text
GET /
```

It returns the running status of the demo subscriber.

This provides a simple way to verify that the container is running correctly.

---

## Outage Toggle

The demo subscriber also includes:

```text
POST /toggle-outage
```

This toggles an internal outage mode.

When outage mode is enabled, webhook requests return:

```text
500 Internal Server Error
```

The outage functionality is intentionally included for the reliability features planned for Week 3.

It will later be used to demonstrate:

```text
Delivery Failure
      │
      ▼
Retry
      │
      ▼
Backoff
      │
      ▼
Circuit Breaker
      │
      ▼
Dead Letter
```

No retry or circuit-breaker behavior was implemented during Week 2.

---

## Docker Integration

The demo subscriber was added as a separate Docker Compose service:

```yaml
demo-subscriber:
  build:
    context: ./demo-subscriber
  container_name: webhook-demo-subscriber
  environment:
    WEBHOOK_SECRET: ${WEBHOOK_SECRET}
  ports:
    - "8001:8001"
  volumes:
    - ./demo-subscriber:/app
```

The worker can therefore reach the subscriber using its Docker service name:

```text
http://demo-subscriber:8001/webhook
```

---

## End-to-End HMAC Verification

A demo subscriber was registered through the API and configured with the demo endpoint:

```text
http://demo-subscriber:8001/webhook
```

The subscriber was subscribed to:

```text
order.created
```

A controlled integration event was then submitted.

The complete flow was verified:

```text
POST /events
      │
      ▼
PostgreSQL
      │
      ▼
Redis Stream
      │
      ▼
Delivery Worker
      │
      ▼
Load Subscriber
      │
      ▼
Generate HMAC
      │
      ▼
HTTP POST
      │
      ▼
Demo Subscriber
      │
      ▼
Verify HMAC
      │
      ▼
200 OK
      │
      ▼
DeliveryAttempt = success
      │
      ▼
Redis ACK
```

The demo subscriber successfully verified the signature and logged:

```text
Webhook received successfully
```

This confirmed that the gateway's HMAC signing implementation and the receiving subscriber's verification implementation are compatible.

---

# Week 2 — Day 5: Integration Testing + End-of-Week Checkpoint

## Status: COMPLETED ✅

Week 2 Day 5 completed the testing of the webhook delivery pipeline.

The focus was on validating the demo subscriber independently and then verifying the complete system using a final end-to-end event.

---

## Demo Subscriber Tests

A pytest test suite was added:

```text
demo-subscriber/
├── app.py
├── test_app.py
├── requirements.txt
└── Dockerfile
```

The test suite uses FastAPI's:

```text
TestClient
```

The tests cover the important HMAC verification cases.

---

## Valid Signature Test

A valid payload, timestamp, and HMAC signature are generated using the test secret.

The request is sent to:

```text
POST /webhook
```

Expected result:

```text
200 OK
```

The response is verified to contain:

```json
{
  "status": "received",
  "signature_valid": true
}
```

---

## Invalid Signature Test

A webhook request is sent with an invalid signature.

Expected result:

```text
401 Unauthorized
```

The response verifies:

```json
{
  "detail": "Invalid webhook signature"
}
```

This confirms that forged or incorrectly signed requests are rejected.

---

## Missing Signature Test

A request is sent without:

```text
X-Webhook-Signature
```

Expected result:

```text
401 Unauthorized
```

The response verifies:

```json
{
  "detail": "Missing X-Webhook-Signature"
}
```

---

## Missing Timestamp Test

A request is sent without:

```text
X-Webhook-Timestamp
```

Expected result:

```text
401 Unauthorized
```

The response verifies:

```json
{
  "detail": "Missing X-Webhook-Timestamp"
}
```

---

## Test Results

All four demo subscriber tests passed successfully:

```text
4 tests passed
```

The verified cases are:

```text
Valid signature
      ✅

Invalid signature
      ✅

Missing signature
      ✅

Missing timestamp
      ✅
```

---

## Final End-to-End Integration Test

After the individual subscriber tests passed, the complete system was tested using a fresh event.

A new:

```text
order.created
```

event was submitted through the API.

The event successfully travelled through the complete delivery pipeline:

```text
POST /events
      │
      ▼
Persist Event
      │
      ▼
Publish event_id
      │
      ▼
Redis Stream
      │
      ▼
Delivery Worker
      │
      ▼
Consumer Group
      │
      ▼
Load Event
      │
      ▼
Match Subscription
      │
      ▼
Generate HMAC
      │
      ▼
HTTP POST
      │
      ▼
Demo Subscriber
      │
      ▼
Verify HMAC
      │
      ▼
200 OK
      │
      ▼
Create DeliveryAttempt
      │
      ▼
ACK Redis Message
```

The final integration event completed successfully.

The worker recorded:

```text
status=200
```

The demo subscriber successfully verified the webhook signature.

The corresponding delivery attempt was recorded as:

```text
attempt_number = 1
status = success
http_status_code = 200
```

The Redis message was also successfully acknowledged.

---

# Week 2 Completion Checkpoint

At the end of Week 2, the gateway has progressed from event ingestion to an actual asynchronous webhook delivery pipeline.

The complete system now supports:

```text
Event Ingestion
      │
      ▼
PostgreSQL Persistence
      │
      ▼
Redis Stream Publishing
      │
      ▼
Redis Consumer Group
      │
      ▼
Background Delivery Worker
      │
      ▼
Subscription Matching
      │
      ▼
Active Subscriber Filtering
      │
      ▼
HMAC-SHA256 Signing
      │
      ▼
HTTP Webhook Delivery
      │
      ▼
HMAC Verification
      │
      ▼
DeliveryAttempt Logging
      │
      ▼
Redis ACK
```

---

## Week 2 Completion Status

```text
Week 2 — Day 1
    ✅ Redis Stream configuration
    ✅ Redis Python client
    ✅ Event publisher
    ✅ webhook_events stream
    ✅ Publish event_id after successful persistence
    ✅ Duplicate events do not create duplicate Redis messages
    ✅ Redis Stream verified

Week 2 — Day 2–3
    ✅ Delivery worker
    ✅ Redis Consumer Group
    ✅ delivery_workers consumer group
    ✅ Worker event consumption
    ✅ Redis ACK handling
    ✅ Redis idle timeout handling
    ✅ PostgreSQL event loading
    ✅ Active subscription matching
    ✅ Active subscriber filtering
    ✅ HMAC-SHA256 signing
    ✅ X-Webhook-Signature header
    ✅ X-Webhook-Timestamp header
    ✅ HTTP POST delivery
    ✅ Delivery latency measurement
    ✅ DeliveryAttempt persistence
    ✅ Successful delivery handling
    ✅ Failed delivery handling
    ✅ Network error handling
    ✅ End-to-end worker delivery verified

Week 2 — Day 4
    ✅ Demo subscriber FastAPI application
    ✅ Demo subscriber Docker service
    ✅ POST /webhook endpoint
    ✅ HMAC-SHA256 signature verification
    ✅ X-Webhook-Signature verification
    ✅ X-Webhook-Timestamp validation
    ✅ Invalid signature rejection
    ✅ Missing signature rejection
    ✅ Missing timestamp rejection
    ✅ Demo subscriber health endpoint
    ✅ Outage toggle
    ✅ End-to-end HMAC verification

Week 2 — Day 5
    ✅ Demo subscriber pytest setup
    ✅ Valid signature test
    ✅ Invalid signature test
    ✅ Missing signature test
    ✅ Missing timestamp test
    ✅ 4 tests passing
    ✅ Final end-to-end integration test
    ✅ Successful HTTP 200 delivery verified
    ✅ DeliveryAttempt success verified
    ✅ Redis ACK verified
```

---

# Important Week 2 Checkpoint

The Week 2 happy-path delivery pipeline is now fully implemented and verified.

The gateway can now:

```text
Receive Event
      │
      ▼
Persist Event
      │
      ▼
Publish Event ID
      │
      ▼
Process Asynchronously
      │
      ▼
Find Matching Subscriber
      │
      ▼
Sign Webhook
      │
      ▼
Deliver HTTP Request
      │
      ▼
Verify Signature
      │
      ▼
Record Delivery Result
      │
      ▼
ACK Stream Message
```

The demo subscriber provides a controlled environment for testing the delivery and HMAC verification flow.

The Week 2 implementation intentionally stops at the **happy path**.

The following reliability mechanisms have **not** been implemented yet:

```text
❌ Retries
❌ Exponential backoff
❌ Retry scheduling
❌ Circuit breaker
❌ Dead-letter processing
❌ Dead-letter replay
```

These features are intentionally reserved for **Week 3**.

---

# Current Position

```text
Phase 0 — Setup
    ✅ Docker Compose
    ✅ PostgreSQL
    ✅ Redis
    ✅ FastAPI
    ✅ Next.js
    ✅ Alembic

Week 1 — Day 1–2
    ✅ Schema + Models

Week 1 — Day 3
    ✅ Event Ingest API

Week 1 — Day 4
    ✅ Subscriber Management

Week 1 — Day 5
    ✅ Tests + Documentation

Week 2 — Day 1
    ✅ Redis Stream Publishing

Week 2 — Day 2–3
    ✅ Delivery Worker
    ✅ Redis Consumer Group
    ✅ Event Loading
    ✅ Subscription Matching
    ✅ HMAC Signing
    ✅ HTTP Delivery
    ✅ DeliveryAttempt Logging

Week 2 — Day 4
    ✅ Demo Subscriber
    ✅ HMAC Verification
    ✅ Outage Toggle
    ✅ End-to-End Delivery Verification

Week 2 — Day 5
    ✅ Demo Subscriber Tests
    ✅ 4 Tests Passing
    ✅ Final Integration Test
    ✅ Week 2 Completed
```

## Current Position: Week 2 COMPLETED ✅

The project has now completed the entire **Week 2 — Delivery Pipeline (Happy Path) + HMAC Signing** phase.

The next implementation phase is:

**Week 3 → Retries + Backoff + Circuit Breaker + Dead-Letter Handling**

# Week 3 — Day 1–2: Retry with Exponential Backoff + Jitter

## Status: COMPLETED ✅

Week 3 Day 1–2 introduced the retry mechanism for failed webhook deliveries.

The gateway can now automatically retry failed deliveries without blocking the delivery worker, using exponential backoff, jitter, per-subscriber retry tracking, and a dedicated retry scheduler.

---

# Retry Architecture

The retry flow is:

```text
Webhook Delivery
      │
      ▼
HTTP Response / Network Error
      │
      ├── 2xx ───────────────► Success
      │
      └── Non-2xx / Error
                │
                ▼
        Calculate Retry Delay
                │
                ▼
        Store DeliveryAttempt
        status = pending_retry
                │
                ▼
        next_retry_at
                │
                ▼
        Retry Scheduler
                │
        Retry becomes due
                │
                ▼
        Redis Stream
                │
                ▼
        Delivery Worker
                │
                ▼
        Retry Specific
        Subscriber
```

Retries are tracked independently for each:

```text
Event + Subscriber
```

This ensures that one subscriber's failures do not interfere with delivery attempts for another subscriber.

---

# Retry Configuration

The worker uses the following retry schedule:

```python
RETRY_DELAYS = [1, 5, 30, 300, 1800]
JITTER_MAX_SECONDS = 1
```

The corresponding retry delays are:

```text
Attempt 1 → approximately 1 second
Attempt 2 → approximately 5 seconds
Attempt 3 → approximately 30 seconds
Attempt 4 → approximately 5 minutes
Attempt 5 → approximately 30 minutes
```

Random jitter is added to prevent multiple failed deliveries from being retried at exactly the same time.

The retry delay is calculated using the attempt number and is capped by the configured maximum delay.

---

# Delivery Failure Handling

The worker now treats the following conditions as delivery failures:

```text
Non-2xx HTTP response
        │
        └──► Retry

HTTP request/network error
        │
        └──► Retry
```

Retries are **not performed inline**.

Instead, the failed attempt is persisted with:

```text
status = pending_retry
next_retry_at = calculated retry time
```

This allows the worker to immediately finish processing the current Redis message instead of waiting for the retry delay.

---

# DeliveryAttempt Retry Tracking

The existing `DeliveryAttempt` model was extended through its existing:

```text
next_retry_at
```

field.

Each retry attempt records:

```text
event_id
subscriber_id
attempt_number
status
http_status_code
response_body
latency_ms
attempted_at
next_retry_at
```

Example:

```text
Attempt 1
    status = pending_retry
    next_retry_at = now + ~1 second

Attempt 2
    status = pending_retry
    next_retry_at = now + ~5 seconds

Attempt 3
    status = pending_retry
    next_retry_at = now + ~30 seconds
```

Successful deliveries continue to record:

```text
status = success
next_retry_at = NULL
```

---

# Per-Subscriber Retry Handling

Retry messages contain both:

```text
event_id
subscriber_id
```

The initial event message contains only:

```text
event_id
```

This allows the worker to distinguish between:

```text
Initial delivery
```

and:

```text
Retry delivery for a specific subscriber
```

For example:

```text
Initial Event
     │
     ├── Subscriber A → Success
     │
     └── Subscriber B → Failure
                         │
                         ▼
                    Retry Subscriber B
```

Subscriber A does not get unnecessarily retried.

---

# Retry Scheduler

A separate worker process was introduced:

```text
workers/retry_scheduler.py
```

The scheduler continuously polls PostgreSQL for delivery attempts where:

```text
status = pending_retry
```

and:

```text
next_retry_at <= current_time
```

When a retry becomes due, the scheduler publishes a new Redis Stream message containing:

```text
event_id
subscriber_id
```

The attempt is then marked:

```text
status = retry_queued
```

The Redis Stream message is subsequently consumed by the normal delivery worker.

---

# Retry Scheduler Flow

```text
PostgreSQL
     │
     ▼
Find pending_retry
where next_retry_at <= now
     │
     ▼
Publish to Redis Stream
     │
     ├── event_id
     └── subscriber_id
     │
     ▼
Mark attempt retry_queued
     │
     ▼
Redis Consumer Group
     │
     ▼
Delivery Worker
     │
     ▼
Retry Subscriber
```

The scheduler polls approximately once per second.

A limit is also applied to the number of retry records processed in one polling cycle.

---

# Redis ACK Handling

Retry messages use the same Redis Consumer Group mechanism as normal deliveries.

After the worker processes the retry:

```text
Retry Delivery
      │
      ▼
Record DeliveryAttempt
      │
      ▼
Redis ACK
```

This maintains the existing at-least-once delivery behavior.

---

# Maximum Retry Handling

A maximum retry boundary was added using:

```python
MAX_RETRY_ATTEMPTS = len(RETRY_DELAYS)
```

With the current configuration:

```text
MAX_RETRY_ATTEMPTS = 5
```

The behavior is:

```text
Attempt 1 → failure → retry
Attempt 2 → failure → retry
Attempt 3 → failure → retry
Attempt 4 → failure → retry
Attempt 5 → failure → retries exhausted
```

After the final allowed attempt fails:

```text
status = failed
next_retry_at = NULL
```

No additional retry is scheduled.

Actual Dead Letter creation is intentionally reserved for:

```text
Week 3 — Day 4
```

---

# Retry Exhaustion Flow

```text
Attempt 5
    │
    ▼
Delivery Failure
    │
    ▼
Maximum Attempts Reached
    │
    ▼
status = failed
    │
    ▼
next_retry_at = NULL
    │
    ▼
No Further Retry
```

This prevents the system from retrying a permanently failing endpoint indefinitely.

---

# Testing and Verification

The retry system was tested using a deliberately failing subscriber endpoint.

The retry delays were temporarily shortened for testing:

```text
[1, 2, 3, 4, 5]
```

The delivery successfully progressed through:

```text
Attempt 1
    ↓
Retry

Attempt 2
    ↓
Retry

Attempt 3
    ↓
Retry

Attempt 4
    ↓
Retry

Attempt 5
    ↓
Retries Exhausted
```

The system correctly stopped after the fifth attempt.

The production retry configuration was then restored to:

```text
[1, 5, 30, 300, 1800]
```

---

# Week 3 — Day 1–2 Completion Checklist

```text
Week 3 — Day 1–2

    ✅ Retry configuration
    ✅ Exponential backoff
    ✅ Random jitter
    ✅ Non-2xx retry handling
    ✅ Network error retry handling
    ✅ Per-event retry tracking
    ✅ Per-subscriber retry tracking
    ✅ next_retry_at handling
    ✅ pending_retry status
    ✅ Retry scheduler
    ✅ PostgreSQL retry polling
    ✅ Redis retry publishing
    ✅ Retry-specific subscriber routing
    ✅ retry_queued status
    ✅ Redis ACK for retry messages
    ✅ Maximum retry attempts
    ✅ Retry exhaustion handling
    ✅ No retry after maximum attempts
    ✅ Retry flow manually verified
```

---

# Important Week 3 Checkpoint

The gateway now supports asynchronous retries for failed webhook deliveries.

The delivery pipeline has evolved from:

```text
Event
  │
  ▼
Redis Stream
  │
  ▼
Delivery Worker
  │
  ▼
HTTP Delivery
  │
  ▼
Success / Failure
```

to:

```text
Event
  │
  ▼
Redis Stream
  │
  ▼
Delivery Worker
  │
  ▼
HTTP Delivery
  │
  ├──────────────► Success
  │
  └── Failure
       │
       ▼
   pending_retry
       │
       ▼
   next_retry_at
       │
       ▼
 Retry Scheduler
       │
       ▼
 Redis Stream
       │
       ▼
 Delivery Worker
       │
       ▼
 Retry Delivery
```

The system no longer performs retries inline and will not retry a permanently failing delivery indefinitely.

---

# Current Position

```text
Phase 0 — Setup
    ✅ Docker Compose
    ✅ PostgreSQL
    ✅ Redis
    ✅ FastAPI
    ✅ Next.js
    ✅ Alembic

Week 1 — Day 1–2
    ✅ Schema + Models

Week 1 — Day 3
    ✅ Event Ingest API

Week 1 — Day 4
    ✅ Subscriber Management

Week 1 — Day 5
    ✅ Tests + Documentation

Week 2 — Day 1
    ✅ Redis Stream Publishing

Week 2 — Day 2–3
    ✅ Delivery Worker
    ✅ Redis Consumer Group
    ✅ Event Loading
    ✅ Subscription Matching
    ✅ HMAC Signing
    ✅ HTTP Delivery
    ✅ DeliveryAttempt Logging

Week 2 — Day 4
    ✅ Demo Subscriber
    ✅ HMAC Verification
    ✅ Outage Toggle

Week 2 — Day 5
    ✅ Tests
    ✅ Final Integration Test
    ✅ Week 2 Completed

Week 3 — Day 1–2
    ✅ Retry with Exponential Backoff
    ✅ Jitter
    ✅ Retry Scheduling
    ✅ Per-Subscriber Retries
    ✅ Maximum Retry Handling
    ✅ Retry Exhaustion
```

## Current Position: Week 3 — Day 1–2 COMPLETED ✅

The next implementation phase is:

**Week 3 → Day 3: Circuit Breaker**

# Week 3 — Day 3: Circuit Breaker

## Status: COMPLETED ✅

Week 3 Day 3 introduced a **per-subscriber circuit breaker** to prevent the gateway from continuously attempting deliveries to a subscriber that is repeatedly failing.

The circuit breaker monitors recent delivery failures for each subscriber.

When the configured failure threshold is reached, the subscriber is moved from:

```text
active
```

to:

```text
degraded
```

While degraded, new deliveries and retry deliveries are skipped for that subscriber.

A periodic health-check probe is then used to determine when the subscriber has recovered.

Once the health check succeeds, the subscriber is moved back to:

```text
active
```

and normal delivery and retry processing resumes.

---

# Circuit Breaker Architecture

The circuit breaker flow is:

```text
Webhook Delivery
      │
      ▼
HTTP Response / Network Error
      │
      ├── Success ───────────────► Normal Delivery
      │
      └── Failure
            │
            ▼
      Record DeliveryAttempt
            │
            ▼
      Check Recent Failures
            │
            ▼
      5 Consecutive Failures
            │
            ▼
      Subscriber → degraded
            │
            ▼
      Stop New Deliveries
            │
            ▼
      Periodic Health Check
            │
       ┌────┴─────┐
       │          │
    Failure    Success
       │          │
       ▼          ▼
    Remain     active
    degraded     │
                  ▼
          Resume Deliveries
          + Retry Backlog
```

The circuit breaker operates **per subscriber**.

Therefore, one failing subscriber does not cause healthy subscribers to stop receiving events.

---

# Circuit Breaker Configuration

The worker contains dedicated configuration values:

```python
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_HEALTH_CHECK_INTERVAL = 60
```

This means:

```text
Failure threshold = 5 attempts
Health check interval = 60 seconds
```

The subscriber is considered unhealthy when the configured number of recent delivery attempts indicate consecutive failures.

---

# Subscriber States

The existing `Subscriber.status` field is used to represent the circuit breaker state.

The relevant states are:

```text
active
degraded
paused
```

The circuit breaker specifically transitions between:

```text
active
   │
   │ repeated failures
   ▼
degraded
   │
   │ successful health check
   ▼
active
```

The `degraded` state is controlled internally by the delivery worker.

The normal subscriber update API continues to expose only:

```text
active
paused
```

so the circuit-breaker state cannot be manually set through the normal subscriber update endpoint.

---

# Failure Detection

A helper function was added to inspect the most recent delivery attempts for a subscriber.

The worker retrieves the latest:

```text
CIRCUIT_FAILURE_THRESHOLD
```

delivery attempts for that subscriber.

With the current configuration, this means the worker checks the latest:

```text
5 attempts
```

The relevant failure states are:

```text
pending_retry
retry_queued
failed
```

These represent delivery attempts that did not successfully deliver the webhook.

The worker then checks whether all five recent attempts represent failures.

Conceptually:

```text
Recent DeliveryAttempts

Attempt 5 → failure
Attempt 4 → failure
Attempt 3 → failure
Attempt 2 → failure
Attempt 1 → failure

             ↓

     5 consecutive failures

             ↓

Subscriber → degraded
```

---

# Opening the Circuit

When the failure threshold is reached and the subscriber is currently active, the worker changes:

```text
subscriber.status
```

from:

```text
active
```

to:

```text
degraded
```

The status change is persisted in PostgreSQL.

The worker also logs the circuit-breaker transition:

```text
Circuit breaker OPENED for subscriber <id>: 5 consecutive failures
```

The important behavior is that the system does **not** continue hammering the failing subscriber after the circuit opens.

---

# Preventing New Deliveries

The initial event fan-out now considers subscriber status.

Only subscribers with:

```text
status = active
```

are selected for normal delivery.

Conceptually:

```text
Event
  │
  ▼
Matching Subscription
  │
  ▼
Subscriber
  │
  ├── active ───────► Deliver
  │
  └── degraded ────► Skip
```

Therefore, once the circuit breaker opens, newly ingested events are not delivered to the degraded subscriber.

---

# Preventing Retry Deliveries

The retry path also checks the subscriber's circuit-breaker state.

If a retry is being processed for a subscriber whose status is:

```text
degraded
```

the worker skips the retry delivery.

The worker logs:

```text
Skipping retry for degraded subscriber: <id>
```

This prevents the retry system from continuously sending requests to a known unhealthy endpoint.

The pending retry remains available for processing after the subscriber recovers.

---

# Retry Scheduler Integration

The retry scheduler was also updated to respect the circuit breaker.

Due retries are selected only when the subscriber is:

```text
active
```

Conceptually:

```text
pending_retry
      │
      ▼
next_retry_at reached
      │
      ▼
Check Subscriber
      │
 ┌────┴─────┐
 │          │
active    degraded
 │          │
 ▼          ▼
Queue      Keep
retry      pending
```

This is important because otherwise the retry scheduler could continue publishing retries for a subscriber whose circuit is already open.

When the subscriber becomes active again, pending retries become eligible for scheduling.

---

# Health Check Probe

A periodic health-check mechanism was added to the delivery worker.

The worker periodically searches for subscribers with:

```text
status = degraded
```

and sends a health-check request to their configured endpoint.

The health-check interval is:

```text
60 seconds
```

The health-check uses the subscriber's existing:

```text
endpoint_url
secret
```

and generates a valid HMAC-SHA256 signature using the same signing mechanism used for normal webhook delivery.

The health-check payload is:

```json
{
  "type": "webhook_health_check",
  "message": "Webhook Reliability Gateway health check"
}
```

A dedicated header is also included:

```text
X-Webhook-Health-Check: true
```

The health check therefore verifies that the subscriber endpoint is reachable and capable of successfully receiving a signed request.

---

# Health Check Success

If the health-check request returns a successful HTTP response:

```text
2xx
```

the circuit breaker is closed.

The subscriber status changes:

```text
degraded
      │
      │ successful health check
      ▼
active
```

The change is persisted to PostgreSQL.

The worker logs:

```text
Circuit breaker CLOSED for subscriber <id>
```

Once active again:

```text
Normal deliveries resume
        +
Pending retry backlog resumes
```

---

# Health Check Failure

If the health-check request fails or returns a non-2xx response, the subscriber remains:

```text
degraded
```

The worker continues checking the subscriber periodically.

Conceptually:

```text
degraded
   │
   ▼
Health Check
   │
   ├── Failure ──► remain degraded
   │
   └── Success ──► active
```

This prevents the system from reopening the circuit prematurely while the subscriber is still unavailable.

---

# Worker Architecture

The worker now runs both:

```text
Event Consumption
```

and:

```text
Health Check Scheduling
```

concurrently.

Conceptually:

```text
                    Delivery Worker
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
       Redis Stream             Health Check
       Consumption              Scheduler
              │                     │
              ▼                     ▼
       Event Delivery        Degraded Subscribers
              │                     │
              ▼                     ▼
       Circuit Breaker        Recovery Probe
```

This allows the worker to continue processing normal events while periodically checking degraded subscribers.

---

# Circuit Breaker Lifecycle

The complete subscriber lifecycle is now:

```text
              ┌─────────────┐
              │   active    │
              └──────┬──────┘
                     │
             5 consecutive
                failures
                     │
                     ▼
              ┌─────────────┐
              │  degraded   │
              └──────┬──────┘
                     │
              Health Check
                     │
              ┌──────┴──────┐
              │             │
           Failure        Success
              │             │
              │             ▼
              │      ┌─────────────┐
              └─────►│   active    │
                     └─────────────┘
```

While the subscriber is degraded:

```text
❌ New deliveries
❌ Retry deliveries

✅ Health-check probes
```

After recovery:

```text
✅ New deliveries
✅ Retry backlog
```

---

# Testing and Verification

The circuit breaker was tested using the existing demo subscriber's outage simulation.

The demo subscriber provides:

```text
POST /toggle-outage
```

which switches the subscriber between normal operation and outage mode.

The complete test sequence was:

```text
Subscriber
    │
    ▼
active
    │
    ▼
Enable outage mode
    │
    ▼
Webhook deliveries return HTTP 500
    │
    ▼
Failure attempts recorded
    │
    ▼
5 consecutive failures
    │
    ▼
Circuit breaker opens
    │
    ▼
Subscriber → degraded
    │
    ▼
New deliveries skipped
    │
    ▼
Disable outage mode
    │
    ▼
Health-check succeeds
    │
    ▼
Circuit breaker closes
    │
    ▼
Subscriber → active
    │
    ▼
Normal delivery resumes
```

The complete circuit-breaker lifecycle was successfully verified.

---

# Final Verification

The following behavior was manually confirmed:

```text
Initial subscriber status
    → active

Outage enabled
    → HTTP 500 responses

Five consecutive failures
    → circuit breaker opened

Subscriber status
    → degraded

Outage disabled
    → subscriber becomes reachable

Health-check
    → successful

Circuit breaker
    → closed

Subscriber status
    → active
```

The recovery path also correctly allows normal delivery and retry processing to resume.

---

# Week 3 — Day 3 Completion Checklist

```text
Week 3 — Day 3

    ✅ Circuit breaker configuration
    ✅ Per-subscriber failure tracking
    ✅ Rolling recent-attempt inspection
    ✅ 5 consecutive failure threshold
    ✅ Subscriber active → degraded transition
    ✅ Stop new deliveries to degraded subscribers
    ✅ Skip retry deliveries to degraded subscribers
    ✅ Retry scheduler degraded-subscriber filtering
    ✅ Periodic health-check scheduler
    ✅ Health-check HMAC signing
    ✅ Health-check endpoint probing
    ✅ Successful health-check recovery
    ✅ Subscriber degraded → active transition
    ✅ Retry backlog resumes after recovery
    ✅ Circuit breaker outage simulation
    ✅ Circuit breaker recovery verified
```

---

# Important Week 3 Checkpoint

The gateway can now protect failing subscribers from continuous delivery attempts.

The reliability pipeline has evolved from:

```text
Event
  │
  ▼
Redis Stream
  │
  ▼
Delivery Worker
  │
  ▼
HTTP Delivery
  │
  ├──────────────► Success
  │
  └── Failure
       │
       ▼
   pending_retry
       │
       ▼
 Retry Scheduler
       │
       ▼
 Redis Stream
       │
       ▼
 Retry Delivery
```

to:

```text
Event
  │
  ▼
Redis Stream
  │
  ▼
Delivery Worker
  │
  ▼
HTTP Delivery
  │
  ├──────────────► Success
  │
  └── Failure
       │
       ▼
   DeliveryAttempt
       │
       ▼
  Check Recent Failures
       │
       ▼
  5 Consecutive Failures
       │
       ▼
Subscriber → degraded
       │
       ├──────────────► Skip New Deliveries
       │
       ├──────────────► Skip Retries
       │
       └──────────────► Health Check
                              │
                         ┌────┴─────┐
                         │          │
                      Failure     Success
                         │          │
                         ▼          ▼
                     degraded    active
                                    │
                                    ▼
                             Resume Delivery
                             + Retry Backlog
```

The circuit breaker therefore adds an important protection mechanism to the retry system:

> **Retries handle temporary delivery failures, while the circuit breaker prevents the gateway from repeatedly hitting a subscriber that is persistently failing.**

---

# Current Position

```text
Phase 0 — Setup
    ✅ Docker Compose
    ✅ PostgreSQL
    ✅ Redis
    ✅ FastAPI
    ✅ Next.js
    ✅ Alembic

Week 1 — Day 1–2
    ✅ Schema + Models

Week 1 — Day 3
    ✅ Event Ingest API

Week 1 — Day 4
    ✅ Subscriber Management

Week 1 — Day 5
    ✅ Tests + Documentation

Week 2 — Day 1
    ✅ Redis Stream Publishing

Week 2 — Day 2–3
    ✅ Delivery Worker
    ✅ Redis Consumer Group
    ✅ Event Loading
    ✅ Subscription Matching
    ✅ HMAC Signing
    ✅ HTTP Delivery
    ✅ DeliveryAttempt Logging

Week 2 — Day 4
    ✅ Demo Subscriber
    ✅ HMAC Verification
    ✅ Outage Toggle

Week 2 — Day 5
    ✅ Tests
    ✅ Final Integration Test
    ✅ Week 2 Completed

Week 3 — Day 1–2
    ✅ Retry with Exponential Backoff
    ✅ Jitter
    ✅ Retry Scheduling
    ✅ Per-Subscriber Retries
    ✅ Maximum Retry Handling
    ✅ Retry Exhaustion

Week 3 — Day 3
    ✅ Circuit Breaker
    ✅ Per-Subscriber Failure Tracking
    ✅ Failure Threshold
    ✅ Degraded Subscriber State
    ✅ Delivery Suppression
    ✅ Retry Suppression
    ✅ Health-Check Recovery
    ✅ Circuit Breaker Verification
```

## Current Position: Week 3 — Day 3 COMPLETED ✅

The project has now completed the **Circuit Breaker** phase.

The next implementation phase is:

**Week 3 → Day 4: Dead-Letter Queue + Replay**

The next step will add permanent failure handling after the maximum retry attempts have been exhausted.
