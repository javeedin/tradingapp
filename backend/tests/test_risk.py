"""Position sizing, stops, and account-level limits."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models import ExitReason, Position, ProductType, Side
from app.risk.manager import RiskManager


@pytest.fixture
def risk() -> RiskManager:
    # max_position_pct=100 so the risk rule binds unless a test says otherwise.
    return RiskManager(
        risk_per_trade_pct=1.0,
        max_daily_loss_pct=3.0,
        max_open_positions=5,
        max_position_pct=100.0,
        atr_stop_multiplier=1.5,
        atr_target_multiplier=2.5,
    )


def make_position(side: Side = Side.BUY, entry: float = 1000.0) -> Position:
    stop = entry - 15 if side is Side.BUY else entry + 15
    target = entry + 25 if side is Side.BUY else entry - 25
    return Position(
        symbol="TEST",
        side=side,
        quantity=10,
        entry_price=entry,
        entry_time=datetime(2025, 1, 1, 10, 0),
        product=ProductType.DELIVERY,
        stoploss=stop,
        target=target,
    )


# ----------------------------------------------------------------------
# Stop / target placement
# ----------------------------------------------------------------------
def test_long_stop_below_and_target_above(risk):
    stop, target = risk.stop_and_target(entry=1000.0, atr=10.0, side=Side.BUY)
    assert stop == pytest.approx(985.0)
    assert target == pytest.approx(1025.0)


def test_short_stop_above_and_target_below(risk):
    stop, target = risk.stop_and_target(entry=1000.0, atr=10.0, side=Side.SELL)
    assert stop == pytest.approx(1015.0)
    assert target == pytest.approx(975.0)


def test_stop_distance_has_a_floor(risk):
    """A near-zero ATR must not produce a stop touching the entry price."""
    stop, _ = risk.stop_and_target(entry=1000.0, atr=0.0001, side=Side.BUY)
    assert stop < 1000.0
    assert 1000.0 - stop >= 1000.0 * 0.001 - 1e-9


def test_stops_scale_with_volatility(risk):
    quiet, _ = risk.stop_and_target(1000.0, atr=5.0, side=Side.BUY)
    wild, _ = risk.stop_and_target(1000.0, atr=40.0, side=Side.BUY)
    assert (1000 - wild) > (1000 - quiet)


# ----------------------------------------------------------------------
# Position sizing
# ----------------------------------------------------------------------
def test_size_risks_the_configured_fraction(risk):
    result = risk.size_position(
        entry=1000.0, atr=10.0, side=Side.BUY, equity=100_000, available_cash=100_000
    )
    assert result.approved
    # 1% of 100k = ₹1000 budget / ₹15 stop distance = 66 shares.
    assert result.quantity == 66
    assert result.risk_amount == pytest.approx(990.0)
    assert result.binding_constraint == "risk_per_trade"
    assert not result.was_capped


def test_wider_stop_gives_smaller_position(risk):
    # Entry and ATR chosen so neither the cash nor the exposure cap binds,
    # isolating the risk rule itself.
    tight = risk.size_position(100.0, 2.0, Side.BUY, 100_000, 100_000)
    wide = risk.size_position(100.0, 8.0, Side.BUY, 100_000, 100_000)

    assert tight.binding_constraint == "risk_per_trade"
    assert wide.binding_constraint == "risk_per_trade"
    assert tight.quantity > wide.quantity
    # Both risk roughly the same rupee amount — that is the point of the rule.
    # They differ slightly only because quantity is rounded down to whole shares.
    assert tight.risk_amount == pytest.approx(wide.risk_amount, rel=0.05)


def test_cash_cap_can_mask_the_risk_rule():
    """A high-priced share can exhaust cash before the risk budget is used.

    Worth asserting explicitly: it is the same class of surprise as the exposure
    cap, and the binding constraint is what makes it visible.
    """
    # Exposure cap set out of the way so cash is the constraint under test.
    manager = RiskManager(risk_per_trade_pct=1.0, max_position_pct=500.0)
    result = manager.size_position(1000.0, 5.0, Side.BUY, 100_000, available_cash=100_000)
    assert result.approved
    assert result.binding_constraint == "available_cash"
    assert result.quantity == 100
    assert result.intended_quantity > result.quantity
    assert result.was_capped


def test_position_cap_binds_and_is_reported():
    """The exposure cap silently becoming the real sizing rule must be visible."""
    manager = RiskManager(risk_per_trade_pct=1.0, max_position_pct=20.0)
    result = manager.size_position(1000.0, 10.0, Side.BUY, 100_000, 100_000)
    assert result.approved
    assert result.intended_quantity == 66
    assert result.quantity == 20  # ₹20k cap / ₹1000 per share
    assert result.binding_constraint == "max_position_pct"
    assert result.was_capped
    assert result.effective_risk_pct(100_000) == pytest.approx(0.3)


def test_cash_shortage_caps_size(risk):
    result = risk.size_position(1000.0, 10.0, Side.BUY, 100_000, available_cash=5_000)
    assert result.approved
    assert result.quantity == 5
    assert result.binding_constraint == "available_cash"


def test_rejects_when_max_positions_reached(risk):
    result = risk.size_position(
        1000.0, 10.0, Side.BUY, 100_000, 100_000, open_positions=5
    )
    assert not result.approved
    assert "Max open positions" in result.reason


def test_rejects_when_budget_below_one_share(risk):
    result = risk.size_position(1000.0, 10.0, Side.BUY, equity=100, available_cash=100)
    assert not result.approved


def test_rejects_invalid_inputs(risk):
    assert not risk.size_position(0.0, 10.0, Side.BUY, 100_000, 100_000).approved
    assert not risk.size_position(1000.0, 0.0, Side.BUY, 100_000, 100_000).approved
    assert not risk.size_position(1000.0, 10.0, Side.BUY, 0, 0).approved


def test_lot_size_rounds_down_to_whole_lots(risk):
    result = risk.size_position(
        1000.0, 10.0, Side.BUY, 100_000, 100_000, lot_size=25
    )
    assert result.approved
    assert result.quantity == 50  # 66 -> 2 whole lots
    assert result.quantity % 25 == 0


def test_rejects_when_one_lot_exceeds_budget(risk):
    result = risk.size_position(
        1000.0, 10.0, Side.BUY, 100_000, 100_000, lot_size=500
    )
    assert not result.approved
    assert result.binding_constraint == "lot_size"


def test_halted_manager_refuses_to_size(risk):
    risk.halt("kill switch")
    assert not risk.size_position(1000.0, 10.0, Side.BUY, 100_000, 100_000).approved


# ----------------------------------------------------------------------
# Trailing stops
# ----------------------------------------------------------------------
def test_trailing_stop_tightens_on_a_long(risk):
    position = make_position(Side.BUY, 1000.0)
    position.update_price(1060.0)
    assert risk.update_trailing_stop(position, atr=10.0)
    assert position.stoploss == pytest.approx(1045.0)  # 1060 - 1.5*10


def test_trailing_stop_never_widens(risk):
    """Widening a stop turns a bounded loss into an unbounded one."""
    position = make_position(Side.BUY, 1000.0)
    position.update_price(1060.0)
    risk.update_trailing_stop(position, atr=10.0)
    tightened = position.stoploss

    position.update_price(1005.0)  # price falls back
    assert not risk.update_trailing_stop(position, atr=10.0)
    assert position.stoploss == tightened


def test_trailing_stop_tightens_on_a_short(risk):
    position = make_position(Side.SELL, 1000.0)
    position.update_price(940.0)
    assert risk.update_trailing_stop(position, atr=10.0)
    assert position.stoploss == pytest.approx(955.0)


def test_trailing_disabled_is_a_no_op():
    manager = RiskManager(use_trailing_stop=False)
    position = make_position(Side.BUY, 1000.0)
    position.update_price(1200.0)
    assert not manager.update_trailing_stop(position, atr=10.0)


# ----------------------------------------------------------------------
# Exit checks
# ----------------------------------------------------------------------
def test_stop_takes_priority_when_a_bar_spans_both_levels(risk):
    """The pessimistic assumption keeps backtests honest."""
    position = make_position(Side.BUY, 1000.0)  # stop 985, target 1025
    result = risk.check_exit(position, high=1030.0, low=980.0)
    assert result is not None
    reason, price = result
    assert reason is ExitReason.STOPLOSS
    assert price == pytest.approx(985.0)


def test_target_fires_when_stop_untouched(risk):
    position = make_position(Side.BUY, 1000.0)
    reason, price = risk.check_exit(position, high=1030.0, low=995.0)
    assert reason is ExitReason.TARGET
    assert price == pytest.approx(1025.0)


def test_no_exit_inside_the_range(risk):
    position = make_position(Side.BUY, 1000.0)
    assert risk.check_exit(position, high=1010.0, low=995.0) is None


def test_short_stop_triggers_on_a_rise(risk):
    position = make_position(Side.SELL, 1000.0)  # stop 1015
    reason, _ = risk.check_exit(position, high=1020.0, low=1005.0)
    assert reason is ExitReason.STOPLOSS


def test_intraday_squareoff_after_cutoff(risk):
    """Squaring off is driven by the strategy flag, not by the Breeze product.

    Cash positions are routinely traded intraday, and the leveraged MARGIN
    product cannot even be placed through the API — so deriving this from the
    product type was both wrong and unusable.
    """
    position = make_position(Side.BUY, 1000.0)
    position.update_price(1000.0)
    result = risk.check_exit(
        position,
        high=1005.0,
        low=998.0,
        now=datetime(2025, 1, 1, 15, 20),
        intraday=True,
    )
    assert result is not None
    assert result[0] is ExitReason.SQUAREOFF


def test_positional_trades_survive_the_cutoff(risk):
    position = make_position(Side.BUY, 1000.0)
    position.update_price(1000.0)
    assert (
        risk.check_exit(
            position, 1005.0, 998.0, now=datetime(2025, 1, 1, 15, 20), intraday=False
        )
        is None
    )


def test_squareoff_does_not_fire_before_the_cutoff(risk):
    position = make_position(Side.BUY, 1000.0)
    position.update_price(1000.0)
    assert (
        risk.check_exit(
            position, 1005.0, 998.0, now=datetime(2025, 1, 1, 11, 0), intraday=True
        )
        is None
    )


# ----------------------------------------------------------------------
# Daily loss limit
# ----------------------------------------------------------------------
def test_daily_loss_limit_halts_trading(risk):
    when = datetime(2025, 1, 2, 11, 0)
    risk.reset_day(when)
    risk.record_pnl(-3_500, when)
    assert risk.check_daily_limit(100_000, when)
    assert risk.is_halted
    assert "Daily loss limit" in risk.halted_reason


def test_below_the_limit_keeps_trading(risk):
    when = datetime(2025, 1, 2, 11, 0)
    risk.reset_day(when)
    risk.record_pnl(-2_000, when)
    assert not risk.check_daily_limit(100_000, when)
    assert not risk.is_halted


def test_new_day_clears_the_halt(risk):
    day_one = datetime(2025, 1, 2, 11, 0)
    risk.reset_day(day_one)
    risk.record_pnl(-5_000, day_one)
    assert risk.check_daily_limit(100_000, day_one)

    day_two = datetime(2025, 1, 3, 9, 30)
    assert not risk.check_daily_limit(100_000, day_two)
    assert risk.daily_pnl == 0.0


def test_resume_clears_a_manual_halt(risk):
    risk.halt("manual")
    assert risk.is_halted
    risk.resume()
    assert not risk.is_halted


def test_risk_reward_ratio(risk):
    assert risk.risk_reward_ratio() == pytest.approx(2.5 / 1.5)
