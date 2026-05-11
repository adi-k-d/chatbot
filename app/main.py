from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import settings
from app.database import init_pool, close_pool
from app.observability.logging import setup_logging
from app.observability.tracing import setup_tracing
from app.observability.metrics import setup_metrics
from app.webhook.router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_pool()
    yield
    await close_pool()


app = FastAPI(
    title="FormAlert Booking Agent",
    description="AI booking agent for Business-plan WhatsApp webhooks",
    version="1.0.0",
    lifespan=lifespan,
)

setup_tracing(app)
setup_metrics(app)

app.include_router(router)
