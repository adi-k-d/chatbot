from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BusinessConfig:
    id: str
    slug: str
    business_name: str
    phone: str | None
    slot_duration: int
    buffer_time: int
    advance_booking_days: int
    owner_id: str
    twilio_account_sid: str | None
    twilio_auth_token: str | None
    twilio_phone_number: str | None
    twilio_connection_status: str
    review_link: str | None
    # AI context
    business_context: str
    agent_prompt: str
    ai_context_extra: str
    agent_enabled: bool
    manual_override: bool
    contact_phone: str | None


@dataclass
class ThreadState:
    id: str
    user_state: dict
    metadata: dict
    inbound_count: int


@dataclass
class Provider:
    id: str
    name: str
    designation: str | None
    services: list
    is_active: bool
    unavailable: bool
