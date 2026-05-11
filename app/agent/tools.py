from datetime import datetime
from zoneinfo import ZoneInfo

import google.generativeai as genai

from app.database import get_pool
from app.models import Provider
from app.observability.logging import get_logger
from app.services.slots import get_available_slots

IST = ZoneInfo("Asia/Kolkata")
log = get_logger(__name__)

# ── Tool schemas sent to Gemini ───────────────────────────────────────────────

TOOL_DEFINITIONS = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="list_providers",
            description=(
                "List available doctors or service providers at this clinic. "
                "Call this when the patient wants to book or asks who is available."
            ),
            parameters=genai.protos.Schema(type=genai.protos.Type.OBJECT, properties={}),
        ),
        genai.protos.FunctionDeclaration(
            name="get_available_slots",
            description=(
                "Get open appointment slots for a specific provider on a given date. "
                "Call this after the patient has chosen a provider and a date."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "provider_id": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="UUID of the provider",
                    ),
                    "date": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Date in YYYY-MM-DD format (IST)",
                    ),
                },
                required=["provider_id", "date"],
            ),
        ),
        genai.protos.FunctionDeclaration(
            name="get_patient_appointments",
            description=(
                "Get the patient's upcoming and recent appointments at this clinic. "
                "Call this when the patient asks about their bookings."
            ),
            parameters=genai.protos.Schema(type=genai.protos.Type.OBJECT, properties={}),
        ),
        genai.protos.FunctionDeclaration(
            name="book_appointment",
            description=(
                "Book a new appointment. Always confirm provider, date, time, and patient name "
                "with the patient before calling this tool."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "provider_id": genai.protos.Schema(type=genai.protos.Type.STRING),
                    "provider_name": genai.protos.Schema(type=genai.protos.Type.STRING),
                    "booking_datetime": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="ISO 8601 datetime in IST e.g. 2026-05-15T10:30:00+05:30",
                    ),
                    "service_name": genai.protos.Schema(type=genai.protos.Type.STRING),
                    "service_id": genai.protos.Schema(type=genai.protos.Type.STRING),
                    "duration_minutes": genai.protos.Schema(type=genai.protos.Type.INTEGER),
                    "customer_name": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Patient's full name",
                    ),
                },
                required=["provider_id", "provider_name", "booking_datetime", "customer_name"],
            ),
        ),
        genai.protos.FunctionDeclaration(
            name="reschedule_appointment",
            description=(
                "Move an existing appointment to a new date and time. "
                "Confirm the new time with the patient before calling this."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "appointment_id": genai.protos.Schema(type=genai.protos.Type.STRING),
                    "new_datetime": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="New ISO 8601 datetime in IST",
                    ),
                },
                required=["appointment_id", "new_datetime"],
            ),
        ),
        genai.protos.FunctionDeclaration(
            name="cancel_appointment",
            description=(
                "Cancel an existing appointment. "
                "Ask the patient to confirm before calling this."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "appointment_id": genai.protos.Schema(type=genai.protos.Type.STRING),
                    "reason": genai.protos.Schema(type=genai.protos.Type.STRING),
                },
                required=["appointment_id"],
            ),
        ),
    ]
)


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def execute_tool(name: str, tool_input: dict, context: dict) -> str:
    try:
        match name:
            case "list_providers":
                return await _list_providers(context)
            case "get_available_slots":
                return await _get_available_slots(tool_input, context)
            case "get_patient_appointments":
                return await _get_patient_appointments(context)
            case "book_appointment":
                return await _book_appointment(tool_input, context)
            case "reschedule_appointment":
                return await _reschedule_appointment(tool_input, context)
            case "cancel_appointment":
                return await _cancel_appointment(tool_input, context)
            case _:
                return f"Unknown tool: {name}"
    except Exception as exc:
        log.error("tool_error", tool=name, error=str(exc))
        return f"Error running {name}: {exc}"


# ── Implementations ───────────────────────────────────────────────────────────

