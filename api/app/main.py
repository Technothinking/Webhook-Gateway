from fastapi import FastAPI
from app.routes.events import eventRouter

app = FastAPI(title="Webhook Reliability Gateway")

@app.get("/")
async def root():
    return {"message": "Webhook Reliability Gateway API is running."}

app.include_router(eventRouter)