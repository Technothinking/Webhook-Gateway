# Load test results

**Environment.** One Windows laptop running Docker Desktop. The API (a single uvicorn process), Postgres, Redis, the demo receiver, the workers, the retry scheduler and Locust all share the same machine.

**Method.** Locust runs closed-loop (one user sends one event per second) with a 30 s ramp and a 120 s hold. Ingest figures cover the hold phase only. Delivery rate is delivered events divided by the time from the first event created to the last success. Rows marked ≈ are derived from the report timestamps, and the rest are measured directly.

| Run | Workers | Target rps | Ingest rps | Ingest p50 / p99 | Events | Delivery /s | Drain after ingest |
|---|---|---|---|---|---|---|---|
| 1 | 3 | 50 | 49.8 | 120 ms / 260 ms | 6,756 | ≈39 | 23 s |
| 2 | 3 | 100 | 100.3 | 490 ms / 700 ms | 13,532 | ≈59 | 78 s |
| 3 | 3 | 200 | 152.0 | 1.3 s / 1.7 s | 21,152 | ≈67 | 167 s |
| 4 | 3 | 400 | 145.5 | 2.7 s / 3.5 s | 21,578 | ≈67 | 171 s |
| 5 | 1 | 400 | 168.3 | 2.3 s / 3.2 s | 24,309 | ≈30 | 664 s |
| 6 | 3 | 400 | 117.8 | 3.4 s / 4.8 s | 17,645 | 68.9 | 103 s |
| 7 | 1 | 400 | 137.3 | 2.9 s / 3.3 s | 19,783 | 31.6 | 474 s |

Runs 1 to 5 ran before the `published_at` change and runs 6 and 7 ran after it.

**Correctness.** About 125,000 events across 7 runs: zero lost, and zero duplicate deliveries (receiver total = unique = events in every run). Stream messages processed equal events in runs 6 and 7.

**Findings.**
- Ingest saturates at about 150 events/s. At a 400 rps target the API achieves only about 120 to 170 rps and shows the excess as latency, with zero errors.
- The `published_at` write costs about 18 to 19% of ingest throughput (168 to 137 rps with 1 worker, 145.5 to 117.8 with 3).
- One worker delivers about 32 events/s, and three deliver about 69/s (2.2× the throughput). Draining the saturated backlog took 474 s with one worker and 103 s with three.
- Going from 1 to 3 workers lowered ingest (137 to 118 rps) because all processes compete for the same laptop CPU.
- Unloaded (20 rps smoke run, 361 events): end-to-end p50 168 ms, p95 345 ms, p99 628 ms.
- End-to-end latency in the saturated runs is queue wait, so it is not a latency figure.