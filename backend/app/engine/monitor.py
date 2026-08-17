"""Monitor positions held at the broker, including ones this app did not open.

The point of this module is coverage of positions bought by hand in ICICI Direct
— MTF especially. Breeze permits *reading* those positions even though it
prohibits placing MTF and Margin orders, so the app can compute an ATR stoploss
and target for a manually-bought holding, watch the live price against them, and
say when a level is reached. It cannot place the exit order for a prohibited
product; that has to be done in ICICI Direct itself.

Levels come from the same `RiskManager` the automated strategy uses, so a
hand-bought position is judged by identical rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.data.store import MarketStore
from app.models import ProductType, Side
from app.risk.manager import RiskManager
from app.strategy import indicators

logger = logging.getLogger(__name__)

# Warn before a level is actually hit, as a fraction of the entry-to-level span.
APPROACHING_FRACTION = 0.85


def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed


@dataclass(slots=True)
class BrokerPosition:
    """A position as the broker reports it, normalised."""

    symbol: str
    side: Side
    quantity: int
    entry_price: float
    product: ProductType
    last_price: float = 0.0
    raw: dict[str, Any] | None = None

    @property
    def unrealised_pnl(self) -> float:
        if not self.last_price:
            return 0.0
        return (self.last_price - self.entry_price) * self.quantity * self.side.sign

    @property
    def unrealised_pct(self) -> float:
        if not self.entry_price or not self.last_price:
            return 0.0
        return (
            (self.last_price - self.entry_price) / self.entry_price * 100 * self.side.sign
        )


# Keys Breeze may use in a positions/holdings payload.
SYMBOL_KEYS = ("stock_code", "symbol", "stock_name")
QUANTITY_KEYS = ("quantity", "net_quantity", "qty", "current_quantity")
ENTRY_KEYS = ("average_price", "avg_price", "average_cost", "price", "buy_price")
PRICE_KEYS = ("ltp", "last_traded_price", "current_market_price", "close")
PRODUCT_KEYS = ("product_type", "product", "segment")
SIDE_KEYS = ("action", "side", "buy_sell")


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def normalise_broker_position(payload: dict[str, Any]) -> BrokerPosition | None:
    """Turn one Breeze positions/holdings row into a `BrokerPosition`.

    Field names differ between the positions and holdings endpoints and across
    segments, so every field is looked up through a list of candidates. A row
    without a symbol or with zero net quantity is not a position.
    """
    symbol = _first(payload, SYMBOL_KEYS)
    if not symbol:
        return None

    quantity = int(_num(_first(payload, QUANTITY_KEYS)))
    if quantity == 0:
        return None

    # A negative quantity is Breeze's way of expressing a short; some payloads
    # instead carry an explicit action field.
    side = Side.BUY
    raw_side = _first(payload, SIDE_KEYS)
    if raw_side and str(raw_side).strip().lower() in {"sell", "s", "short"}:
        side = Side.SELL
    elif quantity < 0:
        side = Side.SELL

    return BrokerPosition(
        symbol=str(symbol).strip().upper(),
        side=side,
        quantity=abs(quantity),
        entry_price=_num(_first(payload, ENTRY_KEYS)),
        product=ProductType.from_breeze(_first(payload, PRODUCT_KEYS)),
        last_price=_num(_first(payload, PRICE_KEYS)),
        raw=payload,
    )


def latest_atr(store: MarketStore, symbol: str, interval: str) -> float:
    """ATR for a symbol from stored candles, or 0 when there is not enough data."""
    frame = store.load_candles(symbol, interval, limit=250)
    if frame.empty or len(frame) < 20:
        return 0.0

    series = indicators.atr(frame["high"], frame["low"], frame["close"], 14).dropna()
    return float(series.iloc[-1]) if not series.empty else 0.0


def describe_position(
    position: BrokerPosition,
    risk: RiskManager,
    store: MarketStore,
    interval: str,
    live_price: float | None = None,
) -> dict[str, Any]:
    """Attach computed stop/target levels and their status to a broker position."""
    price = live_price or position.last_price or position.entry_price
    position.last_price = price

    atr = latest_atr(store, position.symbol, interval)

    payload: dict[str, Any] = {
        "symbol": position.symbol,
        "side": position.side.value,
        "quantity": position.quantity,
        "entry_price": round(position.entry_price, 2),
        "last_price": round(price, 2),
        "product": position.product.value,
        "product_label": product_label(position.product),
        "leveraged": position.product.is_leveraged,
        "exitable_via_api": position.product.placeable_via_api,
        "unrealised_pnl": round(position.unrealised_pnl, 2),
        "unrealised_pct": round(position.unrealised_pct, 2),
        "value": round(price * position.quantity, 2),
        "atr": round(atr, 2),
    }

    if atr <= 0 or position.entry_price <= 0:
        payload["levels_available"] = False
        payload["note"] = (
            "Not enough stored history to compute ATR levels. Add this symbol to "
            "EQUITY_UNIVERSE, or analyse it once so its candles are cached."
        )
        return payload

    stoploss, target = risk.stop_and_target(position.entry_price, atr, position.side)
    status, distance_pct = _level_status(position, price, stoploss, target)

    payload.update(
        {
            "levels_available": True,
            "stoploss": round(stoploss, 2),
            "target": round(target, 2),
            "stop_distance_pct": round(
                abs(price - stoploss) / price * 100 if price else 0.0, 2
            ),
            "target_distance_pct": round(
                abs(target - price) / price * 100 if price else 0.0, 2
            ),
            "risk_reward": round(risk.risk_reward_ratio(), 2),
            "risk_amount": round(abs(position.entry_price - stoploss) * position.quantity, 2),
            "status": status,
            "progress_pct": distance_pct,
        }
    )

    if not position.product.placeable_via_api:
        payload["note"] = (
            f"Breeze cannot place {payload['product_label']} orders via API — "
            "these levels are for monitoring; exit in ICICI Direct."
        )

    return payload


def product_label(product: ProductType) -> str:
    return {
        ProductType.DELIVERY: "Delivery (cash)",
        ProductType.MARGIN: "Margin (intraday)",
        ProductType.MTF: "MTF",
        ProductType.OPTIONS: "Options",
        ProductType.FUTURES: "Futures",
    }.get(product, product.value)


def _level_status(
    position: BrokerPosition, price: float, stoploss: float, target: float
) -> tuple[str, float]:
    """Classify where price sits between the stop and the target.

    `progress_pct` is 0 at the stop and 100 at the target, so a single number
    conveys how the trade is doing against its own plan.
    """
    if position.side is Side.BUY:
        if price <= stoploss:
            return "stop_hit", 0.0
        if price >= target:
            return "target_hit", 100.0
        span = target - stoploss
        progress = (price - stoploss) / span * 100 if span else 0.0
    else:
        if price >= stoploss:
            return "stop_hit", 0.0
        if price <= target:
            return "target_hit", 100.0
        span = stoploss - target
        progress = (stoploss - price) / span * 100 if span else 0.0

    progress = max(0.0, min(100.0, progress))

    if progress >= APPROACHING_FRACTION * 100:
        return "approaching_target", round(progress, 1)
    if progress <= (1 - APPROACHING_FRACTION) * 100:
        return "approaching_stop", round(progress, 1)
    return "open", round(progress, 1)


def collect(
    rows: list[dict[str, Any]],
    risk: RiskManager,
    store: MarketStore,
    interval: str,
    live_prices: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Normalise and describe every broker row, skipping non-positions."""
    live_prices = live_prices or {}
    described: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in rows:
        position = normalise_broker_position(row)
        if position is None:
            continue

        # The positions and holdings endpoints can both report the same holding.
        key = (position.symbol, position.product.value)
        if key in seen:
            continue
        seen.add(key)

        described.append(
            describe_position(
                position,
                risk,
                store,
                interval,
                live_price=live_prices.get(position.symbol),
            )
        )

    return described


def alerts_from(described: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Positions that have reached or are nearing a level."""
    interesting = {"stop_hit", "target_hit", "approaching_stop", "approaching_target"}
    now = datetime.now().isoformat()

    return [
        {
            "symbol": p["symbol"],
            "status": p["status"],
            "last_price": p["last_price"],
            "stoploss": p.get("stoploss"),
            "target": p.get("target"),
            "exitable_via_api": p["exitable_via_api"],
            "timestamp": now,
        }
        for p in described
        if p.get("status") in interesting
    ]
