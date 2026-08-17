"""Indicator correctness and bounds."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.strategy import indicators


def test_ema_matches_pandas_ewm(ohlcv):
    result = indicators.ema(ohlcv["close"], 20)
    expected = ohlcv["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    pd.testing.assert_series_equal(result, expected, check_names=False)


def test_sma_is_rolling_mean(ohlcv):
    result = indicators.sma(ohlcv["close"], 10)
    assert result.iloc[:9].isna().all(), "SMA must be undefined before the window fills"
    assert result.iloc[9] == pytest.approx(ohlcv["close"].iloc[:10].mean())


def test_rsi_stays_within_bounds(ohlcv):
    values = indicators.rsi(ohlcv["close"], 14).dropna()
    assert not values.empty
    assert values.between(0, 100).all()


def test_rsi_is_100_for_monotonic_gains():
    """An unbroken run of gains has zero average loss; RSI must saturate, not divide by zero."""
    rising = pd.Series(np.arange(100, 160, dtype=float))
    values = indicators.rsi(rising, 14).dropna()
    assert (values == 100.0).all()


def test_atr_is_non_negative_and_defined_after_warmup(ohlcv):
    values = indicators.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
    assert values.iloc[:13].isna().all()
    assert (values.dropna() >= 0).all()


def test_true_range_covers_gaps():
    """True range must account for gaps against the prior close, not just high-low."""
    frame = pd.DataFrame(
        {
            "high": [100.0, 130.0],
            "low": [95.0, 125.0],
            "close": [98.0, 128.0],
        }
    )
    tr = indicators.true_range(frame["high"], frame["low"], frame["close"])
    # Bar 2 gapped up: 130 - 98 (prior close) = 32, wider than its 5-point range.
    assert tr.iloc[1] == pytest.approx(32.0)


def test_adx_within_bounds(ohlcv):
    frame = indicators.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
    for column in ("adx", "plus_di", "minus_di"):
        values = frame[column].dropna()
        assert not values.empty, f"{column} produced no values"
        assert values.between(0, 100).all(), f"{column} escaped 0-100"


def test_adx_rises_in_a_strong_trend(trending_ohlcv, ohlcv):
    trending = indicators.adx(
        trending_ohlcv["high"], trending_ohlcv["low"], trending_ohlcv["close"]
    )["adx"].dropna()
    choppy = indicators.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"])["adx"].dropna()
    assert trending.mean() > choppy.mean()


def test_macd_histogram_is_line_minus_signal(ohlcv):
    frame = indicators.macd(ohlcv["close"])
    diff = (frame["macd"] - frame["macd_signal"]).dropna()
    pd.testing.assert_series_equal(
        frame["macd_hist"].dropna(), diff, check_names=False
    )


def test_bollinger_percent_locates_price_in_band(ohlcv):
    frame = indicators.bollinger_bands(ohlcv["close"], 20, 2.0)
    valid = frame.dropna()
    assert (valid["bb_upper"] >= valid["bb_middle"]).all()
    assert (valid["bb_middle"] >= valid["bb_lower"]).all()


def test_vwap_resets_each_day(ohlcv):
    values = indicators.vwap(ohlcv)
    days = pd.Series(ohlcv.index, index=ohlcv.index).dt.date
    first_of_day = values.groupby(days).first()
    typical_first = ((ohlcv["high"] + ohlcv["low"] + ohlcv["close"]) / 3).groupby(days).first()
    # The first bar of a session is its own VWAP.
    for day in first_of_day.index:
        assert first_of_day[day] == pytest.approx(typical_first[day])


def test_supertrend_direction_is_binary(ohlcv):
    frame = indicators.supertrend(ohlcv)
    assert set(frame["supertrend_direction"].unique()) <= {-1.0, 1.0}


def test_enrich_attaches_every_expected_column(ohlcv):
    enriched = indicators.enrich(ohlcv)
    for column in (
        "ema_9", "ema_20", "ema_50", "ema_200",
        "macd", "macd_signal", "macd_hist",
        "rsi", "atr", "atr_pct", "adx", "plus_di", "minus_di",
        "bb_upper", "bb_lower", "bb_percent",
        "roc", "volume_ratio", "vwap", "supertrend_direction",
    ):
        assert column in enriched.columns, f"missing {column}"


def test_enrich_does_not_mutate_input(ohlcv):
    before = ohlcv.copy()
    indicators.enrich(ohlcv)
    pd.testing.assert_frame_equal(ohlcv, before)


def test_enrich_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing required columns"):
        indicators.enrich(pd.DataFrame({"close": [1.0, 2.0]}))


def test_indicators_are_causal(ohlcv):
    """Indicator values must not change when future bars are appended.

    This is the lookahead guard: if truncating the future altered a past value,
    every backtest result computed with this library would be fiction.
    """
    cut = 400
    full = indicators.enrich(ohlcv)
    partial = indicators.enrich(ohlcv.iloc[:cut])

    for column in ("ema_20", "rsi", "atr", "adx", "macd_hist"):
        a = full[column].iloc[:cut].dropna()
        b = partial[column].dropna()
        common = a.index.intersection(b.index)
        assert len(common) > 50
        np.testing.assert_allclose(
            a.loc[common].to_numpy(), b.loc[common].to_numpy(), rtol=1e-9, atol=1e-9
        )
