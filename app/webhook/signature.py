import hashlib
import hmac
import base64
from fastapi import Request


def verify_twilio_signature(
    request_url: str,
    params: dict[str, str],
    signature: str,
    auth_token: str,
) -> bool:
    """Verify Twilio HMAC-SHA1 webhook signature (timing-safe)."""
    if not signature or not auth_token:
        return False

    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    message = (request_url + sorted_params).encode("utf-8")
    mac = hmac.new(auth_token.encode("utf-8"), message, hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def get_canonical_url(request: Request) -> str:
    """Reconstruct the URL exactly as Twilio signed it, honouring proxy headers."""
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")

    if forwarded_host:
        url = f"{forwarded_proto}://{forwarded_host}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        return url

    return str(request.url)
