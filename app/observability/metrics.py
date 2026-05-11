from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

WEBHOOKS_RECEIVED = Counter(
    "webhooks_received_total",
    "Total inbound webhook requests",
    ["slug"],
)

MESSAGE_DURATION = Histogram(
    "message_processing_seconds",
    "End-to-end time to process and reply to a message",
    ["slug", "outcome"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60],
)

TOOL_CALLS = Counter(
    "agent_tool_calls_total",
    "Total tool calls made by the agent",
    ["tool_name", "outcome"],
)

LLM_LATENCY = Histogram(
    "llm_api_latency_seconds",
    "Time spent waiting for Gemini API responses",
    buckets=[0.5, 1, 2, 5, 10, 20, 30],
)

TWILIO_SEND_DURATION = Histogram(
    "twilio_send_seconds",
    "Time spent sending WhatsApp messages via Twilio",
    buckets=[0.1, 0.5, 1, 2, 5],
)


def setup_metrics(app: FastAPI) -> None:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
