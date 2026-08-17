"""On-demand trade plan for a single symbol.

Answers "should I buy this, and where do I get out" for any instrument, using
the same `SignalEngine` and `RiskManager` the live loop and backtester use — so
an ad-hoc lookup can never disagree with what the bot would actually do.

The plan is returned even when the verdict is HOLD. Knowing the levels a trade
*would* use is what lets you set an alert and wait, rather than re-checking by
hand; a bare "no" throws away the useful half of the answer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from app.broker.base import Broker
from app.config import settings
from app.data.breeze_client import BreezeClient, BreezeError
from app.data.store import MarketStore
from app.models import DEFAULT_INTRADAY_PRODUCT, ProductType, Side, SignalAction
from app.risk.manager import RiskManager
from app.strategy import indicators
from app.strategy.signals import SignalEngine

logger = logging.getLogger(__name__)

# History to pull when a symbol has never been seen before.
COLD_START_DAYS = {
    "1minute": 10,
    "5minute": 45,
    "30minute": 180,
    "1day": 900,
}


class AnalysisError(RuntimeError):
    """The symbol could not be analysed."""


def _pct(numerator: float, denominator: float) -> float:
    return (numerator / denominator * 100) if denominator else 0.0


def analyse_symbol(
    symbol: str,
    store: MarketStore,
    engine: SignalEngine,
    risk: RiskManager,
    *,
    client: BreezeClient | None = None,
    broker: Broker | None = None,
    interval: str | None = None,
    benchmark_code: str | None = None,
    product: ProductType = DEFAULT_INTRADAY_PRODUCT,
    lot_size: int = 1,
    refresh: bool = True,
) -> dict[str, Any]:
    """Build a full trade plan for `symbol`.

    Fetches history if the symbol is unknown or stale, so any NSE code works and
    not just the configured universe.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise AnalysisError("No symbol supplied")

    interval = interval or settings.candle_interval
    benchmark_code = benchmark_code or settings.benchmark_code

    if refresh and client is not None and client.is_connected:
        _ensure_history(symbol, store, client, interval)

    frame = store.load_candles(symbol, interval, limit=500)
    warmup = indicators.warmup_period()

    if frame.empty:
        raise AnalysisError(
            f"No price data for '{symbol}'. Breeze uses its own stock codes "
            "(RELIND, not RELIANCE) — see docs/breeze-codes.md."
        )
    if len(frame) < warmup:
        raise AnalysisError(
            f"Only {len(frame)} candles for '{symbol}'; {warmup} are needed before "
            "the indicators are meaningful. Try a longer interval such as 1day."
        )

    benchmark = store.load_candles(benchmark_code, interval, limit=500)
    signal = engine.evaluate(
        frame, symbol, benchmark=benchmark if not benchmark.empty else None
    )
    if signal is None:
        raise AnalysisError(f"Could not score '{symbol}' — indicators are still warming up.")

    enriched = indicators.enrich(frame)
    last = enriched.iloc[-1]
    price = signal.price

    # For a HOLD, price the plan as a long so the levels are still concrete.
    side = Side.SELL if signal.action is SignalAction.SELL else Side.BUY

    equity = broker.equity if broker is not None else settings.starting_capital
    cash = broker.cash if broker is not None else settings.starting_capital
    open_positions = broker.open_position_count if broker is not None else 0

    sizing = risk.size_position(
        entry=price,
        atr=signal.atr,
        side=side,
        equity=equity,
        available_cash=cash,
        open_positions=open_positions,
        lot_size=lot_size,
    )

    stoploss, target = risk.stop_and_target(price, signal.atr, side)
    stop_distance = abs(price - stoploss)
    target_distance = abs(target - price)

    return {
        "symbol": symbol,
        "interval": interval,
        "as_of": signal.timestamp.isoformat(),
        "candles_used": len(frame),
        # --- verdict ---
        "action": signal.action.value,
        "side": side.value,
        "score": signal.score,
        "entry_threshold": engine.entry_threshold,
        "conviction": _conviction(signal.score, engine.entry_threshold),
        "factors": signal.factors.to_dict(),
        "reasons": signal.reasons,
        # --- the plan ---
        "plan": {
            "entry": round(price, 2),
            "stoploss": round(stoploss, 2),
            "target": round(target, 2),
            "stop_distance": round(stop_distance, 2),
            "stop_distance_pct": round(_pct(stop_distance, price), 2),
            "target_distance": round(target_distance, 2),
            "target_distance_pct": round(_pct(target_distance, price), 2),
            "risk_reward": round(risk.risk_reward_ratio(), 2),
            "quantity": sizing.quantity,
            "notional": round(sizing.notional, 2),
            "risk_amount": round(sizing.risk_amount, 2),
            "risk_pct_of_equity": round(sizing.effective_risk_pct(equity), 2),
            "approved": sizing.approved,
            "sizing_note": sizing.reason,
            "binding_constraint": sizing.binding_constraint,
            "was_capped": sizing.was_capped,
            "product": product.value,
        },
        # --- context ---
        "market": {
            "price": round(price, 2),
            "atr": round(signal.atr, 2),
            "atr_pct": round(float(last.get("atr_pct") or 0), 2),
            "rsi": round(float(last.get("rsi") or 0), 1),
            "adx": round(float(last.get("adx") or 0), 1),
            "ema_20": round(float(last.get("ema_20") or 0), 2),
            "ema_50": round(float(last.get("ema_50") or 0), 2),
            "ema_200": round(float(last.get("ema_200") or 0), 2),
            "vwap": round(float(last.get("vwap") or 0), 2),
            "volume_ratio": round(float(last.get("volume_ratio") or 0), 2),
            "above_vwap": bool(price > float(last.get("vwap") or 0)),
            "trend": _trend_label(last, price),
        },
    }


