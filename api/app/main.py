from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.events import router as event_router
from app.routes.subscribers import router as subscriber_router
from app.routes.dead_letters import router as dead_letter_router
from app.routes.delivery_attempts import router as delivery_attempt_router
from app.routes.stats import router as stats_router

app = FastAPI(title="Webhook Reliability Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Webhook Reliability Gateway API is running."}

app.include_router(event_router)
app.include_router(subscriber_router)
app.include_router(dead_letter_router)
app.include_router(delivery_attempt_router)
app.include_router(stats_router)