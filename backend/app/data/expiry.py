"""Option expiry date helpers.

These generate *candidate* expiries to offer in the UI — they are not an
authority. Trading holidays shift an expiry to the previous session, and NSE has
changed the rules twice in recent memory:

* **Expiry weekday moved from Thursday to Tuesday**, effective 28 August 2025,
  for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 and single-stock
  derivatives.
* **Weekly expiries were restricted to one benchmark index per exchange.** On
  NSE that is NIFTY; BANKNIFTY weeklies were discontinued in November 2024, so
  it and the other indices are monthly/quarterly only.

Because these rules move, `WEEKLY_EXPIRY_INDICES` is the single place to correct
if NSE changes them again, and Breeze's acceptance of a date remains the real
check.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

# Weekday NSE uses for derivative expiries (Monday = 0). Tuesday since 2025-08-28.
WEEKLY_EXPIRY_WEEKDAY = 1  # Tuesday

# Underlyings that still have weekly contracts. Everything else is monthly only.
# Breeze codes; NIFTY is the only NSE index with weeklies.
WEEKLY_EXPIRY_INDICES = frozenset({"NIFTY"})


def has_weekly_expiries(symbol: str | None) -> bool:
    """Whether `symbol` has weekly contracts, or monthly only."""
    return bool(symbol) and symbol.strip().upper() in WEEKLY_EXPIRY_INDICES


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


def expiry_candidates(
    today: date | None = None, symbol: str | None = None
) -> list[dict[str, str]]:
    """Candidate expiries for `symbol`, de-duplicated and sorted.

    Only underlyings in `WEEKLY_EXPIRY_INDICES` get weekly candidates. Offering
    weeklies for an index that no longer has them just produces dates Breeze
    rejects with "No Data Found". A weekly coinciding with the month's last
    expiry *is* the monthly contract, so it is labelled that way.

    The weekday is included in the label because the expiry day changed in 2025
    — seeing "Tue" makes an out-of-date build obvious at a glance.
    """
    today = today or date.today()
    monthlies = set(upcoming_monthly_expiries(3, today))

    seen: dict[date, str] = {}
    if symbol is None or has_weekly_expiries(symbol):
        for d in upcoming_weekly_expiries(6, today):
            seen[d] = "monthly" if d in monthlies else "weekly"
    for d in monthlies:
        seen.setdefault(d, "monthly")

    return [
        {
            "date": d.isoformat(),
            "breeze_format": to_breeze_expiry(d),
            "kind": kind,
            "label": f"{d.strftime('%a %d %b %Y')} ({kind})",
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
