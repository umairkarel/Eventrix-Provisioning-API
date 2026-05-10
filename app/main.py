from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.routers import clients, domains


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title="GTM Provisioning API", version="1.0.0", lifespan=lifespan)
app.include_router(clients.router)
app.include_router(domains.router)


@app.get("/healthz")
def health():
    return {"status": "ok"}