async def _list_providers(context: dict) -> str:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, name, designation, services, unavailable
        FROM service_providers
        WHERE business_id = $1 AND is_active = true
        ORDER BY name
        """,
        context["business_id"],
    )
    if not rows:
        return "No providers found for this clinic."

    lines = []
    for row in rows:
        status = " ⚠️ unavailable today" if row["unavailable"] else ""
        desig = f" ({row['designation']})" if row["designation"] else ""
        services = row["services"] or []
        svc_names = [s.get("name") for s in services if s.get("name")]
        svc_str = f"\n  Services: {', '.join(svc_names)}" if svc_names else ""
        lines.append(f"• {row['name']}{desig}{status}{svc_str}\n  ID: {row['id']}")

    return "Available providers:\n\n" + "\n\n".join(lines)


async def _get_available_slots(tool_input: dict, context: dict) -> str:
    pool = get_pool()
    provider_id = tool_input["provider_id"]
    date_str = tool_input["date"]

    row = await pool.fetchrow(
        """
        SELECT id, name, services, unavailable, unavailable_till
        FROM service_providers
        WHERE id = $1 AND business_id = $2 AND is_active = true
        """,
        provider_id,
        context["business_id"],
    )
    if not row:
        return "Provider not found."

    provider = Provider(
        id=str(row["id"]),
        name=row["name"],
        designation=None,
        services=list(row["services"] or []),
        is_active=True,
        unavailable=bool(row["unavailable"]),
    )

    slots = await get_available_slots(
        provider=provider,
        business_id=context["business_id"],
        date_str=date_str,
        slot_duration=context["slot_duration"],
        buffer_time=context["buffer_time"],
    )

    if not slots:
        return f"No slots available for {row['name']} on {date_str}."

    formatted = [datetime.fromisoformat(s).strftime("%I:%M %p") for s in slots[:12]]
    return f"Available slots for {row['name']} on {date_str}:\n" + ", ".join(formatted)


async def _get_patient_appointments(context: dict) -> str:
    pool = get_pool()
    digits = "".join(c for c in context["customer_phone"] if c.isdigit())

    rows = await pool.fetch(
        """
        SELECT id, booking_at, status, service_name, provider_name,
               location, payment_status
        FROM appointments
        WHERE business_id = $1
          AND regexp_replace(customer_phone, '[^0-9]', '', 'g') LIKE $2
          AND status NOT IN ('cancelled', 'rejected')
        ORDER BY booking_at DESC
        LIMIT 5
        """,
        context["business_id"],
        f"%{digits[-10:]}",
    )

    if not rows:
        return "No appointments found for this number at this clinic."

    lines = []
    for row in rows:
        dt = row["booking_at"].astimezone(IST).strftime("%a %d %b %Y at %I:%M %p")
        lines.append(
            f"• {dt}\n"
            f"  Service: {row['service_name'] or 'Appointment'}\n"
            f"  Provider: {row['provider_name'] or '—'}\n"
            f"  Status: {row['status']}  |  Payment: {row['payment_status'] or '—'}\n"
            f"  ID: {row['id']}"
        )

    return "Your appointments:\n\n" + "\n\n".join(lines)


async def _book_appointment(tool_input: dict, context: dict) -> str:
    pool = get_pool()

    try:
        booking_at = datetime.fromisoformat(tool_input["booking_datetime"])
        if booking_at.tzinfo is None:
            booking_at = booking_at.replace(tzinfo=IST)
    except ValueError:
        return "Invalid datetime format. Use ISO 8601 e.g. 2026-05-15T10:30:00+05:30"

    day_start = datetime(booking_at.year, booking_at.month, booking_at.day, 0, 0, 0, tzinfo=IST)
    day_end = datetime(booking_at.year, booking_at.month, booking_at.day, 23, 59, 59, tzinfo=IST)
    duration = tool_input.get("duration_minutes") or context["slot_duration"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Determine token position (sequential slot number within the day)
            count_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM appointments
                WHERE business_id = $1
                  AND booking_at >= $2 AND booking_at <= $3
                  AND token IS NOT NULL
                  AND booking_at < $4
                """,
                context["business_id"], day_start, day_end, booking_at,
            )
            token = (count_row["cnt"] or 0) + 1

            # Shift later tokens to make room
            await conn.execute(
                """
                UPDATE appointments SET token = token + 1
                WHERE business_id = $1
                  AND booking_at >= $2 AND booking_at <= $3
                  AND token >= $4
                """,
                context["business_id"], day_start, day_end, token,
            )

            row = await conn.fetchrow(
                """
                INSERT INTO appointments (
                    id, business_id, service_provider_id, provider_name,
                    service_id, service_name,
                    customer_name, customer_phone,
                    booking_at, status, booking_type, duration_minutes, token,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7,
                    $8, 'pending', 'online', $9, $10,
                    NOW(), NOW()
                )
                RETURNING id
                """,
                context["business_id"],
                tool_input.get("provider_id"),
                tool_input.get("provider_name"),
                tool_input.get("service_id"),
                tool_input.get("service_name", "Consultation"),
                tool_input["customer_name"],
                context["customer_phone"],
                booking_at,
                duration,
                token,
            )
            appt_id = str(row["id"])

            # Upsert patient CRM record
            await conn.execute(
                """
                INSERT INTO patients (id, business_id, phone, name, created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
                ON CONFLICT (business_id, phone) DO UPDATE
                  SET name = EXCLUDED.name, updated_at = NOW()
                """,
                context["business_id"],
                context["customer_phone"],
                tool_input["customer_name"],
            )

    dt_str = booking_at.astimezone(IST).strftime("%A, %d %B %Y at %I:%M %p IST")
    return (
        f"✅ Appointment booked!\n\n"
        f"📅 {dt_str}\n"
        f"👨‍⚕️ {tool_input.get('provider_name', 'TBD')}\n"
        f"💼 {tool_input.get('service_name', 'Consultation')}\n"
        f"🔖 Ref: {appt_id}"
    )


async def _reschedule_appointment(tool_input: dict, context: dict) -> str:
    pool = get_pool()

    try:
        new_dt = datetime.fromisoformat(tool_input["new_datetime"])
        if new_dt.tzinfo is None:
            new_dt = new_dt.replace(tzinfo=IST)
    except ValueError:
        return "Invalid datetime format."

    result = await pool.execute(
        """
        UPDATE appointments
        SET booking_at = $1, status = 'pending', updated_at = NOW()
        WHERE id = $2
          AND business_id = $3
          AND status NOT IN ('cancelled', 'completed', 'completed_no_review')
        """,
        new_dt,
        tool_input["appointment_id"],
        context["business_id"],
    )

    if result == "UPDATE 0":
        return "Appointment not found or cannot be rescheduled (already completed or cancelled)."

    dt_str = new_dt.astimezone(IST).strftime("%A, %d %B %Y at %I:%M %p IST")
    return f"✅ Appointment rescheduled to {dt_str}. Status reset to pending for confirmation."


async def _cancel_appointment(tool_input: dict, context: dict) -> str:
    pool = get_pool()

    result = await pool.execute(
        """
        UPDATE appointments
        SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
        WHERE id = $1
          AND business_id = $2
          AND status NOT IN ('cancelled', 'completed', 'completed_no_review')
        """,
        tool_input["appointment_id"],
        context["business_id"],
    )

    if result == "UPDATE 0":
        return "Appointment not found or already cancelled/completed."

    return "✅ Appointment cancelled."
