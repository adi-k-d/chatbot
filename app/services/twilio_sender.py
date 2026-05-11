import httpx

from app.observability.logging import get_logger
from app.observability.metrics import TWILIO_SEND_DURATION
import time

log = get_logger(__name__)

_TWILIO_API = "https://api.twilio.com/2010-04-01"


async def send_whatsapp(
    *,
    to: str,
    body: str,
    account_sid: str,
    auth_token: str,
    from_number: str,
    status_callback: str | None = None,
) -> str:
    """Send a WhatsApp message via Twilio REST API. Returns the MessageSid."""
    to_wa = f"whatsapp:+{to}" if not to.startswith("whatsapp:") else to
    from_wa = f"whatsapp:+{from_number.lstrip('+')}" if not from_number.startswith("whatsapp:") else from_number

    payload = {"To": to_wa, "From": from_wa, "Body": body}
    if status_callback:
        payload["StatusCallback"] = status_callback

    url = f"{_TWILIO_API}/Accounts/{account_sid}/Messages.json"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data=payload, auth=(account_sid, auth_token))
        resp.raise_for_status()
        sid = resp.json()["sid"]
        log.info("twilio_sent", to=to_wa, sid=sid)
        return sid
    finally:
        TWILIO_SEND_DURATION.observe(time.perf_counter() - t0)
