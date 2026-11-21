"""Weekday slots in America/New_York, matched to UTC cron wakes.

GitHub cron fires on the hour pattern in `.github/workflows/wake.yml`.
A persona slot is due when its jittered New York time falls in the lookback
window. Jitter is a stable hash of persona, date, and slot, so the same day
always picks the same minute and two days do not share that minute.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from harness.personas import Persona, Slot

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Documented wakes. The workflow cron is the source of the actual fires.
# 11:05 UTC through 23:35 UTC, every 30 minutes, Monday-Friday.
CRON_HOURS = range(11, 24)
CRON_MINUTES = (5, 35)
LOOKBACK_MINUTES = 40


def cron_times_utc(day: date) -> list[datetime]:
    times = []
    for hour in CRON_HOURS:
        for minute in CRON_MINUTES:
            times.append(datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC))
    return times


def jitter_minutes(persona_id: str, session: date, slot_id: str, span: int) -> int:
    digest = hashlib.sha256(f"{persona_id}|{session.isoformat()}|{slot_id}".encode("utf-8")).hexdigest()
    width = span * 2 + 1
    return (int(digest[:8], 16) % width) - span


def slot_target(persona_id: str, session: date, slot: Slot) -> datetime:
    hour, minute = (int(part) for part in slot.et.split(":"))
    base = datetime(session.year, session.month, session.day, hour, minute, tzinfo=ET)
    return base + timedelta(minutes=jitter_minutes(persona_id, session, slot.id, slot.jitter_minutes))


def session_date(now: datetime) -> date:
    return now.astimezone(ET).date()


def is_weekday(day: date) -> bool:
    return day.weekday() < 5


def due_slots(
    persona: Persona,
    now: datetime,
    *,
    lookback_minutes: int = LOOKBACK_MINUTES,
    session: date | None = None,
) -> list[tuple[Slot, datetime]]:
    """Return slots whose jittered time is in (now - lookback, now]."""
    moment = now.astimezone(UTC)
    day = session or session_date(moment)
    if not is_weekday(day):
        return []
    start = moment - timedelta(minutes=lookback_minutes)
    found: list[tuple[Slot, datetime]] = []
    for slot in persona.slots:
        target = slot_target(persona.id, day, slot).astimezone(UTC)
        if start < target <= moment:
            found.append((slot, target))
    return found
