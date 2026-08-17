"""Paper broker fills, costs, and equity accounting."""

from __future__ import annotations

import pytest

from app.broker.paper import PaperBroker
from app.models import ExitReason, ProductType, Side

CAPITAL = 100_000.0


@pytest.fixture
def broker() -> PaperBroker:
    return PaperBroker(CAPITAL)


# ----------------------------------------------------------------------
# Fills and slippage
# ----------------------------------------------------------------------
def test_buy_fills_above_the_decision_price(broker):
    """Slippage must always work against the trader, never for them."""
    order = broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    assert order.status.value == "filled"
    assert order.filled_price > 1000.0


def test_sell_fills_below_the_decision_price(broker):
    order = broker.sell("TEST", 10, 1000.0, stoploss=1015, target=975)
    assert order.filled_price < 1000.0


def test_exit_slippage_also_works_against_the_trader(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    trade = broker.close_position("TEST", 1000.0)
    assert trade.exit_price < 1000.0  # long exits fill lower


def test_rejects_duplicate_position(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    second = broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    assert second.status.value == "rejected"
    assert "already open" in second.message


def test_rejects_when_cash_is_short(broker):
    order = broker.buy("TEST", 10_000, 1000.0, stoploss=985, target=1025)
    assert order.status.value == "rejected"
    assert "Insufficient cash" in order.message


def test_rejects_non_positive_quantity(broker):
    assert broker.buy("TEST", 0, 1000.0, 985, 1025).status.value == "rejected"


# ----------------------------------------------------------------------
# P&L
# ----------------------------------------------------------------------
def test_long_profit_and_costs(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    trade = broker.close_position("TEST", 1050.0, ExitReason.TARGET)
    assert trade.pnl > 0
    assert trade.costs > 0
    assert trade.net_pnl == pytest.approx(trade.pnl - trade.costs)
    assert trade.is_win


def test_short_profits_when_price_falls(broker):
    broker.sell("TEST", 10, 1000.0, stoploss=1015, target=975)
    trade = broker.close_position("TEST", 950.0, ExitReason.TARGET)
    assert trade.pnl > 0
    assert trade.side is Side.SELL


def test_short_loses_when_price_rises(broker):
    broker.sell("TEST", 10, 1000.0, stoploss=1015, target=975)
    trade = broker.close_position("TEST", 1050.0, ExitReason.STOPLOSS)
    assert trade.net_pnl < 0


def test_round_trip_at_the_same_price_loses_costs(broker):
    """A flat round trip must lose exactly brokerage plus both sides of slippage."""
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    broker.close_position("TEST", 1000.0)
    assert broker.equity < CAPITAL
    assert broker.equity == pytest.approx(CAPITAL, rel=0.01)


# ----------------------------------------------------------------------
# Equity accounting
# ----------------------------------------------------------------------
def test_opening_a_long_barely_moves_equity(broker):
    broker.buy("TEST", 50, 1000.0, stoploss=985, target=1025)
    assert broker.equity == pytest.approx(CAPITAL, rel=0.005)


def test_opening_a_short_does_not_destroy_equity(broker):
    """Regression: reserved short margin was being dropped from equity entirely.

    Equity drives position sizing and the daily-loss-limit check, so a short
    that halved reported equity silently under-sized every later trade and
    could trip the loss limit on an account that was actually flat.
    """
    broker.sell("TEST", 50, 1000.0, stoploss=1015, target=975)
    assert broker.equity == pytest.approx(CAPITAL, rel=0.005)


def test_short_equity_tracks_unrealised_profit(broker):
    broker.sell("TEST", 50, 1000.0, stoploss=1015, target=975)
    baseline = broker.equity
    broker.update_price("TEST", 980.0)
    assert broker.equity > baseline


def test_long_equity_tracks_unrealised_loss(broker):
    broker.buy("TEST", 50, 1000.0, stoploss=985, target=1025)
    baseline = broker.equity
    broker.update_price("TEST", 960.0)
    assert broker.equity < baseline


def test_cash_is_restored_after_closing(broker):
    broker.buy("TEST", 50, 1000.0, stoploss=985, target=1025)
    assert broker.cash < CAPITAL
    broker.close_position("TEST", 1000.0)
    assert broker.cash == pytest.approx(broker.equity)


# ----------------------------------------------------------------------
# Stop / target simulation
# ----------------------------------------------------------------------
def test_check_stops_fires_the_stoploss(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    closed = broker.check_stops({"TEST": (995.0, 980.0)})
    assert len(closed) == 1
    assert closed[0].exit_reason is ExitReason.STOPLOSS
    assert not broker.has_position("TEST")


def test_check_stops_fires_the_target(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    closed = broker.check_stops({"TEST": (1030.0, 1005.0)})
    assert closed[0].exit_reason is ExitReason.TARGET


def test_stop_wins_when_a_bar_spans_both(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    closed = broker.check_stops({"TEST": (1030.0, 980.0)})
    assert closed[0].exit_reason is ExitReason.STOPLOSS


def test_check_stops_leaves_untouched_positions(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    assert broker.check_stops({"TEST": (1010.0, 995.0)}) == []
    assert broker.has_position("TEST")


# ----------------------------------------------------------------------
# Bulk operations
# ----------------------------------------------------------------------
def test_close_all_flattens_everything(broker):
    broker.buy("A", 10, 1000.0, stoploss=985, target=1025)
    broker.buy("B", 10, 500.0, stoploss=490, target=515)
    closed = broker.close_all({"A": 1010.0, "B": 505.0}, ExitReason.KILL_SWITCH)
    assert len(closed) == 2
    assert broker.open_position_count == 0
    assert all(t.exit_reason is ExitReason.KILL_SWITCH for t in closed)


def test_reset_restores_starting_state(broker):
    broker.buy("TEST", 10, 1000.0, stoploss=985, target=1025)
    broker.close_position("TEST", 1050.0)
    broker.reset()
    assert broker.cash == CAPITAL
    assert broker.equity == CAPITAL
    assert broker.trades == []
    assert broker.open_position_count == 0


def test_summary_reports_win_rate(broker):
    broker.buy("A", 10, 1000.0, stoploss=985, target=1025)
    broker.close_position("A", 1100.0)
    broker.buy("B", 10, 1000.0, stoploss=985, target=1025)
    broker.close_position("B", 900.0)

    summary = broker.summary()
    assert summary["total_trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["win_rate"] == pytest.approx(50.0)
    assert summary["mode"] == "paper"


def test_closing_an_unknown_symbol_is_a_no_op(broker):
    assert broker.close_position("NOPE", 100.0) is None


def test_only_permitted_products_are_placeable():
    """Breeze prohibits Margin and Option Plus order placement via API."""
    assert ProductType.DELIVERY.placeable_via_api
    assert ProductType.OPTIONS.placeable_via_api
    assert ProductType.FUTURES.placeable_via_api
    assert not ProductType.MARGIN.placeable_via_api
    assert not ProductType.MTF.placeable_via_api
