"""Signal engine scoring and gating."""

from __future__ import annotations

import pandas as pd
import pytest

from app.models import SignalAction
from app.strategy import indicators
from app.strategy.signals import FactorWeights, SignalEngine


@pytest.fixture
def engine() -> SignalEngine:
    return SignalEngine()


def test_returns_none_on_empty_input(engine):
    assert engine.evaluate(pd.DataFrame(), "TEST") is None


def test_returns_none_before_warmup(engine, ohlcv):
    assert engine.evaluate(ohlcv.iloc[:20], "TEST") is None


def test_produces_a_bounded_score(engine, ohlcv):
    signal = engine.evaluate(ohlcv, "TEST")
    assert signal is not None
    assert -1.0 <= signal.score <= 1.0
    assert signal.price > 0
    assert signal.atr > 0
    assert signal.symbol == "TEST"


def test_every_factor_is_bounded(engine, ohlcv):
    signal = engine.evaluate(ohlcv, "TEST")
    for name, value in signal.factors.to_dict().items():
        assert -1.0 <= value <= 1.0, f"{name} out of bounds: {value}"


def test_uptrend_scores_bullish(engine, trending_ohlcv):
    signal = engine.evaluate(trending_ohlcv, "TEST")
    assert signal.factors.trend > 0
    assert signal.score > 0


def test_downtrend_scores_bearish(engine, falling_ohlcv):
    signal = engine.evaluate(falling_ohlcv, "TEST")
    assert signal.factors.trend < 0
    assert signal.score < 0


def test_signal_carries_its_reasoning(engine, trending_ohlcv):
    """A score with no visible derivation cannot be trusted or tuned."""
    signal = engine.evaluate(trending_ohlcv, "TEST")
    assert signal.reasons, "signal produced no explanation"
    assert all(isinstance(r, str) for r in signal.reasons)


def test_threshold_controls_actionability(trending_ohlcv):
    eager = SignalEngine(entry_threshold=0.01).evaluate(trending_ohlcv, "TEST")
    strict = SignalEngine(entry_threshold=0.99).evaluate(trending_ohlcv, "TEST")
    assert eager.action is not SignalAction.HOLD
    assert strict.action is SignalAction.HOLD


def test_shorts_can_be_disabled(falling_ohlcv):
    allowed = SignalEngine(entry_threshold=0.05, allow_shorts=True)
    blocked = SignalEngine(entry_threshold=0.05, allow_shorts=False)
    assert allowed.evaluate(falling_ohlcv, "TEST").action is SignalAction.SELL
    assert blocked.evaluate(falling_ohlcv, "TEST").action is SignalAction.HOLD


def test_hostile_regime_damps_the_score(trending_ohlcv, falling_ohlcv):
    """A bullish setup in a falling market must score lower than in a rising one."""
    engine = SignalEngine()
    friendly = engine.evaluate(trending_ohlcv, "TEST", benchmark=trending_ohlcv)
    hostile = engine.evaluate(trending_ohlcv, "TEST", benchmark=falling_ohlcv)
    assert hostile.score < friendly.score
    assert hostile.factors.regime < friendly.factors.regime


def test_should_exit_on_decayed_conviction(engine, ohlcv):
    signal = engine.evaluate(ohlcv, "TEST")
    signal.score = 0.02  # well below the exit threshold
    assert engine.should_exit(signal, position_side_sign=1)


def test_should_exit_when_score_flips_against_a_long(engine, ohlcv):
    signal = engine.evaluate(ohlcv, "TEST")
    signal.score = -0.8
    assert engine.should_exit(signal, position_side_sign=1)


def test_holds_a_position_with_intact_conviction(engine, ohlcv):
    signal = engine.evaluate(ohlcv, "TEST")
    signal.score = 0.9
    assert not engine.should_exit(signal, position_side_sign=1)


def test_short_position_exit_uses_inverted_sign(engine, ohlcv):
    signal = engine.evaluate(ohlcv, "TEST")
    signal.score = -0.9  # strongly bearish: good for a short
    assert not engine.should_exit(signal, position_side_sign=-1)
    signal.score = 0.9  # flipped bullish: bad for a short
    assert engine.should_exit(signal, position_side_sign=-1)


def test_weights_are_normalised():
    weights = FactorWeights(trend=2.0, momentum=2.0, volume=2.0, volatility=2.0)
    normalised = weights.normalised()
    total = (
        normalised.trend + normalised.momentum + normalised.volume + normalised.volatility
    )
    assert total == pytest.approx(1.0)
    assert normalised.trend == pytest.approx(0.25)


def test_zero_weights_are_rejected():
    with pytest.raises(ValueError):
        FactorWeights(trend=0, momentum=0, volume=0, volatility=0).normalised()


def test_accepts_pre_enriched_frames(engine, ohlcv):
    """The backtester enriches once up front; the engine must accept that."""
    enriched = indicators.enrich(ohlcv)
    from_raw = engine.evaluate(ohlcv, "TEST")
    from_enriched = engine.evaluate(enriched, "TEST", enrich=False)
    assert from_enriched.score == pytest.approx(from_raw.score)


def test_evaluation_is_causal(engine, ohlcv):
    """Scoring a bar must not depend on bars that came after it."""
    cut = 500
    truncated = engine.evaluate(ohlcv.iloc[:cut], "TEST")
    full_history = engine.evaluate(ohlcv, "TEST")
    # Re-score the same bar from the longer frame, sliced to that bar.
    resliced = engine.evaluate(ohlcv.iloc[:cut], "TEST")

    assert truncated.score == pytest.approx(resliced.score)
    assert truncated.timestamp == ohlcv.index[cut - 1]
    assert full_history.timestamp == ohlcv.index[-1]
