"""Live ticker normalisation and expiry helpers."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.data.expiry import (
    WEEKLY_EXPIRY_WEEKDAY,
    expiry_candidates,
    monthly_expiry,
    to_breeze_expiry,
    upcoming_monthly_expiries,
    upcoming_weekly_expiries,
)
from app.data.ticker import LiveTicker, Quote


class FakeClient:
    """Stands in for BreezeClient; no network."""

    def __init__(self, connected: bool = True) -> None:
        self.is_connected = connected
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.stream_started = False
        self.quotes: dict[str, dict] = {}

    def start_stream(self, on_tick):
        self.stream_started = True
        self.on_tick = on_tick

    def subscribe(self, stock_code, **_):
        self.subscribed.append(stock_code)

    def unsubscribe(self, stock_code, **_):
        self.unsubscribed.append(stock_code)

    def stop_stream(self):
        self.stream_started = False

    def get_quote(self, stock_code, **_):
        return self.quotes.get(stock_code, {})


@pytest.fixture
def ticker() -> LiveTicker:
    # Constructed without start() so no polling thread runs during the test.
    return LiveTicker(FakeClient(), ["TCS", "RELIND"])


# ----------------------------------------------------------------------
# Quote arithmetic
# ----------------------------------------------------------------------
def test_change_is_measured_against_previous_close():
    quote = Quote(symbol="TCS", price=110.0, previous_close=100.0)
    assert quote.change == pytest.approx(10.0)
    assert quote.change_pct == pytest.approx(10.0)


def test_change_is_zero_without_a_reference():
    """A missing previous close must not produce a nonsense percentage."""
    quote = Quote(symbol="TCS", price=110.0)
    assert quote.change == 0.0
    assert quote.change_pct == 0.0


def test_negative_change_is_reported():
    quote = Quote(symbol="TCS", price=90.0, previous_close=100.0)
    assert quote.change_pct == pytest.approx(-10.0)


# ----------------------------------------------------------------------
# Tick attribution
# ----------------------------------------------------------------------
def test_tick_matched_by_stock_code(ticker):
    ticker._on_tick({"stock_code": "TCS", "last": 3500.5})
    assert ticker.prices()["TCS"] == pytest.approx(3500.5)


def test_tick_matched_case_insensitively(ticker):
    ticker._on_tick({"stock_code": "tcs", "ltp": 3400})
    assert "TCS" in ticker.prices()


def test_tick_matched_through_a_decorated_identifier(ticker):
    """Breeze may send 'NSE:TCS' or 'TCS-EQ' rather than the bare code."""
    ticker._on_tick({"symbol": "NSE:TCS", "last": 3450})
    assert ticker.prices()["TCS"] == pytest.approx(3450)


def test_unattributable_tick_is_dropped_not_guessed(ticker):
    """Attributing a price to the wrong stock is worse than dropping it."""
    ticker._on_tick({"symbol": "4.1!2885", "last": 1500})
    assert ticker.prices() == {}


def test_single_symbol_watchlist_needs_no_identifier():
    solo = LiveTicker(FakeClient(), ["TCS"])
    solo._on_tick({"last": 3500})
    assert solo.prices()["TCS"] == pytest.approx(3500)


def test_tick_without_a_price_is_ignored(ticker):
    ticker._on_tick({"stock_code": "TCS"})
    assert ticker.prices() == {}


def test_zero_and_negative_prices_are_rejected(ticker):
    ticker._on_tick({"stock_code": "TCS", "last": 0})
    ticker._on_tick({"stock_code": "TCS", "last": -5})
    assert ticker.prices() == {}


def test_malformed_payloads_never_raise(ticker):
    """The handler runs on the SDK's thread — raising would kill the stream."""
    for payload in (None, "string", 42, [], {"stock_code": "TCS", "last": "abc"}):
        ticker._on_tick(payload)
    assert ticker.prices() == {}


def test_reference_fields_survive_a_bare_tick(ticker):
    """Streamed ticks often carry only the price; the open/high must persist."""
    ticker._on_tick(
        {"stock_code": "TCS", "last": 3500, "close": 3400, "open": 3450, "high": 3510}
    )
    ticker._on_tick({"stock_code": "TCS", "last": 3505})

    quote = next(q for q in ticker.quotes() if q["symbol"] == "TCS")
    assert quote["price"] == pytest.approx(3505)
    assert quote["previous_close"] == pytest.approx(3400)
    assert quote["open"] == pytest.approx(3450)


def test_high_only_ratchets_upward(ticker):
    ticker._on_tick({"stock_code": "TCS", "last": 3500, "high": 3600})
    ticker._on_tick({"stock_code": "TCS", "last": 3400, "high": 3450})
    quote = next(q for q in ticker.quotes() if q["symbol"] == "TCS")
    assert quote["high"] == pytest.approx(3600)


def test_watchlist_replacement_resubscribes():
    client = FakeClient()
    live = LiveTicker(client, ["TCS"])
    live._stream_active = True  # pretend the stream is up

    live.set_symbols(["RELIND", "INFTEC"])
    assert live.symbols == ["RELIND", "INFTEC"]
    assert "TCS" in client.unsubscribed
    assert set(client.subscribed) == {"RELIND", "INFTEC"}


def test_status_reports_the_feed_state(ticker):
    status = ticker.status()
    assert status["streaming"] is False
    assert status["symbols"] == ["TCS", "RELIND"]
    assert "poll_interval_seconds" in status


# ----------------------------------------------------------------------
# Expiry helpers
# ----------------------------------------------------------------------
def test_weekly_expiries_land_on_the_expiry_weekday():
    for d in upcoming_weekly_expiries(5, today=date(2026, 8, 17)):
        assert d.weekday() == WEEKLY_EXPIRY_WEEKDAY


def test_weekly_expiries_are_ordered_and_future():
    today = date(2026, 8, 17)
    dates = upcoming_weekly_expiries(5, today=today)
    assert dates == sorted(dates)
    assert all(d >= today for d in dates)


def test_monthly_expiry_is_the_last_expiry_weekday_of_the_month():
    from datetime import timedelta

    result = monthly_expiry(2026, 8)
    assert result.weekday() == WEEKLY_EXPIRY_WEEKDAY
    assert result.month == 8
    # Nothing later in the month shares that weekday.
    assert (result + timedelta(days=7)).month != 8


def test_monthly_expiries_roll_across_a_year_boundary():
    dates = upcoming_monthly_expiries(4, today=date(2026, 11, 20))
    assert len(dates) == 4
    assert dates == sorted(dates)
    assert any(d.year == 2027 for d in dates)


def test_candidates_are_deduplicated_and_labelled():
    candidates = expiry_candidates(today=date(2026, 8, 17))
    dates = [c["date"] for c in candidates]

    assert len(dates) == len(set(dates)), "duplicate expiry offered"
    assert dates == sorted(dates)
    assert all(c["kind"] in {"weekly", "monthly"} for c in candidates)
    assert all(c["days_away"] >= 0 for c in candidates)


def test_candidate_carries_the_breeze_format():
    candidate = expiry_candidates(today=date(2026, 8, 17))[0]
    assert candidate["breeze_format"].endswith("T06:00:00.000Z")


def test_breeze_expiry_format_accepts_several_input_types():
    expected = "2026-08-20T06:00:00.000Z"
    assert to_breeze_expiry(date(2026, 8, 20)) == expected
    assert to_breeze_expiry(datetime(2026, 8, 20, 15, 30)) == expected
    assert to_breeze_expiry("2026-08-20") == expected
