import os
import uuid

from locust import FastHttpUser, LoadTestShape, constant_throughput, task

EVENT_TYPE = os.environ["LOAD_EVENT_TYPE"]
TARGET_RPS = int(os.getenv("LOAD_TARGET_RPS", "50"))
RAMP_SECONDS = int(os.getenv("LOAD_RAMP_S", "20"))
HOLD_SECONDS = int(os.getenv("LOAD_HOLD_S", "60"))

RUN = uuid.uuid4().hex[:8]


class Producer(FastHttpUser):
    wait_time = constant_throughput(1)  # 1 event/sec per user

    @task
    def send_event(self):
        with self.client.post(
            "/events/",
            json={
                "event_type": EVENT_TYPE,
                "payload": {"load_run": RUN, "seq": uuid.uuid4().hex, "amount": 499},
                "idempotency_key": f"{RUN}-{uuid.uuid4().hex}",
            },
            name="POST /events",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"HTTP {response.status_code}")


class RampAndHold(LoadTestShape):
    reset_done = False

    def tick(self):
        t = self.get_run_time()

        if t >= RAMP_SECONDS + HOLD_SECONDS:
            return None

        if t < RAMP_SECONDS:
            users = max(1, int(TARGET_RPS * t / RAMP_SECONDS))
            return users, max(1.0, TARGET_RPS / RAMP_SECONDS)

        if not self.reset_done:
            # Report only the steady-state hold phase
            self.runner.stats.reset_all()
            self.reset_done = True

        return TARGET_RPS, TARGET_RPS