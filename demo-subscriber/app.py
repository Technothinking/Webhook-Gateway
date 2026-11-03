
import hashlib
import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="Webhook Demo Subscriber")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
HEALTHY_WEBHOOK_SECRET = os.getenv("HEALTHY_WEBHOOK_SECRET")
OUTAGE_MODE = False


@app.get("/")
def health_check():
    return {
        "service": "demo-subscriber",
        "status": "running"
    }

async def verify_webhook_signature(
    request: Request,
    x_webhook_signature: str | None,
    x_webhook_timestamp: str | None,
    secret: str | None,
) -> str:
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Webhook secret is not configured"
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
        secret.encode("utf-8"),
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

    return payload.decode("utf-8")


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    if OUTAGE_MODE:
        raise HTTPException(
            status_code=500,
            detail="Demo subscriber is currently in outage mode"
        )

    payload = await verify_webhook_signature(
        request,
        x_webhook_signature,
        x_webhook_timestamp,
        WEBHOOK_SECRET,
    )

    print(
        f"Webhook received successfully: "
        f"timestamp={x_webhook_timestamp}, "
        f"payload={payload}"
    )

    return {
        "status": "received",
        "signature_valid": True
    }


@app.post("/healthy-webhook")
async def receive_healthy_webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    x_webhook_timestamp: str | None = Header(default=None),
):
    payload = await verify_webhook_signature(
        request,
        x_webhook_signature,
        x_webhook_timestamp,
        HEALTHY_WEBHOOK_SECRET,
    )
    print(
        f"HEALTHY WEBHOOK RECEIVED: "
        f"timestamp={x_webhook_timestamp}, "
        f"payload={payload}"
    )

    return {
        "status": "received",
        "signature_valid": True,
        "receiver": "healthy"
    }


@app.get("/outage")
def get_outage():
    return {"outage_mode": OUTAGE_MODE}


@app.post("/toggle-outage")
def toggle_outage():
    global OUTAGE_MODE

    OUTAGE_MODE = not OUTAGE_MODE

    return {
        "outage_mode": OUTAGE_MODE
    }
