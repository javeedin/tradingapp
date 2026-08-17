"""Broker-side position monitoring, including hand-bought MTF holdings."""

from __future__ import annotations

import pytest

from app.data.store import MarketStore
from app.engine import monitor
from app.models import Candle, ProductType, Side
from app.risk.manager import RiskManager

from .conftest import make_ohlcv


@pytest.fixture
def store(tmp_path) -> MarketStore:
    s = MarketStore(tmp_path / "monitor.duckdb")
    frame = make_ohlcv(bars=300, start_price=1000.0)
    s.save_candles(
        "TCS",
        [Candle(ts, r.open, r.high, r.low, r.close, r.volume) for ts, r in frame.iterrows()],
        "5minute",
    )
    yield s
    s.close()


@pytest.fixture
def risk() -> RiskManager:
    return RiskManager(atr_stop_multiplier=1.5, atr_target_multiplier=2.5)


# ----------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------
def test_normalises_a_positions_row():
    position = monitor.normalise_broker_position(
        {
            "stock_code": "tcs",
            "quantity": "25",
            "average_price": "3400.50",
            "ltp": "3450",
            "product_type": "cash",
        }
    )
    assert position is not None
    assert position.symbol == "TCS"
    assert position.quantity == 25
    assert position.side is Side.BUY
    assert position.entry_price == pytest.approx(3400.50)
    assert position.product is ProductType.DELIVERY


def test_recognises_an_mtf_holding():
    """MTF is the case this whole module exists for."""
    position = monitor.normalise_broker_position(
        {"stock_code": "RELIND", "quantity": 10, "average_price": 1400, "product_type": "mtf"}
    )
    assert position is not None
    assert position.product is ProductType.MTF
    assert position.product.is_leveraged
    assert not position.product.placeable_via_api


def test_alternative_field_names_are_accepted():
    """Positions and holdings endpoints use different keys for the same data."""
    position = monitor.normalise_broker_position(
        {"symbol": "INFTEC", "net_quantity": 8, "avg_price": 1500, "last_traded_price": 1520}
    )
    assert position is not None
    assert position.quantity == 8
    assert position.entry_price == pytest.approx(1500)


def test_negative_quantity_is_read_as_a_short():
    position = monitor.normalise_broker_position(
        {"stock_code": "TCS", "quantity": -15, "average_price": 3400}
    )
    assert position is not None
    assert position.side is Side.SELL
    assert position.quantity == 15


def test_explicit_sell_action_is_respected():
    position = monitor.normalise_broker_position(
        {"stock_code": "TCS", "quantity": 15, "average_price": 3400, "action": "Sell"}
    )
    assert position is not None
    assert position.side is Side.SELL


def test_zero_quantity_is_not_a_position():
    assert (
        monitor.normalise_broker_position(
            {"stock_code": "TCS", "quantity": 0, "average_price": 3400}
        )
        is None
    )


def test_row_without_a_symbol_is_skipped():
    assert monitor.normalise_broker_position({"quantity": 10}) is None


def test_unknown_product_defaults_to_delivery():
    position = monitor.normalise_broker_position(
        {"stock_code": "TCS", "quantity": 5, "product_type": "something_new"}
    )
    assert position is not None
    assert position.product is ProductType.DELIVERY


# ----------------------------------------------------------------------
# P&L
# ----------------------------------------------------------------------
def test_long_pnl():
    position = monitor.BrokerPosition(
        symbol="TCS", side=Side.BUY, quantity=10,
        entry_price=100.0, product=ProductType.DELIVERY, last_price=110.0,
    )
    assert position.unrealised_pnl == pytest.approx(100.0)
    assert position.unrealised_pct == pytest.approx(10.0)


def test_short_pnl_is_inverted():
    position = monitor.BrokerPosition(
        symbol="TCS", side=Side.SELL, quantity=10,
        entry_price=100.0, product=ProductType.DELIVERY, last_price=90.0,
    )
    assert position.unrealised_pnl == pytest.approx(100.0)
    assert position.unrealised_pct == pytest.approx(10.0)


# ----------------------------------------------------------------------
# Levels
# ----------------------------------------------------------------------
def describe(store, risk, **overrides):
    defaults = dict(
        symbol="TCS", side=Side.BUY, quantity=10,
        entry_price=1000.0, product=ProductType.DELIVERY, last_price=1000.0,
    )
    defaults.update(overrides)
    return monitor.describe_position(
        monitor.BrokerPosition(**defaults), risk, store, "5minute"
    )


def test_levels_are_computed_from_stored_history(store, risk):
    result = describe(store, risk)
    assert result["levels_available"]
    assert result["atr"] > 0
    assert result["stoploss"] < result["entry_price"] < result["target"]
    assert result["risk_reward"] == pytest.approx(2.5 / 1.5, rel=0.01)


