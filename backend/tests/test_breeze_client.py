"""Breeze client session handling and URL construction."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.data.breeze_client import (
    MAX_CANDLES_PER_REQUEST,
    BreezeClient,
    SessionExpiredError,
    login_url,
)

# Structurally like real Breeze credentials, including the special characters
# that appear in them. Not real values.
FAKE_KEY = "6985D2l3pu%K3G55Jb!92P69u15823n1"
FAKE_SECRET = "04Y$8750S7505j93r790%399wS43#661"


@pytest.fixture
def client() -> BreezeClient:
    return BreezeClient(api_key=FAKE_KEY, api_secret=FAKE_SECRET)


# ----------------------------------------------------------------------
# Session token validation
# ----------------------------------------------------------------------
def test_missing_token_is_rejected(client):
    with pytest.raises(SessionExpiredError, match="No Breeze session token"):
        client.connect("   ")


def test_api_key_pasted_as_session_token_is_named(client):
    """Breeze answers this with an opaque 'could not authenticate credentials'.

    The key and secret sit next to each other on the Breeze app page while the
    session token only appears in a redirect URL, so this substitution is easy
    to make and worth diagnosing precisely.
    """
    with pytest.raises(SessionExpiredError, match="your API key, not the session token"):
        client.connect(FAKE_KEY)


def test_secret_pasted_as_session_token_is_named(client):
    with pytest.raises(SessionExpiredError, match="your secret key, not the session token"):
        client.connect(FAKE_SECRET)


def test_surrounding_whitespace_does_not_defeat_the_check(client):
    """A key copied from a web page often carries whitespace."""
    with pytest.raises(SessionExpiredError, match="your API key, not the session token"):
        client.connect(f"  {FAKE_KEY}  ")


def test_a_plausible_token_passes_validation_and_reaches_the_sdk(client):
    """A real-looking token must get past validation.

    breeze-connect is not installed in the test environment, so this surfaces as
    the import error — which is proof the token itself was accepted rather than
    rejected by the checks above.
    """
    with pytest.raises(Exception) as excinfo:
        client.connect("48123456")

    message = str(excinfo.value)
    assert "not the session token" not in message
    assert "No Breeze session token" not in message


def test_client_starts_disconnected(client):
    assert not client.is_connected
    assert client.session_age_hours is None


def test_calls_before_connecting_are_refused(client):
    with pytest.raises(SessionExpiredError, match="not connected"):
        client.get_quote("RELIND")


# ----------------------------------------------------------------------
# Login URL
# ----------------------------------------------------------------------
def test_login_url_encodes_special_characters():
    """An unencoded '%' or '!' in the key would corrupt the query string."""
    url = login_url(FAKE_KEY)
    assert "%25" in url  # % encoded
    assert "%21" in url  # ! encoded
    # The raw key must not appear verbatim.
    assert FAKE_KEY not in url
    assert url.startswith("https://api.icicidirect.com/apiuser/login?api_key=")


# ----------------------------------------------------------------------
# Historical backfill windowing
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "interval",
    ["1minute", "5minute", "30minute", "1day"],
)
def test_backfill_window_stays_within_the_candle_cap(interval):
    """Each window must request at most MAX_CANDLES_PER_REQUEST bars.

    Intraday candles only accrue during the 375-minute trading day, so sizing a
    window by bar duration alone would overshoot and silently truncate results.
    """
    window = BreezeClient._window_for(interval)

    if interval == "1day":
        assert window == timedelta(days=MAX_CANDLES_PER_REQUEST)
        return

    bar_minutes = {"1minute": 1, "5minute": 5, "30minute": 30}[interval]
    bars_in_window = window.days * (375 / bar_minutes)
    assert 0 < bars_in_window <= MAX_CANDLES_PER_REQUEST, (
        f"{interval}: window of {window.days}d yields {bars_in_window} bars"
    )


def test_unsupported_interval_is_rejected(client):
    from app.data.breeze_client import BreezeError

    with pytest.raises(BreezeError, match="Unsupported interval"):
        client.get_historical_data(
            "RELIND",
            from_date=datetime(2025, 1, 1),
            to_date=datetime(2025, 2, 1),
            interval="7minute",
        )


# ----------------------------------------------------------------------
# Candle parsing
# ----------------------------------------------------------------------
def test_parses_a_standard_candle_row():
    candle = BreezeClient._parse_candle(
        {
            "datetime": "2025-01-15 09:20:00",
            "open": "1000.5",
            "high": "1010",
            "low": "998.25",
            "close": "1005",
            "volume": "12345",
        }
    )
    assert candle is not None
    assert candle.timestamp == datetime(2025, 1, 15, 9, 20)
    assert candle.open == pytest.approx(1000.5)
    assert candle.volume == pytest.approx(12345)


def test_parses_a_daily_row_without_a_time_component():
    candle = BreezeClient._parse_candle(
        {"datetime": "2025-01-15", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}
    )
    assert candle is not None
    assert candle.timestamp == datetime(2025, 1, 15)


def test_rows_without_a_timestamp_are_dropped():
    assert BreezeClient._parse_candle({"open": 1, "close": 2}) is None


def test_unparseable_timestamps_are_dropped():
    assert BreezeClient._parse_candle({"datetime": "not-a-date", "close": 1}) is None


def test_missing_prices_default_to_zero_rather_than_raising():
    candle = BreezeClient._parse_candle({"datetime": "2025-01-15 09:20:00"})
    assert candle is not None
    assert candle.close == 0.0
