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
