"""Weekday slots in America/New_York, matched to UTC cron wakes.

GitHub cron is scheduled in `.github/workflows/wake.yml`, but Actions often
delays or drops those runs. A wake therefore files every slot whose jittered
New York time has already passed, not only the slots inside one lookback
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


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th ``weekday`` (Monday is 0) of a month. ``n=-1`` is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    following = date(year + (month // 12), month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(day: date) -> date:
    """NYSE rule: Saturday holidays close Friday, Sunday holidays close Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def market_holidays(year: int) -> set[date]:
    """Full-day NYSE closures for ``year``.

    Early closes (the day after Thanksgiving, Christmas Eve) are still
    trading days and are not listed. New Year's Day on a Saturday is not
    moved back into the prior year, which matches NYSE practice.
    """
    days = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _observed(date(year, 6, 19)),  # Juneteenth
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:
        days.add(_observed(new_year))
    return days


def is_market_holiday(day: date) -> bool:
    return day in market_holidays(day.year)


def is_trading_day(day: date) -> bool:
    """A weekday when US equity markets are open for at least part of the day."""
    return is_weekday(day) and not is_market_holiday(day)


def due_slots(
    persona: Persona,
    now: datetime,
    *,
    lookback_minutes: int = LOOKBACK_MINUTES,
    session: date | None = None,
) -> list[tuple[Slot, datetime]]:
    """Return slots whose jittered time is in (now - lookback, now].

    The scheduled wake does not use this window. GitHub drops cron fires, so
    the wake files ``passed_slots`` instead. This helper still describes the
    cron grid in ``.github/workflows/wake.yml``.
    """
    moment = now.astimezone(UTC)
    day = session or session_date(moment)
    if not is_trading_day(day):
        return []
    start = moment - timedelta(minutes=lookback_minutes)
    found: list[tuple[Slot, datetime]] = []
    for slot in persona.slots:
        target = slot_target(persona.id, day, slot).astimezone(UTC)
        if start < target <= moment:
            found.append((slot, target))
    return found


def previous_trading_day(day: date) -> date:
    """The trading day before ``day``. Weekends and NYSE holidays are skipped."""
    cursor = day - timedelta(days=1)
    while not is_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def passed_slots(persona: Persona, now: datetime, session: date) -> list[tuple[Slot, datetime]]:
    """Slots whose jittered New York time is at or before ``now``.

    A slot still in the future is omitted. Weekends and market holidays have no session.
    """
    moment = now.astimezone(UTC)
    if not is_trading_day(session):
        return []
    found: list[tuple[Slot, datetime]] = []
    for slot in persona.slots:
        target = slot_target(persona.id, session, slot).astimezone(UTC)
        if target <= moment:
            found.append((slot, target))
    return found


def ledger_accepts_report_date(session: date, *, today: date) -> bool:
    """Whether this wake may put ``session`` on a ledger report.

    The ledger stores the date in the branch (``report/YYYY-MM-DD-...``) and
    under ``data/YYYY-MM-DD/``. ``validate_report.py`` and the scope check
    accept any real calendar day in that shape. They do not accept a weekend
    or a value that is not a calendar day. This wake also refuses a future
    session and anything older than the previous trading day. That one missed
    weekday is the short catch-up, and it stays inside what the ledger accepts.
    """
    if not is_trading_day(session) or session > today:
        return False
    return session >= previous_trading_day(today)
