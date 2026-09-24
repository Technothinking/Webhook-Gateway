# Webhook Reliability Gateway — Database Schema

This document describes the database schema of the Webhook Reliability Gateway and the relationships between the main entities.

## 1. Schema Overview

The system uses five main tables:

* `event`
* `subscriber`
* `subscription`
* `delivery_attempt`
* `dead_letter`

The central delivery relationship is:

```text
Event
  │
  └──< DeliveryAttempt
             │
             └── Subscriber
```

An `Event` represents a webhook event received by the gateway. A `DeliveryAttempt` records an attempt to deliver that event to a particular `Subscriber`.

`Subscription` determines which event types a subscriber is interested in, while `DeadLetter` stores information about events that have permanently failed delivery.

---

## 2. Event

The `event` table stores webhook events received by the gateway.

### Important fields

| Field             | Description                                         |
| ----------------- | --------------------------------------------------- |
| `id`              | Unique UUID identifying the event                   |
| `idempotency_key` | Unique key used to prevent duplicate event creation |
| `event_type`      | Type of event, such as `order.created`              |
| `payload`         | JSON payload associated with the event              |
| `created_at`      | Timestamp when the event was created                |
| `status`          | Current processing status of the event              |

### Idempotency

`idempotency_key` has a unique constraint.

When an event is submitted:

```text
Request
   │
   ├── idempotency_key already exists
   │        ↓
   │    Return existing event
   │
   └── idempotency_key does not exist
            ↓
        Create new event
```

This ensures that repeated requests with the same idempotency key do not create duplicate events.

---

## 3. Subscriber

The `subscriber` table stores webhook consumers that receive events.

### Important fields

| Field          | Description                                     |
| -------------- | ----------------------------------------------- |
| `id`           | Unique UUID identifying the subscriber          |
| `name`         | Subscriber/service name                         |
| `endpoint_url` | URL where webhook requests are delivered        |
| `secret`       | Secret used for webhook authentication/signing  |
| `status`       | Subscriber status, such as `active` or `paused` |
| `created_at`   | Timestamp when the subscriber was created       |

A subscriber represents a service that has registered an endpoint with the gateway.

The secret is returned when the subscriber is created but is not exposed by the subscriber listing endpoint.

---

## 4. Subscription

The `subscription` table connects subscribers with the event types they want to receive.

### Important fields

| Field           | Description                                  |
| --------------- | -------------------------------------------- |
| `id`            | Unique identifier of the subscription        |
| `subscriber_id` | References the subscriber                    |
| `event_type`    | Event type the subscriber wants to receive   |
| `is_active`     | Indicates whether the subscription is active |

### Relationship

```text
Subscriber
    │
    └──< Subscription
              │
              └── event_type
```

For example:

```text
Order Service
    ├── order.created
    └── payment.completed

Analytics Service
    └── order.created

Notification Service
    └── user.created
```

A subscriber can therefore subscribe to multiple event types.

The database has a unique constraint on:

```text
(subscriber_id, event_type)
```

This prevents the same subscriber from being subscribed to the same event type more than once.

---

## 5. DeliveryAttempt

The `delivery_attempt` table records individual attempts to deliver an event to a subscriber.

### Important fields

| Field              | Description                                        |
| ------------------ | -------------------------------------------------- |
| `id`               | Unique identifier of the delivery attempt          |
| `event_id`         | References the event being delivered               |
| `subscriber_id`    | References the target subscriber                   |
| `attempt_number`   | Number of the delivery attempt                     |
| `status`           | Result/status of the attempt                       |
| `http_status_code` | HTTP status returned by the subscriber             |
| `response_body`    | Response received from the subscriber              |
| `latency_ms`       | Delivery request latency                           |
| `attempted_at`     | Time when the attempt occurred                     |
| `next_retry_at`    | Time scheduled for the next retry, when applicable |

### Core relationship

```text
Event
  │
  └──< DeliveryAttempt
             │
             └── Subscriber
```

One event can have multiple delivery attempts.

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

Multiple attempts allow the gateway to implement retry behavior later in the system.

The database also has a unique constraint on:

```text
(event_id, subscriber_id, attempt_number)
```

This prevents duplicate records for the same attempt number for the same event and subscriber.

---

## 6. DeadLetter

The `dead_letter` table stores events that have permanently failed delivery and are moved to the dead-letter state.

### Important fields

| Field           | Description                                               |
| --------------- | --------------------------------------------------------- |
| `id`            | Unique identifier                                         |
| `event_id`      | References the failed event                               |
| `subscriber_id` | References the affected subscriber                        |
| `failed_reason` | Reason the delivery was considered permanently failed     |
| `moved_at`      | Timestamp when the event was moved to dead-letter storage |

The relationship is:

```text
Event
  │
  └──> DeadLetter
          │
          └── Subscriber
```

Dead-letter handling will become important when retry limits and failure handling are implemented.

---

## 7. Overall Entity Relationship

The main relationships can be represented as:

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

Conceptually:

```text
Event
 │
 │ has many
 ▼
DeliveryAttempt
 │
 │ belongs to
 ▼
Subscriber
 │
 │ has many
 ▼
Subscription
```

and a failed delivery can eventually result in:

```text
Event + Subscriber
        │
        ▼
   DeadLetter
```

---

## 8. Why the Schema Is Structured This Way

The schema separates the different responsibilities of the gateway:

### Event

Represents **what happened**.

```text
order.created
payment.completed
user.created
```

### Subscriber

Represents **who receives events**.

```text
Order Service
Analytics Service
Notification Service
```

### Subscription

Represents **which event types a subscriber wants**.

```text
Analytics Service → order.created
```

### DeliveryAttempt

Represents **what happened when the gateway tried to deliver an event**.

```text
Attempt 1 → failed
Attempt 2 → failed
Attempt 3 → succeeded
```

### DeadLetter

Represents **delivery failures that have reached permanent failure handling**.

This separation allows the gateway to independently manage event ingestion, subscriber configuration, subscription filtering, delivery attempts, retries, and dead-letter handling.

---

## 9. Current Implementation Status

At the end of Week 1, the database schema and core APIs are implemented.

Currently implemented:

* Event persistence
* Idempotency and duplicate-event prevention
* Event retrieval
* Event filtering and pagination
* Subscriber creation
* Subscriber listing
* Subscriber status updates
* Subscription creation
* Duplicate subscription prevention

Actual webhook delivery, retry processing, circuit breaking, and worker-based delivery are planned for later phases.
