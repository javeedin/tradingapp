"""Wrapper around the official `breeze-connect` SDK.

Everything ICICI-specific lives here so the rest of the system talks in plain
domain objects. The three Breeze quirks this file exists to absorb:

1. The session token expires every day, so `generate_session` must be re-run each
   morning with a token the user fetches by hand from the Breeze login page.
2. `get_historical_data_v2` returns at most 1000 candles per call, so any real
   backfill has to be walked in windows.
3. The SDK is synchronous and raises bare exceptions, so calls are wrapped and
   normalised into `BreezeError`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

from app.config import settings
from app.models import Candle

logger = logging.getLogger(__name__)

LOGIN_URL = "https://api.icicidirect.com/apiuser/login?api_key={api_key}"

# Max candles Breeze returns per historical request.
MAX_CANDLES_PER_REQUEST = 1000

# Approximate bar durations, used to size backfill windows.
INTERVAL_DURATION: dict[str, timedelta] = {
    "1second": timedelta(seconds=1),
    "1minute": timedelta(minutes=1),
    "5minute": timedelta(minutes=5),
    "30minute": timedelta(minutes=30),
    "1day": timedelta(days=1),
}

# NSE trades 09:15–15:30 IST = 375 minutes/day. Used to convert a candle budget
# into a wall-clock window for intraday intervals, which only tick during
# market hours.
TRADING_MINUTES_PER_DAY = 375


class BreezeError(RuntimeError):
    """Any failure originating from the Breeze API or SDK."""


class SessionExpiredError(BreezeError):
    """The daily session token is missing, expired, or rejected."""


def login_url(api_key: str | None = None) -> str:
    """The URL the user opens each morning to mint a fresh session token."""
    key = api_key or settings.breeze_api_key
    return LOGIN_URL.format(api_key=quote_plus(key))


def _to_breeze_datetime(dt: datetime) -> str:
    """Breeze expects ISO-8601 with milliseconds and a literal Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class BreezeClient:
    """Thin, thread-safe facade over `BreezeConnect`."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.breeze_api_key
        self.api_secret = api_secret or settings.breeze_api_secret
        self._breeze: Any = None
        self._lock = threading.Lock()
        self._session_generated_at: datetime | None = None
        self._ws_connected = False

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._breeze is not None and self._session_generated_at is not None

    @property
    def session_age_hours(self) -> float | None:
        if self._session_generated_at is None:
            return None
        return (datetime.now() - self._session_generated_at).total_seconds() / 3600

    def connect(self, session_token: str | None = None) -> None:
        """Create the SDK client and generate a session.

        `session_token` must come from the daily Breeze login page. Raises
        `SessionExpiredError` if it is missing or rejected.
        """
        token = (session_token or settings.breeze_session_token).strip()
        if not token:
            raise SessionExpiredError(
                "No Breeze session token. Fetch today's token from "
                f"{login_url()} and submit it via the dashboard or BREEZE_SESSION_TOKEN."
            )
        if not self.api_key or not self.api_secret:
            raise BreezeError("BREEZE_API_KEY and BREEZE_API_SECRET must both be set.")

        try:
            from breeze_connect import BreezeConnect
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise BreezeError(
                "breeze-connect is not installed. Run: pip install breeze-connect"
            ) from exc

        with self._lock:
            try:
                client = BreezeConnect(api_key=self.api_key)
                client.generate_session(api_secret=self.api_secret, session_token=token)
            except Exception as exc:  # SDK raises bare Exception subclasses
                raise SessionExpiredError(f"Breeze session generation failed: {exc}") from exc

            self._breeze = client
            self._session_generated_at = datetime.now()

        logger.info("Breeze session established")

    def _require_client(self) -> Any:
        if self._breeze is None:
            raise SessionExpiredError("Breeze client is not connected. Call connect() first.")
        return self._breeze

    def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        """Invoke an SDK method and normalise the response envelope.

        Breeze returns ``{"Success": ..., "Status": 200, "Error": None}``; a
        non-200 status or a populated Error field is turned into an exception so
        callers never have to inspect the envelope themselves.
        """
        client = self._require_client()
        fn: Callable[..., dict[str, Any]] = getattr(client, method, None)
        if fn is None:
            raise BreezeError(f"Breeze SDK has no method '{method}'")

        try:
            response = fn(**kwargs)
        except Exception as exc:
            raise BreezeError(f"{method} raised: {exc}") from exc

        if not isinstance(response, dict):
            raise BreezeError(f"{method} returned unexpected payload: {type(response)!r}")

        status = response.get("Status")
        error = response.get("Error")
        if error:
            text = str(error).lower()
            if "session" in text or "authenticat" in text or "token" in text:
                raise SessionExpiredError(f"{method}: {error}")
            raise BreezeError(f"{method}: {error}")
        if status is not None and int(status) not in (200, 0):
            raise BreezeError(f"{method} returned status {status}: {response}")

        return response

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------
    def get_historical_data(
        self,
        stock_code: str,
        from_date: datetime,
        to_date: datetime,
        interval: str | None = None,
        exchange_code: str = "NSE",
        product_type: str = "cash",
        **instrument_kwargs: Any,
    ) -> list[Candle]:
        """Fetch OHLCV candles, transparently paging around the 1000-candle cap.

        Extra `instrument_kwargs` (expiry_date, right, strike_price) are passed
        through for derivatives.
        """
        interval = interval or settings.candle_interval
        if interval not in INTERVAL_DURATION:
            raise BreezeError(f"Unsupported interval '{interval}'")

        candles: list[Candle] = []
        seen: set[datetime] = set()
        window = self._window_for(interval)
        cursor = from_date

        while cursor < to_date:
            chunk_end = min(cursor + window, to_date)
            batch = self._fetch_history_chunk(
                stock_code=stock_code,
                from_date=cursor,
                to_date=chunk_end,
                interval=interval,
                exchange_code=exchange_code,
                product_type=product_type,
                **instrument_kwargs,
            )
            for candle in batch:
                # Windows are inclusive at both ends, so boundary bars repeat.
                if candle.timestamp not in seen:
                    seen.add(candle.timestamp)
                    candles.append(candle)

            # Always advance, even on an empty batch (holidays, halts, no data).
            cursor = chunk_end + INTERVAL_DURATION[interval]

        candles.sort(key=lambda c: c.timestamp)
        logger.info(
            "Fetched %d %s candles for %s (%s to %s)",
            len(candles),
            interval,
            stock_code,
            from_date.date(),
            to_date.date(),
        )
        return candles

    @staticmethod
    def _window_for(interval: str) -> timedelta:
        """Wall-clock span that yields at most MAX_CANDLES_PER_REQUEST bars.

        Intraday bars only accrue during the 375-minute trading day, so a naive
        `bar_duration * 1000` would overshoot badly and silently truncate.
        """
        if interval == "1day":
            return timedelta(days=MAX_CANDLES_PER_REQUEST)

        bar_minutes = INTERVAL_DURATION[interval].total_seconds() / 60
        bars_per_day = max(TRADING_MINUTES_PER_DAY / bar_minutes, 1)
        days = max(int(MAX_CANDLES_PER_REQUEST / bars_per_day), 1)
        return timedelta(days=days)

    def _fetch_history_chunk(
        self,
        stock_code: str,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        exchange_code: str,
        product_type: str,
        **instrument_kwargs: Any,
    ) -> list[Candle]:
        params: dict[str, Any] = {
            "interval": interval,
            "from_date": _to_breeze_datetime(from_date),
            "to_date": _to_breeze_datetime(to_date),
            "stock_code": stock_code,
            "exchange_code": exchange_code,
            "product_type": product_type,
        }
        params.update(instrument_kwargs)

        response = self._call("get_historical_data_v2", **params)
        rows = response.get("Success") or []
        return [c for c in (self._parse_candle(r) for r in rows) if c is not None]

    @staticmethod
    def _parse_candle(row: dict[str, Any]) -> Candle | None:
        raw_ts = row.get("datetime") or row.get("date")
        if not raw_ts:
            return None
        timestamp = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
            try:
                timestamp = datetime.strptime(str(raw_ts), fmt)
                break
            except ValueError:
                continue
        if timestamp is None:
            logger.warning("Unparseable candle timestamp: %r", raw_ts)
            return None

        return Candle(
            timestamp=timestamp,
            open=_as_float(row.get("open")),
            high=_as_float(row.get("high")),
            low=_as_float(row.get("low")),
            close=_as_float(row.get("close")),
            volume=_as_float(row.get("volume")),
        )

    # ------------------------------------------------------------------
    # Quotes & instruments
    # ------------------------------------------------------------------
    def get_quote(
        self,
        stock_code: str,
        exchange_code: str = "NSE",
        product_type: str = "cash",
        **instrument_kwargs: Any,
    ) -> dict[str, Any]:
        response = self._call(
            "get_quotes",
            stock_code=stock_code,
            exchange_code=exchange_code,
            product_type=product_type,
            **instrument_kwargs,
        )
        rows = response.get("Success") or []
        return rows[0] if rows else {}

    def get_last_price(self, stock_code: str, exchange_code: str = "NSE") -> float | None:
        quote = self.get_quote(stock_code, exchange_code=exchange_code)
        ltp = quote.get("ltp") or quote.get("last_traded_price")
        return _as_float(ltp) if ltp is not None else None

    def get_option_chain(
        self,
        stock_code: str,
        expiry_date: str,
        right: str = "call",
        exchange_code: str = "NFO",
    ) -> list[dict[str, Any]]:
        """Option chain for one expiry. `right` is 'call', 'put', or 'others'."""
        response = self._call(
            "get_option_chain_quotes",
            stock_code=stock_code,
            exchange_code=exchange_code,
            product_type="options",
            expiry_date=expiry_date,
            right=right,
        )
        return response.get("Success") or []

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def get_funds(self) -> dict[str, Any]:
        return self._call("get_funds").get("Success") or {}

    def get_positions(self) -> list[dict[str, Any]]:
        return self._call("get_portfolio_positions").get("Success") or []

    def get_holdings(self) -> list[dict[str, Any]]:
        return self._call("get_portfolio_holdings").get("Success") or []

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def place_order(self, **params: Any) -> dict[str, Any]:
        return self._call("place_order", **params).get("Success") or {}

    def cancel_order(self, order_id: str, exchange_code: str = "NSE") -> dict[str, Any]:
        return self._call(
            "cancel_order", order_id=order_id, exchange_code=exchange_code
        ).get("Success") or {}

    def modify_order(self, **params: Any) -> dict[str, Any]:
        return self._call("modify_order", **params).get("Success") or {}

    def get_order_detail(self, order_id: str, exchange_code: str = "NSE") -> dict[str, Any]:
        response = self._call(
            "get_order_detail", order_id=order_id, exchange_code=exchange_code
        )
        rows = response.get("Success") or []
        return rows[0] if rows else {}

    def get_order_list(
        self, exchange_code: str, from_date: datetime, to_date: datetime
    ) -> list[dict[str, Any]]:
        response = self._call(
            "get_order_list",
            exchange_code=exchange_code,
            from_date=_to_breeze_datetime(from_date),
            to_date=_to_breeze_datetime(to_date),
        )
        return response.get("Success") or []

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    def start_stream(self, on_tick: Callable[[dict[str, Any]], None]) -> None:
        client = self._require_client()
        if self._ws_connected:
            return
        try:
            client.ws_connect()
            client.on_ticks = on_tick
            self._ws_connected = True
            logger.info("Breeze websocket connected")
        except Exception as exc:
            raise BreezeError(f"Websocket connect failed: {exc}") from exc

    def subscribe(
        self,
        stock_code: str,
        exchange_code: str = "NSE",
        product_type: str = "cash",
        interval: str | None = None,
        **instrument_kwargs: Any,
    ) -> None:
        client = self._require_client()
        params: dict[str, Any] = {
            "exchange_code": exchange_code,
            "stock_code": stock_code,
            "product_type": product_type,
            "get_exchange_quotes": True,
            "get_market_depth": False,
        }
        if interval:
            params["interval"] = interval
        params.update(instrument_kwargs)
        try:
            client.subscribe_feeds(**params)
        except Exception as exc:
            raise BreezeError(f"subscribe_feeds failed for {stock_code}: {exc}") from exc

    def unsubscribe(
        self, stock_code: str, exchange_code: str = "NSE", product_type: str = "cash"
    ) -> None:
        client = self._require_client()
        try:
            client.unsubscribe_feeds(
                exchange_code=exchange_code,
                stock_code=stock_code,
                product_type=product_type,
                get_exchange_quotes=True,
                get_market_depth=False,
            )
        except Exception as exc:
            logger.warning("unsubscribe_feeds failed for %s: %s", stock_code, exc)

    def stop_stream(self) -> None:
        if self._breeze is None or not self._ws_connected:
            return
        try:
            self._breeze.ws_disconnect()
        except Exception as exc:
            logger.warning("Websocket disconnect failed: %s", exc)
        finally:
            self._ws_connected = False
