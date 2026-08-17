"""Option expiry date helpers.

These generate *candidate* expiries to offer in the UI — they are not an
authority. NSE has changed index expiry weekdays more than once (and moved some
indices to monthly-only), and trading holidays shift an expiry to the previous
session. Breeze rejects a date that is not a real contract, so the candidate list
is a convenience for picking, and the API's response is the actual check.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

# Weekday NSE currently uses for index weekly expiries (Monday = 0).
WEEKLY_EXPIRY_WEEKDAY = 3  # Thursday


def _next_weekday(start: date, weekday: int) -> date:
    days_ahead = (weekday - start.weekday()) % 7
    return start + timedelta(days=days_ahead)


def upcoming_weekly_expiries(count: int = 5, today: date | None = None) -> list[date]:
    """The next `count` weekly expiry candidates."""
    today = today or date.today()
    first = _next_weekday(today, WEEKLY_EXPIRY_WEEKDAY)
    return [first + timedelta(weeks=i) for i in range(count)]


def monthly_expiry(year: int, month: int) -> date:
    """Last expiry-weekday of the given month — the monthly contract."""
    last_day = calendar.monthrange(year, month)[1]
    cursor = date(year, month, last_day)
    while cursor.weekday() != WEEKLY_EXPIRY_WEEKDAY:
        cursor -= timedelta(days=1)
    return cursor


def upcoming_monthly_expiries(count: int = 3, today: date | None = None) -> list[date]:
    today = today or date.today()
    results: list[date] = []
    year, month = today.year, today.month

    while len(results) < count:
        candidate = monthly_expiry(year, month)
        if candidate >= today:
            results.append(candidate)
        month += 1
        if month > 12:
            month = 1
            year += 1

    return results


def expiry_candidates(today: date | None = None) -> list[dict[str, str]]:
    """Weekly and monthly candidates, de-duplicated and sorted.

    A weekly that coincides with the month's last expiry is the monthly
    contract; labelling it "monthly" is the more useful of the two.
    """
    today = today or date.today()
    monthlies = set(upcoming_monthly_expiries(3, today))

    seen: dict[date, str] = {}
    for d in upcoming_weekly_expiries(6, today):
        seen[d] = "monthly" if d in monthlies else "weekly"
    for d in monthlies:
        seen.setdefault(d, "monthly")

    return [
        {
            "date": d.isoformat(),
            "breeze_format": to_breeze_expiry(d),
            "kind": kind,
            "label": f"{d.strftime('%d %b %Y')} ({kind})",
            "days_away": (d - today).days,
        }
        for d, kind in sorted(seen.items())
    ]


def to_breeze_expiry(value: date | datetime | str) -> str:
    """Format an expiry the way Breeze expects it.

    Breeze wants ISO-8601 with milliseconds and a literal Z, at 06:00 — the
    convention its own examples use for contract dates.
    """
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "")).date()
    elif isinstance(value, datetime):
        parsed = value.date()
    else:
        parsed = value

    return f"{parsed.isoformat()}T06:00:00.000Z"
