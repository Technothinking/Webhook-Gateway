# Design decisions

## 1. Commit first, then publish (and a reconciler for the gap)

`POST /events` commits the event to PostgreSQL, then publishes its id to the Redis stream, then stamps `event.published_at`. This is outbox-style rather than a strict transactional outbox: there is no outbox table or relay process. The two writes are not atomic, so a crash or Redis failure between them leaves a stored event that was never queued.

The fix is a reconciler in the retry scheduler. Every 10 seconds it republishes events that are `pending`, older than 60 s, have no delivery attempts, **and** have `published_at IS NULL`, guarded by a short-lived Redis key so each is republished at most once per window.

The `published_at` column exists because of a bug the load test exposed: the first reconciler used only "pending, old, no attempts". During a long backlog, events that were merely waiting in the stream matched that rule and were republished, so workers processed up to 7,300 extra messages in one run. The fan-out guard stopped any duplicate delivery, but the work was wasted. Recording when an event was actually published removed it. The extra write cost about 18 to 19% of ingest throughput (168 to 137 rps with one worker).

A failed publish does not fail the request: the event is already durable, and the producer should not retry something that is safely stored.

## 2. Redis Streams rather than Kafka or RabbitMQ

Needed: a work queue with consumer groups, acknowledgements and redelivery of unacknowledged work. Redis Streams gives that with one small dependency that is trivial to run in Compose. Kafka would add ZooKeeper/KRaft, partition planning and heavier operations for a workload that is far below its sweet spot. RabbitMQ would also work, but Streams let one stack component serve queueing, short-lived locks (health-check de-duplication, reconciler guards) and counters.

Trade-offs accepted: Redis durability is weaker than Kafka's (mitigated by PostgreSQL being the source of truth and the reconciler), and there is no built-in delayed delivery, which is why retries are scheduled from PostgreSQL by a separate process.

## 3. HMAC signing rather than mutual TLS

Subscribers verify `HMAC-SHA256(secret, timestamp + "." + payload)`. Implementing that takes a dozen lines in any language and needs no certificate management on the subscriber side, whereas mTLS requires every subscriber to manage client-certificate trust. The secret is generated per subscriber, returned once at creation and never exposed again by the API.

Limitation: the timestamp is signed but the demo subscriber does not reject old timestamps, so replay protection on the receiving side is left to the subscriber.

## 4. At-least-once delivery and where duplicates are prevented

The gateway cannot be exactly-once: if a worker crashes after the HTTP call but before recording the attempt, the event will be redelivered. What it does guarantee in normal operation:

- Idempotent ingest (unique `idempotency_key`, with the race handled by catching `IntegrityError`).
- Fan-out skips any subscriber that already has an attempt for the event, so a re-processed stream message does not deliver twice.
- A retry is skipped if the subscriber already has a success.
- A unique constraint on `(event_id, subscriber_id, attempt_number)`.

Duplicates are therefore checked at the receiver, not just in the database: every load run recorded receiver total = unique = events. Subscribers should still deduplicate.

## 5. Retries are scheduled, not slept

A failed delivery writes a `pending_retry` row with `next_retry_at` and the worker moves on. A single scheduler polls PostgreSQL and republishes due rows to the stream. Workers never block on backoff, retry state survives restarts, and retries for one subscriber never stall another.

## 6. Circuit breaker design and its quirks

Five consecutive failed real attempts for a subscriber mark it `degraded`. While degraded:

- New events are **deferred**, not skipped: a placeholder attempt (`attempt_number = -1`) is stored and released once the subscriber is active again. Nothing is lost, and deferral does not consume the retry budget.
- Queued retries are put back to `pending_retry` so they are not orphaned.
- A periodic signed health-check request decides when to close the circuit. Replicas de-duplicate probes with a per-subscriber Redis lock.
- Manual replay is an operator override and bypasses the breaker.

Known quirks, left as they are:

- The breaker counts the subscriber's last five attempts **across events**, so two events failing in parallel can open it before either reaches its fifth attempt. Those events wait for recovery instead of being dead-lettered. Dead letters therefore mostly come from failures while the circuit is closed.
- After recovery, the last five attempts may still all be failures, so a flapping endpoint can reopen the circuit on its first failure.
- The threshold is hard-coded at 5.

## 7. Encoding state in `attempt_number`

Rather than add columns, `attempt_number` carries meaning: `1..N` real attempts, `0, -2, -3...` replays, `-1` the deferred placeholder. The unique constraint then makes repeated replays collide-free, and the breaker and statistics filter on `attempt_number > 0`. The cost is that the convention must be documented (see `delivery-states.md`); a dedicated `kind` column would be cleaner.

## 8. A lesson from load testing: blocking calls in an async handler

The first smoke run stalled. `create_event` was `async` but made blocking SQLAlchemy calls on the event loop while holding a connection across a Redis `await`. With a 15-connection pool, the pool exhausted, blocking the loop, which stalled the Redis calls that would have released connections. Fixes: run the DB work in a threadpool and release the connection before talking to Redis, enlarge the pool, add Redis socket timeouts, and make a failed publish recoverable (decision 1).

## 9. Scaling behaviour

Workers scale by adding replicas on the same consumer group (unique consumer name per replica). On one laptop, three workers delivered about 2.2x the throughput of one (31.6 to 68.9 events/s) and drained a saturated backlog in 103 s instead of 474 s. Scaling is sub-linear here because every process shares the same CPU, and each worker handles one message at a time.

## What I would change at 10x

- **Scale the retry scheduler.** It is a single instance because two would publish the same retry twice. Claim rows with `SELECT ... FOR UPDATE SKIP LOCKED` to run several.
- **True transactional outbox.** Write an outbox row in the same transaction as the event and have a relay publish it, removing the dual-write window and the reconciler heuristic.
- **Concurrent workers.** Handle several stream messages per worker with a shared HTTP client, instead of one message and one client per delivery.
- **Ingest throughput.** Multiple API processes and batching the `published_at` write.
- **Circuit breaker per endpoint and time-windowed**, with a configurable threshold and half-open probing.
- **Partition or retain data.** `delivery_attempt` grows without bound; partition by time and expire old rows.
- **Authentication and rate limiting** on the management and ingest APIs, and timestamp tolerance guidance for subscribers.