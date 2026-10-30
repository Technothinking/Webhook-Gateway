# Delivery states and conventions

No schema change was needed for the Week 3 Day 5 fixes. The behaviour is encoded in existing columns.

## `delivery_attempt.status`

| Status | Meaning |
| --- | --- |
| `success` | 2xx received |
| `pending_retry` | Failed; waiting for `next_retry_at` (backoff) |
| `deferred` | Parked because the subscriber was degraded when the event arrived; picked up by the retry scheduler once the subscriber is `active` |
| `retry_queued` | The scheduler published it to the stream; a worker is about to process it |
| `failed` | Final failure (retries exhausted, or a failed replay) |

## `delivery_attempt.attempt_number`

The unique key is `(event_id, subscriber_id, attempt_number)`.

| Value | Use |
| --- | --- |
| `1..N` | Real delivery attempts; consume the retry budget (`N = len(RETRY_DELAYS)`) |
| `0`, `-2`, `-3`, ... | Manual replays (first replay is 0, later ones go down), so replaying repeatedly never collides |
| `-1` | The deferred placeholder; does not consume the retry budget |

The circuit breaker only looks at real attempts (`attempt_number > 0`).

## `event.status` (derived after every delivery step)

| Status | Meaning |
| --- | --- |
| `pending` | Accepted, not processed yet |
| `no_subscribers` | No active subscription matched |
| `retrying` | At least one subscriber has not received it yet (backoff, deferred, in flight) |
| `dead_lettered` | At least one undelivered subscriber was dead-lettered |
| `delivered` | Every targeted subscriber has a success |

## Subscriber state and what happens to events

| Subscriber | New event | Queued retry | Replay |
| --- | --- | --- | --- |
| `active` | delivered | delivered | delivered |
| `degraded` | **deferred** (not lost) | put back to `pending_retry`/`deferred` | delivered (operator override) |
| `paused` | not delivered | put back until resumed | delivered |

## Safety nets

* Fan-out skips a subscriber that already has an attempt for the event, so a re-processed stream message never double-delivers.
* A retry for an event that already has a `success` for that subscriber is skipped.
* Stream messages that fail with an unexpected error stay pending and are reclaimed after `RECLAIM_MIN_IDLE_MS` (`XAUTOCLAIM`), up to `MAX_DELIVERIES_PER_MESSAGE`, then dropped with a log line.
* The consumer group is created at `0`, so events published before the first worker started are still processed.