"""Multi-factor signal engine.

Turns "read the market and decide" into something explicit and testable. Five
factors each score the bar in [-1, +1]; they are combined into a weighted
composite, and the market regime then scales that composite up or down rather
than merely adding to it.

Regime is a multiplier, not another addend, on purpose. A strong stock-level
buy signal during a broad market downtrend is not "slightly less good" — it is
categorically riskier, because index drawdowns drag correlated equities down
regardless of their own setup. Multiplying lets a hostile regime veto a trade
that additive weighting would still wave through.

Every score carries the per-factor breakdown and human-readable reasons, so a
trade on the dashboard can always be traced back to why it fired.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from app.config import settings
from app.models import FactorScores, Signal, SignalAction
from app.strategy import indicators

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FactorWeights:
    """Relative influence of each factor. Normalised at use, so these need not sum to 1."""

    trend: float = 0.40
    momentum: float = 0.25
    volume: float = 0.15
    volatility: float = 0.20

    def normalised(self) -> FactorWeights:
        total = self.trend + self.momentum + self.volume + self.volatility
        if total <= 0:
            raise ValueError("Factor weights must sum to a positive number")
        return FactorWeights(
            trend=self.trend / total,
            momentum=self.momentum / total,
            volume=self.volume / total,
            volatility=self.volatility / total,
        )


# --- Tunables -----------------------------------------------------------------
ADX_TRENDING = 25.0  # above this, a directional trend is established
ADX_RANGING = 20.0  # below this, the market is chopping
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
ATR_PCT_CALM = 0.5  # below this the instrument is too quiet to pay costs
ATR_PCT_ELEVATED = 3.0  # above this, position sizing gets dangerous
VOLUME_CONFIRM = 1.2  # volume must beat its average by this to confirm
HOSTILE_REGIME_DAMPING = 0.35  # multiplier when the signal fights the regime
NEUTRAL_REGIME_DAMPING = 0.75  # multiplier when the regime is undecided


def _squash(value: float, scale: float) -> float:
    """Map an unbounded value into (-1, 1) via tanh.

    Bounding matters: without it a single extreme factor (a volatility spike, a
    volume blowout) would dominate the composite and override the other four.
    """
    if scale <= 0 or not math.isfinite(value):
        return 0.0
    return float(np.tanh(value / scale))


def _safe(value: object, default: float = 0.0) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


class SignalEngine:
    """Scores instruments and emits BUY / SELL / HOLD decisions."""

    def __init__(
        self,
        weights: FactorWeights | None = None,
        entry_threshold: float | None = None,
        exit_threshold: float | None = None,
        allow_shorts: bool = True,
    ) -> None:
        self.weights = (weights or FactorWeights()).normalised()
        self.entry_threshold = (
            entry_threshold if entry_threshold is not None else settings.signal_entry_threshold
        )
        self.exit_threshold = (
            exit_threshold if exit_threshold is not None else settings.signal_exit_threshold
        )
        self.allow_shorts = allow_shorts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(
        self,
        df: pd.DataFrame,
        symbol: str,
        benchmark: pd.DataFrame | None = None,
        enrich: bool = True,
    ) -> Signal | None:
        """Score the most recent bar. Returns None if there is not enough history.

        `df` must hold OHLCV columns. Pass `enrich=False` if indicators are
        already attached (the backtester does this once up front rather than
        recomputing on every bar).
        """
        if df is None or df.empty:
            return None

        data = indicators.enrich(df) if enrich else df
        if len(data) < 2:
            return None

        row = data.iloc[-1]
        if pd.isna(row.get("atr")) or pd.isna(row.get("ema_50")):
            return None  # still inside the indicator warm-up window

        prev = data.iloc[-2]
        price = _safe(row["close"])
        atr_value = _safe(row["atr"])
        if price <= 0 or atr_value <= 0:
            return None

        reasons: list[str] = []
        trend = self._score_trend(row, prev, reasons)
        momentum = self._score_momentum(row, prev, reasons)
        volume = self._score_volume(row, trend, reasons)
        volatility = self._score_volatility(row, reasons)
        regime = self._score_regime(row, benchmark, reasons)

        base = (
            trend * self.weights.trend
            + momentum * self.weights.momentum
            + volume * self.weights.volume
            + volatility * self.weights.volatility
        )
        composite = self._apply_regime(base, regime, reasons)
        action = self._decide(composite, reasons)

        timestamp = row.name if isinstance(row.name, datetime) else datetime.now()

        return Signal(
            symbol=symbol,
            timestamp=timestamp,
            action=action,
            score=round(float(composite), 4),
            price=price,
            atr=atr_value,
            factors=FactorScores(
                regime=regime,
                trend=trend,
                momentum=momentum,
                volatility=volatility,
                volume=volume,
            ),
            reasons=reasons,
        )

    def should_exit(self, signal: Signal, position_side_sign: int) -> bool:
        """Whether a weakening signal should close an existing position.

        Exits when conviction decays below the exit threshold, or when the score
        flips against the position outright.
        """
        aligned = signal.score * position_side_sign
        return aligned < self.exit_threshold

    # ------------------------------------------------------------------
    # Factors
    # ------------------------------------------------------------------
    def _score_trend(self, row: pd.Series, prev: pd.Series, reasons: list[str]) -> float:
        """EMA structure, MACD, long-term bias, and Supertrend direction."""
        price = _safe(row["close"])
        ema_20 = _safe(row["ema_20"])
        ema_50 = _safe(row["ema_50"])
        ema_200 = _safe(row.get("ema_200"))
        atr_value = _safe(row["atr"], 1.0) or 1.0

        components: list[float] = []

        # EMA separation, measured in ATR units so it is comparable across stocks.
        if ema_20 and ema_50:
            separation = _squash((ema_20 - ema_50) / atr_value, scale=1.0)
            components.append(separation)
            if abs(separation) > 0.3:
                reasons.append(
                    f"EMA20 {'above' if separation > 0 else 'below'} EMA50 "
                    f"by {abs(ema_20 - ema_50) / atr_value:.2f} ATR"
                )

        # Fresh MACD crossovers carry more information than a persistent sign.
        hist = _safe(row.get("macd_hist"))
        prev_hist = _safe(prev.get("macd_hist"))
        if hist or prev_hist:
            components.append(_squash(hist / atr_value, scale=0.5))
            if hist > 0 and prev_hist <= 0:
                reasons.append("MACD crossed bullish")
                components.append(0.5)
            elif hist < 0 and prev_hist >= 0:
                reasons.append("MACD crossed bearish")
                components.append(-0.5)

        # Long-term bias.
        if ema_200 and price:
            components.append(0.4 if price > ema_200 else -0.4)

        direction = _safe(row.get("supertrend_direction"))
        if direction:
            components.append(0.5 * direction)

        if not components:
            return 0.0

        score = float(np.clip(np.mean(components), -1.0, 1.0))

        # Chop filter: trend-following signals whipsaw when ADX is low.
        adx_value = _safe(row.get("adx"))
        if adx_value and adx_value < ADX_RANGING:
            score *= 0.5
            reasons.append(f"ADX {adx_value:.1f} — ranging, trend signal halved")
        elif adx_value >= ADX_TRENDING:
            reasons.append(f"ADX {adx_value:.1f} — trend confirmed")

        return score

    def _score_momentum(self, row: pd.Series, prev: pd.Series, reasons: list[str]) -> float:
        """RSI positioning plus rate of change.

        RSI is treated as trend confirmation in its mid-range and as an
        exhaustion warning at the extremes — buying a 78 RSI is chasing.
        """
        components: list[float] = []

        rsi_value = _safe(row.get("rsi"), 50.0)
        if rsi_value:
            if rsi_value > RSI_OVERBOUGHT:
                components.append(-0.3)
                reasons.append(f"RSI {rsi_value:.1f} overbought — exhaustion risk")
            elif rsi_value < RSI_OVERSOLD:
                components.append(0.3)
                reasons.append(f"RSI {rsi_value:.1f} oversold — bounce potential")
            else:
                # Centre on 50 and scale to [-1, 1] across the 30-70 band.
                components.append((rsi_value - 50) / 20)

        roc_value = _safe(row.get("roc"))
        if roc_value:
            components.append(_squash(roc_value, scale=2.0))

        # Acceleration: is momentum itself building or fading?
        prev_rsi = _safe(prev.get("rsi"), 50.0)
        if rsi_value and prev_rsi:
            components.append(_squash(rsi_value - prev_rsi, scale=5.0) * 0.5)

        if not components:
            return 0.0
        return float(np.clip(np.mean(components), -1.0, 1.0))

    def _score_volume(self, row: pd.Series, trend_score: float, reasons: list[str]) -> float:
        """Volume confirmation, signed to agree with the trend.

        Volume has no direction of its own — heavy volume amplifies whatever the
        price is doing, so this factor takes the trend's sign and scales it by
        participation.
        """
        ratio = _safe(row.get("volume_ratio"), 1.0)
        if not ratio:
            return 0.0

        if ratio >= VOLUME_CONFIRM:
            strength = min((ratio - 1.0) / 1.5, 1.0)
            reasons.append(f"Volume {ratio:.2f}x average — move confirmed")
            return float(np.sign(trend_score) * strength) if trend_score else 0.0

        if ratio < 0.7:
            reasons.append(f"Volume {ratio:.2f}x average — weak participation")
            return float(-np.sign(trend_score) * 0.3) if trend_score else 0.0

        return 0.0

    def _score_volatility(self, row: pd.Series, reasons: list[str]) -> float:
        """Volatility as a tradability check, signed toward the prevailing move.

        Too quiet and the move cannot cover brokerage and slippage; too wild and
        stops get taken out by noise. The healthy middle earns a small boost in
        the direction price is already travelling.
        """
        atr_pct = _safe(row.get("atr_pct"))
        if not atr_pct:
            return 0.0

        price = _safe(row["close"])
        ema_20 = _safe(row.get("ema_20"), price)
        direction = float(np.sign(price - ema_20)) if ema_20 else 0.0

        if atr_pct < ATR_PCT_CALM:
            reasons.append(f"ATR {atr_pct:.2f}% — too quiet to cover costs")
            return -0.4 * direction if direction else -0.2

        if atr_pct > ATR_PCT_ELEVATED:
            reasons.append(f"ATR {atr_pct:.2f}% — elevated volatility, conviction reduced")
            return -0.3 * direction if direction else -0.2

        bb_percent = _safe(row.get("bb_percent"), 0.5)
        if bb_percent > 0.95:
            reasons.append("Price at upper Bollinger band — stretched")
            return -0.2
        if bb_percent < 0.05:
            reasons.append("Price at lower Bollinger band — stretched")
            return 0.2

        return 0.3 * direction

    def _score_regime(
        self, row: pd.Series, benchmark: pd.DataFrame | None, reasons: list[str]
    ) -> float:
        """Broad-market direction, from the benchmark if available.

        Falls back to the instrument's own long-term structure when no benchmark
        is supplied, so the engine still works on a single symbol.
        """
        if benchmark is not None and not benchmark.empty:
            bench = benchmark if "ema_200" in benchmark.columns else indicators.enrich(benchmark)
            if len(bench) and not pd.isna(bench.iloc[-1].get("ema_50")):
                brow = bench.iloc[-1]
                bprice = _safe(brow["close"])
                bema_50 = _safe(brow.get("ema_50"))
                bema_200 = _safe(brow.get("ema_200"), bema_50)
                badx = _safe(brow.get("adx"))

                score = 0.0
                if bema_50:
                    score += 0.5 if bprice > bema_50 else -0.5
                if bema_200:
                    score += 0.5 if bprice > bema_200 else -0.5

                # A weak index trend means the regime signal itself is unreliable.
                if badx and badx < ADX_RANGING:
                    score *= 0.5
                    reasons.append("Benchmark ranging — regime signal weak")
                else:
                    reasons.append(
                        f"Benchmark regime {'bullish' if score > 0 else 'bearish'}"
                    )
                return float(np.clip(score, -1.0, 1.0))

        # Fallback: the instrument's own position relative to its 200 EMA.
        price = _safe(row["close"])
        ema_200 = _safe(row.get("ema_200"))
        if ema_200 and price:
            return 0.5 if price > ema_200 else -0.5
        return 0.0

    # ------------------------------------------------------------------
    # Combination & decision
    # ------------------------------------------------------------------
    def _apply_regime(self, base: float, regime: float, reasons: list[str]) -> float:
        """Scale the composite by how well it aligns with the market regime."""
        if abs(regime) < 0.2:
            return base * NEUTRAL_REGIME_DAMPING

        if np.sign(base) == np.sign(regime):
            # Aligned: allow up to a 20% boost, still bounded to [-1, 1].
            return float(np.clip(base * (1.0 + 0.2 * abs(regime)), -1.0, 1.0))

        reasons.append("Signal fights the market regime — heavily damped")
        return base * HOSTILE_REGIME_DAMPING

    def _decide(self, composite: float, reasons: list[str]) -> SignalAction:
        if composite >= self.entry_threshold:
            return SignalAction.BUY
        if composite <= -self.entry_threshold:
            if not self.allow_shorts:
                reasons.append("Bearish signal suppressed — shorts disabled")
                return SignalAction.HOLD
            return SignalAction.SELL
        return SignalAction.HOLD


def evaluate_series(
    df: pd.DataFrame,
    symbol: str,
    engine: SignalEngine | None = None,
    benchmark: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Score every bar in `df` — used for backtests and chart overlays.

    Indicators are computed once for the whole frame, then each bar is scored
    against the history available up to that point.
    """
    engine = engine or SignalEngine()
    enriched = indicators.enrich(df)
    warmup = indicators.warmup_period()

    bench_enriched = (
        indicators.enrich(benchmark) if benchmark is not None and not benchmark.empty else None
    )

    records = []
    for i in range(warmup, len(enriched)):
        window = enriched.iloc[: i + 1]

        bench_window = None
        if bench_enriched is not None:
            # Only benchmark bars at or before this timestamp — using later bars
            # would leak future information into the backtest.
            bench_window = bench_enriched.loc[: window.index[-1]]
            if bench_window.empty:
                bench_window = None

        signal = engine.evaluate(window, symbol, benchmark=bench_window, enrich=False)
        if signal is None:
            continue
        records.append(
            {
                "timestamp": signal.timestamp,
                "action": signal.action.value,
                "score": signal.score,
                "price": signal.price,
                "atr": signal.atr,
                **signal.factors.to_dict(),
            }
        )

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).set_index("timestamp")