def _conviction(score: float, threshold: float) -> str:
    """Plain-language strength, so a bare number is not the only output."""
    magnitude = abs(score)
    if threshold <= 0:
        return "unknown"
    if magnitude >= threshold * 2:
        return "strong"
    if magnitude >= threshold:
        return "moderate"
    if magnitude >= threshold * 0.6:
        return "weak — below the entry threshold"
    return "negligible"


def _trend_label(last: Any, price: float) -> str:
    ema_20 = float(last.get("ema_20") or 0)
    ema_50 = float(last.get("ema_50") or 0)
    adx = float(last.get("adx") or 0)

    if not ema_20 or not ema_50:
        return "unknown"

    direction = "up" if ema_20 > ema_50 else "down"
    if adx < 20:
        return f"ranging (weak {direction}trend, ADX {adx:.0f})"
    if price > ema_20 > ema_50:
        return f"{direction}trend, price leading"
    if price < ema_20 < ema_50:
        return f"{direction}trend, price leading"
    return f"{direction}trend, price pulling back"


def _ensure_history(
    symbol: str, store: MarketStore, client: BreezeClient, interval: str
) -> None:
    """Fetch whatever history is missing for `symbol`, cheaply.

    Resumes from the newest stored bar so a repeat lookup costs one small
    request rather than a full re-download.
    """
    now = datetime.now()
    latest = store.latest_candle_time(symbol, interval)

    if latest is None:
        start = now - timedelta(days=COLD_START_DAYS.get(interval, 45))
    else:
        # Nothing to do if the newest bar is already current.
        if now - latest < timedelta(minutes=1):
            return
        start = latest - timedelta(minutes=5)

    try:
        candles = client.get_historical_data(
            stock_code=symbol, from_date=start, to_date=now, interval=interval
        )
    except BreezeError as exc:
        # A stale cache still beats failing outright.
        logger.warning("Could not refresh history for %s: %s", symbol, exc)
        return

    if candles:
        store.save_candles(symbol, candles, interval)
