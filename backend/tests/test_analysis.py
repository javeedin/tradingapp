"""On-demand symbol analysis."""

from __future__ import annotations

import pytest

from app.data.store import MarketStore
from app.engine.analysis import AnalysisError, analyse_symbol
from app.models import Candle, ProductType
from app.risk.manager import RiskManager
from app.strategy import indicators
from app.strategy.signals import SignalEngine

from .conftest import make_ohlcv


def seed(store: MarketStore, symbol: str, bars: int = 500, **kwargs) -> None:
    frame = make_ohlcv(bars=bars, **kwargs)
    store.save_candles(
        symbol,
        [
            Candle(ts, r.open, r.high, r.low, r.close, r.volume)
            for ts, r in frame.iterrows()
        ],
        "5minute",
    )


@pytest.fixture
def store(tmp_path) -> MarketStore:
    s = MarketStore(tmp_path / "analysis.duckdb")
    yield s
    s.close()


@pytest.fixture
def engine() -> SignalEngine:
    return SignalEngine()


@pytest.fixture
def risk() -> RiskManager:
    return RiskManager(max_position_pct=100.0)


def analyse(store, engine, risk, symbol="TEST", **kwargs):
    return analyse_symbol(
        symbol, store, engine, risk, refresh=False, interval="5minute", **kwargs
    )


# ----------------------------------------------------------------------
# Failure modes
# ----------------------------------------------------------------------
def test_unknown_symbol_names_the_code_convention(store, engine, risk):
    """The usual cause is an NSE ticker where a Breeze code is required."""
    with pytest.raises(AnalysisError, match="Breeze uses its own stock codes"):
        analyse(store, engine, risk, "NOTREAL")


def test_blank_symbol_is_rejected(store, engine, risk):
    with pytest.raises(AnalysisError, match="No symbol supplied"):
        analyse(store, engine, risk, "   ")


def test_insufficient_history_is_explained(store, engine, risk):
    seed(store, "SHORT", bars=60)
    with pytest.raises(AnalysisError, match="candles for"):
        analyse(store, engine, risk, "SHORT")


# ----------------------------------------------------------------------
# Plan shape
# ----------------------------------------------------------------------
def test_returns_a_complete_plan(store, engine, risk):
    seed(store, "TEST")
    result = analyse(store, engine, risk)

    assert result["symbol"] == "TEST"
    assert result["action"] in {"buy", "sell", "hold"}
    assert -1 <= result["score"] <= 1
    assert set(result) >= {"plan", "market", "factors", "reasons", "conviction"}

    plan = result["plan"]
    assert set(plan) >= {
        "entry", "stoploss", "target", "quantity", "risk_amount", "risk_reward",
    }
    assert plan["entry"] > 0
    assert plan["quantity"] >= 0


def test_long_plan_brackets_the_entry(store, engine, risk):
    """A buy must have its stop below and target above — the inverse would be a
    plan that takes a loss on success."""
    seed(store, "UP", seed=7, drift=0.0025, volatility=0.0015)
    result = analyse(store, engine, risk, "UP")

    plan = result["plan"]
    if result["side"] == "buy":
        assert plan["stoploss"] < plan["entry"] < plan["target"]


def test_short_plan_brackets_the_entry(store, engine, risk):
    seed(store, "DOWN", seed=11, drift=-0.0025, volatility=0.0015)
    result = analyse(store, engine, risk, "DOWN")

    plan = result["plan"]
    if result["side"] == "sell":
        assert plan["target"] < plan["entry"] < plan["stoploss"]


def test_hold_still_returns_usable_levels(store, engine, risk):
    """A bare 'no' throws away the useful half of the answer."""
    strict = SignalEngine(entry_threshold=0.99)
    seed(store, "TEST")
    result = analyse(store, strict, risk)

    assert result["action"] == "hold"
    assert result["plan"]["entry"] > 0
    assert result["plan"]["stoploss"] > 0
    assert result["plan"]["target"] > 0


def test_stop_distance_matches_the_atr_multiple(store, engine, risk):
    seed(store, "TEST")
    result = analyse(store, engine, risk)

    expected = result["market"]["atr"] * risk.atr_stop_multiplier
    assert result["plan"]["stop_distance"] == pytest.approx(expected, rel=0.02)


def test_risk_reward_reflects_the_configured_multiples(store, engine, risk):
    seed(store, "TEST")
    result = analyse(store, engine, risk)
    assert result["plan"]["risk_reward"] == pytest.approx(
        risk.atr_target_multiplier / risk.atr_stop_multiplier, rel=0.01
    )


def test_reports_when_the_position_cap_overrode_the_risk_rule(store, engine):
    """The user needs to see that real risk is below what they configured."""
    capped = RiskManager(risk_per_trade_pct=1.0, max_position_pct=5.0)
    seed(store, "TEST")
    result = analyse(store, engine, capped)

    plan = result["plan"]
    assert plan["was_capped"]
    assert plan["binding_constraint"] == "max_position_pct"
    assert plan["risk_pct_of_equity"] < 1.0


def test_conviction_language_tracks_the_threshold(store, risk):
    seed(store, "TEST")
    eager = analyse(store, SignalEngine(entry_threshold=0.01), risk)
    strict = analyse(store, SignalEngine(entry_threshold=0.99), risk)

    assert eager["conviction"] in {"strong", "moderate"}
    assert "below the entry threshold" in strict["conviction"] or (
        strict["conviction"] == "negligible"
    )


def test_market_context_is_populated(store, engine, risk):
    seed(store, "TEST")
    market = analyse(store, engine, risk)["market"]

    assert market["price"] > 0
    assert market["atr"] > 0
    assert 0 <= market["rsi"] <= 100
    assert 0 <= market["adx"] <= 100
    assert isinstance(market["above_vwap"], bool)
    assert market["trend"]


def test_reasons_are_present(store, engine, risk):
    seed(store, "TEST")
    assert analyse(store, engine, risk)["reasons"]


def test_benchmark_is_used_when_available(store, engine, risk):
    """A hostile benchmark must damp the score, matching live behaviour."""
    seed(store, "TEST", seed=7, drift=0.0025, volatility=0.0015)
    seed(store, "NIFTY", seed=11, drift=-0.0025, volatility=0.0015)

    with_bench = analyse(store, engine, risk, benchmark_code="NIFTY")
    without = analyse(store, engine, risk, benchmark_code="ABSENT")
    assert with_bench["score"] <= without["score"]


def test_lot_size_rounds_the_quantity(store, engine, risk):
    seed(store, "TEST")
    result = analyse(store, engine, risk, lot_size=25)
    quantity = result["plan"]["quantity"]
    if quantity:
        assert quantity % 25 == 0


def test_delivery_product_is_reported(store, engine, risk):
    seed(store, "TEST")
    result = analyse(store, engine, risk, product=ProductType.DELIVERY)
    assert result["plan"]["product"] == ProductType.DELIVERY.value


def test_matches_the_live_engine_verdict(store, engine, risk):
    """An ad-hoc lookup must not disagree with what the bot would do."""
    seed(store, "TEST")
    result = analyse(store, engine, risk)

    frame = store.load_candles("TEST", "5minute", limit=500)
    direct = engine.evaluate(indicators.enrich(frame), "TEST", enrich=False)

    assert direct is not None
    assert result["action"] == direct.action.value
    assert result["score"] == pytest.approx(direct.score)