def test_short_levels_are_inverted(store, risk):
    result = describe(store, risk, side=Side.SELL)
    assert result["target"] < result["entry_price"] < result["stoploss"]


def test_missing_history_is_reported_not_guessed(store, risk):
    """Inventing levels without ATR would produce a stop that means nothing."""
    result = describe(store, risk, symbol="UNSEEN")
    assert not result["levels_available"]
    assert "Not enough stored history" in result["note"]
    assert "stoploss" not in result


def test_mtf_position_is_flagged_as_not_exitable(store, risk):
    result = describe(store, risk, product=ProductType.MTF)
    assert result["levels_available"], "levels must still be computed for monitoring"
    assert not result["exitable_via_api"]
    assert "ICICI Direct" in result["note"]
    assert result["product_label"] == "MTF"


def test_delivery_position_is_exitable(store, risk):
    result = describe(store, risk, product=ProductType.DELIVERY)
    assert result["exitable_via_api"]


# ----------------------------------------------------------------------
# Level status
# ----------------------------------------------------------------------
def test_status_open_between_levels(store, risk):
    assert describe(store, risk, last_price=1000.0)["status"] in {
        "open", "approaching_stop", "approaching_target"
    }


def test_stop_hit_is_detected(store, risk):
    result = describe(store, risk, last_price=500.0)
    assert result["status"] == "stop_hit"
    assert result["progress_pct"] == 0.0


def test_target_hit_is_detected(store, risk):
    result = describe(store, risk, last_price=5000.0)
    assert result["status"] == "target_hit"
    assert result["progress_pct"] == 100.0


def test_short_stop_hit_on_a_rise(store, risk):
    result = describe(store, risk, side=Side.SELL, last_price=5000.0)
    assert result["status"] == "stop_hit"


def test_short_target_hit_on_a_fall(store, risk):
    result = describe(store, risk, side=Side.SELL, last_price=500.0)
    assert result["status"] == "target_hit"


def test_progress_is_bounded(store, risk):
    for price in (1.0, 500.0, 1000.0, 5000.0, 1e6):
        progress = describe(store, risk, last_price=price)["progress_pct"]
        assert 0.0 <= progress <= 100.0


# ----------------------------------------------------------------------
# Collection and alerts
# ----------------------------------------------------------------------
def test_collect_skips_non_positions(store, risk):
    rows = [
        {"stock_code": "TCS", "quantity": 10, "average_price": 1000, "product_type": "cash"},
        {"stock_code": "TCS", "quantity": 0, "average_price": 1000},  # flat
        {"quantity": 5},  # no symbol
    ]
    assert len(monitor.collect(rows, risk, store, "5minute")) == 1


def test_collect_deduplicates_positions_and_holdings(store, risk):
    """Both endpoints can report the same holding; it must appear once."""
    row = {"stock_code": "TCS", "quantity": 10, "average_price": 1000, "product_type": "cash"}
    assert len(monitor.collect([row, dict(row)], risk, store, "5minute")) == 1


def test_same_symbol_in_two_products_is_kept_separate(store, risk):
    rows = [
        {"stock_code": "TCS", "quantity": 10, "average_price": 1000, "product_type": "cash"},
        {"stock_code": "TCS", "quantity": 5, "average_price": 1010, "product_type": "mtf"},
    ]
    assert len(monitor.collect(rows, risk, store, "5minute")) == 2


def test_live_price_overrides_the_broker_price(store, risk):
    rows = [{"stock_code": "TCS", "quantity": 10, "average_price": 1000, "ltp": 1000}]
    described = monitor.collect(
        rows, risk, store, "5minute", live_prices={"TCS": 1234.0}
    )
    assert described[0]["last_price"] == pytest.approx(1234.0)


def test_alerts_only_surface_positions_needing_attention(store, risk):
    rows = [
        {"stock_code": "TCS", "quantity": 10, "average_price": 1000, "ltp": 500},   # stop
        {"stock_code": "AAA", "quantity": 10, "average_price": 1000, "ltp": 1000},  # mid
    ]
    described = monitor.collect(rows, risk, store, "5minute")
    alerts = monitor.alerts_from(described)

    assert any(a["symbol"] == "TCS" and a["status"] == "stop_hit" for a in alerts)


def test_alert_carries_whether_the_app_can_exit(store, risk):
    rows = [
        {"stock_code": "TCS", "quantity": 10, "average_price": 1000, "ltp": 500,
         "product_type": "mtf"},
    ]
    alerts = monitor.alerts_from(monitor.collect(rows, risk, store, "5minute"))
    assert alerts
    assert alerts[0]["exitable_via_api"] is False


def test_empty_input_yields_nothing(store, risk):
    assert monitor.collect([], risk, store, "5minute") == []
    assert monitor.alerts_from([]) == []
