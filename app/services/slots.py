from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.database import get_pool
from app.models import Provider

IST = ZoneInfo("Asia/Kolkata")


def _time_to_minutes(t: str) -> int:
    parts = str(t).split(":")
    return int(parts[0]) * 60 + int(parts[1] if len(parts) > 1 else 0)


def _minutes_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


async def get_available_slots(
    *,
    provider: Provider,
    business_id: str,
    date_str: str,  # YYYY-MM-DD
    slot_duration: int,
    buffer_time: int,
) -> list[str]:
    """
    Return a list of ISO 8601 datetime strings (IST) for available slots
    on the given date for the given provider.
    Mirrors the JS getAvailableSlotsBatch logic (DB-only, no Google Calendar).
    """
    if provider.unavailable:
        return []

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return []

    day_of_week = target_date.weekday()
    # Python: Mon=0…Sun=6; JS: Sun=0…Sat=6
    js_day = (day_of_week + 1) % 7

    # Collect schedules from each service
    time_blocks_for_date: list[dict] = []
    service_durations: list[tuple[dict, int, int]] = []  # (time_block, duration, step)

    for svc in provider.services:
        schedule = svc.get("schedule") or []
        if isinstance(schedule, str):
            import json
            try:
                schedule = json.loads(schedule)
            except Exception:
                schedule = []

        day_entry = next((s for s in schedule if s.get("day_of_week") == js_day), None)
        if not day_entry or day_entry.get("closed"):
            continue

        blocks = day_entry.get("time_blocks") or []
        if not blocks:
            continue

        duration = svc.get("slot_duration") or svc.get("duration") or slot_duration
        svc_buffer = svc.get("buffer") if svc.get("buffer") is not None else buffer_time
        step = duration + svc_buffer

        for block in blocks:
            service_durations.append((block, duration, step))

    if not service_durations:
        return []

    # Fetch existing appointments for this provider on this date
    pool = get_pool()
    day_start_ist = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=IST)
    day_end_ist   = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=IST)

    rows = await pool.fetch(
        """
        SELECT booking_at, duration_minutes
        FROM appointments
        WHERE business_id = $1
          AND service_provider_id = $2
          AND booking_at >= $3 AND booking_at <= $4
          AND status NOT IN ('cancelled', 'no_show', 'rejected', 'no_show_reschedule')
        """,
        business_id,
        provider.id,
        day_start_ist.astimezone(timezone.utc),
        day_end_ist.astimezone(timezone.utc),
    )

    booked: list[tuple[int, int]] = []  # (start_min, end_min)
    for row in rows:
        appt_ist = row["booking_at"].astimezone(IST)
        start_min = appt_ist.hour * 60 + appt_ist.minute
        dur = row["duration_minutes"] or slot_duration
        booked.append((start_min, start_min + dur))

    now_ist = datetime.now(IST)
    now_min = now_ist.hour * 60 + now_ist.minute if date_str == now_ist.strftime("%Y-%m-%d") else -1

    seen: set[str] = set()
    slots: list[str] = []

    for block, duration, step in service_durations:
        cur = _time_to_minutes(block.get("start_time", "00:00"))
        block_end = _time_to_minutes(block.get("end_time", "00:00"))

        while cur + duration <= block_end:
            slot_end = cur + duration
            hhmm = _minutes_to_hhmm(cur)

            # Skip past slots
            if now_min >= 0 and cur <= now_min:
                cur += step
                continue

            # Skip conflicting slots
            if any(cur < b_end and slot_end > b_start for b_start, b_end in booked):
                cur += step
                continue

            if hhmm not in seen:
                seen.add(hhmm)
                dt = datetime(
                    target_date.year, target_date.month, target_date.day,
                    cur // 60, cur % 60, 0, tzinfo=IST
                )
                slots.append(dt.isoformat())

            cur += step

    slots.sort()
    return slots
