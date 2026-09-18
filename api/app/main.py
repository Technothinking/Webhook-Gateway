from fastapi import FastAPI

app = FastAPI(title="Webhook Reliability Gateway")

@app.get("/")
async def root():
    return {"message": "Webhook Reliability Gateway API is running."}