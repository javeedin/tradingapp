"""Shared fixtures: deterministic synthetic price data."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

BARS_PER_DAY = 75  # 09:15-15:30 in 5-minute candles


def make_ohlcv(
    bars: int = 600,
    seed: int = 42,
    drift: float = 0.0003,
    volatility: float = 0.004,
    start_price: float = 1000.0,
) -> pd.DataFrame:
    """Synthetic OHLCV on a realistic intraday timestamp grid."""
    rng = np.random.default_rng(seed)

    stamps: list[datetime] = []
    day = 0
    origin = datetime(2025, 1, 1, 9, 15)
    while len(stamps) < bars:
        base = origin + timedelta(days=day)
        if base.weekday() < 5:
            for i in range(BARS_PER_DAY):
                if len(stamps) >= bars:
                    break
                stamps.append(base + timedelta(minutes=5 * i))
        day += 1

    returns = rng.normal(drift, volatility, bars)
    close = start_price * np.exp(np.cumsum(returns))
    spread = np.abs(rng.normal(0, volatility * 0.7, bars)) * close

    return pd.DataFrame(
        {
            "open": np.concatenate([[close[0]], close[:-1]]),
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": rng.integers(10_000, 200_000, bars).astype(float),
        },
        index=pd.DatetimeIndex(stamps, name="timestamp"),
    )


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv()


@pytest.fixture
def trending_ohlcv() -> pd.DataFrame:
    """Strong uptrend with low noise — should score bullish."""
    return make_ohlcv(seed=7, drift=0.0025, volatility=0.0015)


@pytest.fixture
def falling_ohlcv() -> pd.DataFrame:
    return make_ohlcv(seed=11, drift=-0.0025, volatility=0.0015)
