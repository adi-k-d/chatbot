import asyncio
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response

from app.cache import get_cached_business, set_cached_business
from app.config import settings
from app.database import get_pool
from app.models import BusinessConfig
from app.observability.logging import get_logger
from app.observability.metrics import (
    MESSAGE_DURATION, TOOL_CALLS, WEBHOOKS_RECEIVED,
)
from app.services.threads import update_thread_stage, upsert_thread
from app.services.twilio_sender import send_whatsapp
from app.webhook.signature import get_canonical_url, verify_twilio_signature

log = get_logger(__name__)
router = APIRouter()


# ── Phone normalisation ───────────────────────────────────────────────────────

def _normalise(phone: str) -> str:
    phone = phone.replace("whatsapp:", "").replace("+", "").strip()
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return "91" + digits
    return digits


# ── Business config loader (DB + cache) ───────────────────────────────────────

async def _load_business(slug: str) -> BusinessConfig | None:
    cached = get_cached_business(slug)
    if cached:
        return cached

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            b.id, b.slug, b.business_name, b.phone,
            b.slot_duration, b.buffer_time, b.advance_booking_days,
            b.owner_id, b.review_link,
            b.twilio_account_sid, b.twilio_auth_token,
            b.twilio_phone_number, b.twilio_connection_status,
            COALESCE(ai.business_context, '')  AS business_context,
            COALESCE(ai.agent_prompt, '')      AS agent_prompt,
            COALESCE(ai.ai_context_extra, '')  AS ai_context_extra,
            COALESCE(ai.agent_enabled, false)  AS agent_enabled,
            COALESCE(ai.manual_override, false) AS manual_override,
            ai.contact_phone
        FROM businesses b
        LEFT JOIN business_ai_context ai ON ai.business_id = b.id
        WHERE b.slug = $1 AND b.is_active = true
        """,
        slug,
    )
    if not row:
        return None

    config = BusinessConfig(
        id=str(row["id"]),
        slug=row["slug"],
        business_name=row["business_name"] or slug,
        phone=row["phone"],
        slot_duration=row["slot_duration"] or 30,
        buffer_time=row["buffer_time"] or 0,
        advance_booking_days=row["advance_booking_days"] or 30,
        owner_id=str(row["owner_id"]),
        twilio_account_sid=row["twilio_account_sid"],
        twilio_auth_token=row["twilio_auth_token"],
        twilio_phone_number=row["twilio_phone_number"],
        twilio_connection_status=row["twilio_connection_status"] or "disconnected",
        review_link=row["review_link"],
        business_context=row["business_context"],
        agent_prompt=row["agent_prompt"],
        ai_context_extra=row["ai_context_extra"],
        agent_enabled=bool(row["agent_enabled"]),
        manual_override=bool(row["manual_override"]),
        contact_phone=row["contact_phone"],
    )
    set_cached_business(slug, config)
    return config


# ── Message storage ───────────────────────────────────────────────────────────

async def _store_message(
    *,
    thread_id: str,
    business_id: str,
    message_sid: str,
    direction: str,
    body: str | None,
    button_payload: str | None = None,
    message_type: str | None = None,
    appointment_id: str | None = None,
) -> None:
    pool = get_pool()
    status = "received" if direction == "inbound" else "sent"
    await pool.execute(
        """
        INSERT INTO whatsapp_messages (
            id, thread_id, business_id, message_sid, direction,
            message_body, button_payload, message_type, status,
            appointment_id, created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1, $2, $3, $4,
            $5, $6, $7, $8,
            $9, NOW(), NOW()
        )
        ON CONFLICT (message_sid) DO NOTHING
        """,
        thread_id, business_id, message_sid, direction,
        body, button_payload, message_type, status,
        appointment_id,
    )


async def _is_duplicate(message_sid: str) -> bool:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id FROM whatsapp_messages WHERE message_sid = $1 LIMIT 1",
        message_sid,
    )
    return row is not None


# ── Background message processor ──────────────────────────────────────────────

async def _process_message(
    *,
    slug: str,
    config: BusinessConfig,
    thread_id: str,
    business_id: str,
    customer_phone: str,
    message_body: str,
    message_sid: str,
    user_state: dict,
) -> None:
    # Import here to avoid circular imports at module load time
    from app.agent.runner import run_agent

    bound_log = log.bind(slug=slug, customer_phone=customer_phone, message_sid=message_sid)
    t0 = time.perf_counter()
    outcome = "success"

    try:
        if not config.agent_enabled:
            bound_log.info("agent_disabled")
            return

        if config.manual_override or user_state.get("manual_override"):
            bound_log.info("manual_override_active")
            return

        # History from DB — not JSONB, fixes the write-amplification bug
        pool = get_pool()
        history_rows = await pool.fetch(
            """
            SELECT direction, message_body
            FROM whatsapp_messages
            WHERE thread_id = $1
              AND message_body IS NOT NULL
              AND direction IN ('inbound', 'outbound')
            ORDER BY created_at DESC
            LIMIT 20
            """,
            thread_id,
        )
        history = list(reversed([dict(r) for r in history_rows]))

        context = {
            "business_id": business_id,
            "customer_phone": customer_phone,
            "slot_duration": config.slot_duration,
            "buffer_time": config.buffer_time,
            "slug": slug,
        }

        bound_log.info("calling_agent", history_len=len(history))
        reply = await run_agent(
            user_message=message_body,
            history=history,
            config=config,
            context=context,
        )

        if not reply:
            contact = config.contact_phone or config.phone or "the clinic"
            reply = (
                f"Sorry, I'm having trouble right now. 😔\n\n"
                f"Please book online: {settings.base_url}/book/{config.slug}\n"
                + (f"Or call us: {contact}" if contact else "")
            )

        # Send reply
        outbound_sid = await send_whatsapp(
            to=customer_phone,
            body=reply,
            account_sid=config.twilio_account_sid,
            auth_token=config.twilio_auth_token,
            from_number=config.twilio_phone_number,
            status_callback=f"{settings.base_url.rstrip('/')}/webhook/{slug}/callback"
            if settings.environment == "production"
            else None,
        )

        # Store outbound message
        await _store_message(
            thread_id=thread_id,
            business_id=business_id,
            message_sid=outbound_sid,
            direction="outbound",
            body=reply,
            message_type="ai_response",
        )

        # Update conversation stage
        stage = "BOOKING_IN_PROGRESS" if any(
            kw in reply.lower() for kw in ["book", "slot", "appointment", "available"]
        ) else "AI_REPLIED"
        await update_thread_stage(thread_id, stage)

        bound_log.info("reply_sent", sid=outbound_sid, stage=stage, reply_len=len(reply))

    except Exception:
        outcome = "error"
        bound_log.exception("process_message_failed")
    finally:
        MESSAGE_DURATION.labels(slug=slug, outcome=outcome).observe(
            time.perf_counter() - t0
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/webhook/{slug}/incoming")
async def incoming(slug: str, request: Request) -> Response:
    WEBHOOKS_RECEIVED.labels(slug=slug).inc()

    raw_body = await request.body()
    params = {k: v[0] for k, v in parse_qs(raw_body.decode()).items()}

    message_sid = params.get("MessageSid", "")
    sender = params.get("From", "").replace("whatsapp:", "")
    receiver = params.get("To", "").replace("whatsapp:", "")
    body = params.get("Body", "").strip()
    button_payload = params.get("ButtonPayload", "").strip() or None

    bound_log = log.bind(slug=slug, message_sid=message_sid, sender=sender)

    if not message_sid or not sender:
        bound_log.warning("missing_fields")
        return Response(status_code=400)

    config = await _load_business(slug)
    if not config:
        bound_log.warning("business_not_found")
        return Response(status_code=200)

    if config.twilio_connection_status != "connected":
        bound_log.warning("twilio_not_connected", status=config.twilio_connection_status)
        return Response(status_code=200)

    # Verify Twilio HMAC signature; try with and without trailing slash
    canonical_url = get_canonical_url(request)
    sig = request.headers.get("x-twilio-signature", "")
    valid = verify_twilio_signature(canonical_url, params, sig, config.twilio_auth_token)
    if not valid:
        alt = canonical_url.rstrip("/") + "/" if not canonical_url.endswith("/") else canonical_url.rstrip("/")
        valid = verify_twilio_signature(alt, params, sig, config.twilio_auth_token)
    if not valid:
        bound_log.error("signature_invalid", url=canonical_url)
        return Response(status_code=403)

    # Dedup — unique index on message_sid handles concurrent retries
    if await _is_duplicate(message_sid):
        bound_log.info("duplicate_skipped")
        return Response(status_code=200)

    customer_phone = _normalise(sender)
    business_phone = _normalise(receiver or config.twilio_phone_number or "")

    # Atomic thread upsert — SET count = count + 1, no read-modify-write race
    thread = await upsert_thread(
        business_id=config.id,
        customer_number=customer_phone,
        business_number=business_phone,
        is_inbound=True,
    )

    # Store inbound message
    await _store_message(
        thread_id=thread.id,
        business_id=config.id,
        message_sid=message_sid,
        direction="inbound",
        body=button_payload or body or "[media]",
        button_payload=button_payload,
    )

    # Provider button actions (accept/reject/review) are handled by the Next.js app
    if button_payload:
        bound_log.info("button_payload_skipped", payload=button_payload)
        return Response(status_code=200)

    if not body:
        bound_log.info("empty_body_skipped")
        return Response(status_code=200)

    # Fire-and-forget — Twilio gets its 200 before any AI work begins
    asyncio.create_task(
        _process_message(
            slug=slug,
            config=config,
            thread_id=thread.id,
            business_id=config.id,
            customer_phone=customer_phone,
            message_body=body,
            message_sid=message_sid,
            user_state=thread.user_state,
        )
    )

    return Response(status_code=200)


@router.post("/webhook/{slug}/callback")
async def delivery_callback(slug: str, request: Request) -> Response:
    """Twilio delivery status callbacks — update whatsapp_messages.status."""
    raw_body = await request.body()
    params = {k: v[0] for k, v in parse_qs(raw_body.decode()).items()}
    message_sid = params.get("MessageSid", "")
    status = params.get("MessageStatus", "")

    if message_sid and status:
        pool = get_pool()
        await pool.execute(
            "UPDATE whatsapp_messages SET status = $1, updated_at = NOW() WHERE message_sid = $2",
            status,
            message_sid,
        )
        log.info("delivery_status", sid=message_sid, status=status, slug=slug)

    return Response(status_code=200)


@router.get("/health")
async def health() -> dict:
    try:
        await get_pool().fetchval("SELECT 1")
        return {"status": "ok", "database": "ok"}
    except Exception as exc:
        return {"status": "degraded", "database": str(exc)}
