from datetime import datetime
from zoneinfo import ZoneInfo
from app.models import BusinessConfig

IST = ZoneInfo("Asia/Kolkata")

_MEDICAL_BLOCK = """\

HARD LIMITS — never break these:
- Do NOT diagnose symptoms, interpret lab results, or give any medical opinion.
- Do NOT recommend medications, dosages, or treatments.
- Do NOT comment on whether a symptom is serious or not.
- If the patient asks a clinical or medical question, respond exactly like this:
  "I'm not able to provide medical advice. Please call the clinic at {contact} or raise your concern during your appointment with the doctor."
- You MAY describe what services the clinic offers (e.g. "We offer general consultations and skin care treatments") but never give clinical opinions on individual cases.
"""

_DEFAULT_PERSONA = (
    "You are a friendly and professional WhatsApp assistant for {business_name}. "
    "You help patients book, reschedule, or cancel appointments and answer general questions "
    "about the clinic. Be concise — WhatsApp messages should be short and easy to read. "
    "Use line breaks generously. Avoid walls of text."
)


def build_system_prompt(config: BusinessConfig) -> str:
    now = datetime.now(IST).strftime("%A, %d %B %Y, %I:%M %p IST")
    contact = config.contact_phone or config.phone or "the clinic"
    booking_url = f"https://formalert.in/book/{config.slug}"

    persona = (
        config.agent_prompt.strip()
        if config.agent_prompt.strip()
        else _DEFAULT_PERSONA.format(business_name=config.business_name)
    )

    sections = [persona, f"Current date/time: {now}.", f"Online booking: {booking_url}"]

    if config.review_link:
        sections.append(f"Google review link: {config.review_link}")

    if config.business_context.strip():
        sections.append(
            f"\n--- CLINIC INFORMATION ---\n{config.business_context.strip()}\n--- END ---"
        )

    if config.ai_context_extra.strip():
        sections.append(
            f"\n--- ADDITIONAL NOTES ---\n{config.ai_context_extra.strip()}\n--- END ---"
        )

    sections.append(_MEDICAL_BLOCK.format(contact=contact))

    return "\n".join(sections)
