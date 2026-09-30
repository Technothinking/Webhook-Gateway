import hashlib
import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="Webhook Demo Subscriber")


WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
OUTAGE_MODE = False


@app.get("/")
def health_check():
    return {
        "service": "demo-subscriber",
        "status": "running"
    }


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    if not WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="WEBHOOK_SECRET is not configured"
        )

    if OUTAGE_MODE:
        raise HTTPException(
            status_code=500,
            detail="Demo subscriber is currently in outage mode"
        )

    if not x_webhook_signature:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Webhook-Signature"
        )

    if not x_webhook_timestamp:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Webhook-Timestamp"
        )

    payload = await request.body()

    signed_payload = (
        f"{x_webhook_timestamp}."
        f"{payload.decode('utf-8')}"
    )

    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        expected_signature,
        x_webhook_signature
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature"
        )

    print(
        f"Webhook received successfully: "
        f"timestamp={x_webhook_timestamp}, "
        f"payload={payload.decode('utf-8')}"
    )

    return {
        "status": "received",
        "signature_valid": True
    }


@app.post("/toggle-outage")
def toggle_outage():
    global OUTAGE_MODE

    OUTAGE_MODE = not OUTAGE_MODE

    return {
        "outage_mode": OUTAGE_MODE
    }