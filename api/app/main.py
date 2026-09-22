from fastapi import FastAPI
from app.routes.events import router as event_router
from app.routes.subscribers import router as subscriber_router

app = FastAPI(title="Webhook Reliability Gateway")

@app.get("/")
async def root():
    return {"message": "Webhook Reliability Gateway API is running."}

app.include_router(event_router)
app.include_router(subscriber_router)
