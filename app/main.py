import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routers import clients, domains
from app.services.docker_events import start_event_listener

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(start_event_listener())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="GTM Provisioning API", version="1.0.0", lifespan=lifespan)
app.include_router(clients.router)
app.include_router(domains.router)


@app.get("/healthz")
def health():
    return {"status": "ok"}
