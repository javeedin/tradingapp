"""Technical indicators implemented in pure pandas/numpy.

Deliberately dependency-free: TA-Lib needs a C library compiled on the host,
which is a recurring source of deployment pain on a small VPS. Everything here
is vectorised, so a full backtest over years of 5-minute bars stays fast.

All functions take and return pandas Series/DataFrames aligned on the input
index, and use Wilder's smoothing (alpha = 1/period) where the classic
definition calls for it — that is what charting platforms display, so signals
computed here match what you see on a chart.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing — an EMA with alpha = 1/period."""
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100), Wilder-smoothed."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)

    # A zero average loss means an unbroken run of gains -> RSI 100.
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.where(avg_loss != 0, 100.0).where(avg_gain.notna(), np.nan)


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_hist": macd_line - signal_line,
        }
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range — the volatility unit for stops and position sizing."""
    return _wilder(true_range(high, low, close), period)


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.DataFrame:
    """Average Directional Index with +DI/-DI.

    ADX measures trend *strength* regardless of direction: below ~20 the market
    is ranging (mean-reversion territory, where trend-following signals whipsaw),
    above ~25 a trend is established.
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )

    atr_values = _wilder(true_range(high, low, close), period)
    safe_atr = atr_values.replace(0, np.nan)

    plus_di = 100 * _wilder(plus_dm, period) / safe_atr
    minus_di = 100 * _wilder(minus_dm, period) / safe_atr

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum

    return pd.DataFrame(
        {"adx": _wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di}
    )


def bollinger_bands(
    close: pd.Series, period: int = 20, std_dev: float = 2.0
) -> pd.DataFrame:
    middle = sma(close, period)
    deviation = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std_dev * deviation
    lower = middle - std_dev * deviation
    width = (upper - lower) / middle.replace(0, np.nan)

    # %B: where price sits inside the band (0 = lower, 1 = upper).
    span = (upper - lower).replace(0, np.nan)
    percent_b = (close - lower) / span

    return pd.DataFrame(
        {
            "bb_upper": upper,
            "bb_middle": middle,
            "bb_lower": lower,
            "bb_width": width,
            "bb_percent": percent_b,
        }
    )


def rate_of_change(close: pd.Series, period: int = 10) -> pd.Series:
    """Percentage change over `period` bars."""
    return close.pct_change(periods=period) * 100


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume relative to its rolling average (1.0 = average)."""
    average = volume.rolling(window=period, min_periods=period).mean()
    return volume / average.replace(0, np.nan)


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP.

    Resets each calendar day, which is what intraday traders actually watch —
    a continuous VWAP across days is meaningless for intraday decisions.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    notional = typical * df["volume"]
    day = pd.Series(df.index, index=df.index).dt.date

    cumulative_notional = notional.groupby(day).cumsum()
    cumulative_volume = df["volume"].groupby(day).cumsum()
    return cumulative_notional / cumulative_volume.replace(0, np.nan)


def supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> pd.DataFrame:
    """Supertrend: ATR-banded trend direction (+1 up, -1 down).

    The bands ratchet — they only tighten in the direction of the trend — so the
    loop below cannot be vectorised without changing the indicator's meaning.
    """
    atr_values = atr(df["high"], df["low"], df["close"], period)
    hl2 = (df["high"] + df["low"]) / 2
    upper_basic = hl2 + multiplier * atr_values
    lower_basic = hl2 - multiplier * atr_values

    close = df["close"].to_numpy(dtype=float)
    upper_arr = upper_basic.to_numpy(dtype=float)
    lower_arr = lower_basic.to_numpy(dtype=float)

    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.ones(n)

    for i in range(1, n):
        if np.isnan(upper_arr[i]) or np.isnan(lower_arr[i]):
            continue

        prev_upper = final_upper[i - 1]
        prev_lower = final_lower[i - 1]

        final_upper[i] = (
            upper_arr[i]
            if np.isnan(prev_upper) or upper_arr[i] < prev_upper or close[i - 1] > prev_upper
            else prev_upper
        )
        final_lower[i] = (
            lower_arr[i]
            if np.isnan(prev_lower) or lower_arr[i] > prev_lower or close[i - 1] < prev_lower
            else prev_lower
        )

        if not np.isnan(final_upper[i]) and close[i] > final_upper[i]:
            direction[i] = 1
        elif not np.isnan(final_lower[i]) and close[i] < final_lower[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

    line = np.where(direction == 1, final_lower, final_upper)
    return pd.DataFrame(
        {"supertrend": line, "supertrend_direction": direction}, index=df.index
    )


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the full indicator set used by the signal engine.

    Returns a copy; the input is never mutated.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame is missing required columns: {missing}")

    out = df.copy()

    out["ema_9"] = ema(out["close"], 9)
    out["ema_20"] = ema(out["close"], 20)
    out["ema_50"] = ema(out["close"], 50)
    out["ema_200"] = ema(out["close"], 200)

    out = out.join(macd(out["close"]))
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = atr(out["high"], out["low"], out["close"], 14)
    out["atr_pct"] = out["atr"] / out["close"].replace(0, np.nan) * 100
    out = out.join(adx(out["high"], out["low"], out["close"], 14))
    out = out.join(bollinger_bands(out["close"], 20, 2.0))
    out["roc"] = rate_of_change(out["close"], 10)
    out["volume_ratio"] = volume_ratio(out["volume"], 20)
    out["vwap"] = vwap(out)
    out = out.join(supertrend(out, 10, 3.0))

    return out


def warmup_period() -> int:
    """Bars needed before every indicator in `enrich` is defined.

    Driven by the 200-period EMA; the backtester and live loop use this to skip
    the leading window instead of trading on half-formed indicators.
    """
    return 200
