"""Exit policies: stoploss-only, capped averaging, and unlimited averaging."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models import ExitPolicy, ExitReason, Position, ProductType, Side
from app.risk.manager import RiskManager

EQUITY = 100_000.0


def make_position(
    entry: float = 1000.0, quantity: int = 10, side: Side = Side.BUY
) -> Position:
    stop = entry - 15 if side is Side.BUY else entry + 15
    target = entry + 25 if side is Side.BUY else entry - 25
    return Position(
        symbol="TEST",
        side=side,
        quantity=quantity,
        entry_price=entry,
        entry_time=datetime(2025, 1, 1, 10, 0),
        product=ProductType.DELIVERY,
        stoploss=stop,
        target=target,
    )


def manager(policy: ExitPolicy, **kwargs) -> RiskManager:
    defaults = dict(
        exit_policy=policy,
        max_adds=2,
        add_trigger_atr=1.0,
        max_symbol_exposure_pct=25.0,
        max_position_pct=100.0,
    )
    defaults.update(kwargs)
    return RiskManager(**defaults)


# ----------------------------------------------------------------------
# Policy properties
# ----------------------------------------------------------------------
def test_only_the_no_stop_policy_lacks_a_stoploss():
    assert ExitPolicy.STOP_ONLY.has_stoploss
    assert ExitPolicy.CAPPED_AVERAGING.has_stoploss
    assert not ExitPolicy.AVERAGE_NO_STOP.has_stoploss


def test_averaging_policies_are_identified():
    assert not ExitPolicy.STOP_ONLY.averages_down
    assert ExitPolicy.CAPPED_AVERAGING.averages_down
    assert ExitPolicy.AVERAGE_NO_STOP.averages_down


def test_every_policy_carries_a_risk_note():
    """The UI shows these next to the choice, so none may be empty."""
    for policy in ExitPolicy:
        assert policy.label
        assert policy.risk_note


def test_the_no_stop_note_states_the_consequence():
    note = ExitPolicy.AVERAGE_NO_STOP.risk_note.lower()
    assert "no stoploss" in note
    assert "daily loss limit" in note


# ----------------------------------------------------------------------
# Position averaging arithmetic
# ----------------------------------------------------------------------
def test_add_produces_a_weighted_average_entry():
    position = make_position(entry=1000.0, quantity=10)
    position.add(10, 900.0)

    assert position.quantity == 20
    assert position.entry_price == pytest.approx(950.0)
    assert position.adds == 1
    # The original entry is preserved for measuring adverse distance.
    assert position.initial_entry_price == pytest.approx(1000.0)


def test_unequal_add_weights_correctly():
    position = make_position(entry=1000.0, quantity=10)
    position.add(30, 900.0)
    assert position.entry_price == pytest.approx((10_000 + 27_000) / 40)


def test_successive_adds_accumulate():
    position = make_position(entry=1000.0, quantity=10)
    position.add(10, 900.0)
    position.add(10, 800.0)
    assert position.adds == 2
    assert position.quantity == 30
    assert position.entry_price == pytest.approx(900.0)


def test_add_rejects_nonsense_input():
    position = make_position()
    with pytest.raises(ValueError):
        position.add(0, 900.0)
    with pytest.raises(ValueError):
        position.add(10, 0.0)


def test_drawdown_is_measured_from_the_original_entry():
    """Averaging pulls the average toward price and would understate the loss."""
    position = make_position(entry=1000.0, quantity=10)
    position.add(10, 900.0)
    position.update_price(900.0)

    # Average entry is 950, so unrealised looks like -5%...
    assert position.unrealized_pnl_pct == pytest.approx(-5.26, abs=0.1)
    # ...but the position is 10% below where it was first bought.
    assert position.drawdown_from_initial_pct == pytest.approx(-10.0)


def test_short_drawdown_is_inverted():
    position = make_position(entry=1000.0, side=Side.SELL)
    position.update_price(1100.0)
    assert position.drawdown_from_initial_pct == pytest.approx(-10.0)


# ----------------------------------------------------------------------
# STOP_ONLY
# ----------------------------------------------------------------------
def test_stop_only_never_averages():
    risk = manager(ExitPolicy.STOP_ONLY)
    position = make_position()
    position.update_price(900.0)

    decision = risk.should_average_down(position, 900.0, atr=10.0, equity=EQUITY)
    assert not decision.should_add
    assert "stoploss-only" in decision.reason


def test_stop_only_still_exits_on_the_stop():
    risk = manager(ExitPolicy.STOP_ONLY)
    position = make_position()  # stop at 985
    result = risk.check_exit(position, high=995.0, low=980.0)
    assert result is not None
    assert result[0] is ExitReason.STOPLOSS


# ----------------------------------------------------------------------
# Add triggering
# ----------------------------------------------------------------------
def test_no_add_while_the_position_is_in_profit():
    risk = manager(ExitPolicy.CAPPED_AVERAGING)
    position = make_position()
    position.update_price(1050.0)

    decision = risk.should_average_down(position, 1050.0, atr=10.0, equity=EQUITY)
    assert not decision.should_add
    assert "not in loss" in decision.reason


def test_no_add_before_the_trigger_distance():
    """One ATR of adverse move is required for the first add."""
    risk = manager(ExitPolicy.CAPPED_AVERAGING, add_trigger_atr=1.0)
    position = make_position()
    position.update_price(995.0)  # only 5 against, needs 10

    decision = risk.should_average_down(position, 995.0, atr=10.0, equity=EQUITY)
    assert not decision.should_add
    assert "adverse move" in decision.reason


def test_add_fires_once_the_trigger_distance_is_reached():
    risk = manager(ExitPolicy.CAPPED_AVERAGING, add_trigger_atr=1.0)
    position = make_position()
    position.update_price(989.0)  # 11 against, needs 10

    decision = risk.should_average_down(position, 989.0, atr=10.0, equity=EQUITY)
    assert decision.should_add
    assert decision.quantity > 0
    assert decision.adds_after == 1


def test_each_successive_add_needs_a_wider_move():
    """The ladder widens so adds slow down rather than accelerate."""
    risk = manager(ExitPolicy.CAPPED_AVERAGING, add_trigger_atr=1.0)
    position = make_position()

    position.update_price(989.0)
    first = risk.should_average_down(position, 989.0, atr=10.0, equity=EQUITY)
    assert first.should_add
    position.add(first.quantity, 989.0)

    # 11 against was enough for add #1; add #2 needs 20.
    position.update_price(985.0)
    second = risk.should_average_down(position, 985.0, atr=10.0, equity=EQUITY)
    assert not second.should_add

    position.update_price(975.0)  # 25 against
    third = risk.should_average_down(position, 975.0, atr=10.0, equity=EQUITY)
    assert third.should_add


def test_short_position_adds_when_price_rises():
    risk = manager(ExitPolicy.CAPPED_AVERAGING)
    position = make_position(side=Side.SELL)
    position.update_price(1015.0)

    decision = risk.should_average_down(position, 1015.0, atr=10.0, equity=EQUITY)
    assert decision.should_add


# ----------------------------------------------------------------------
# CAPPED_AVERAGING
# ----------------------------------------------------------------------
def test_capped_averaging_stops_at_the_add_limit():
    risk = manager(ExitPolicy.CAPPED_AVERAGING, max_adds=2)
    position = make_position()
    position.adds = 2
    position.update_price(900.0)

    decision = risk.should_average_down(position, 900.0, atr=10.0, equity=EQUITY)
    assert not decision.should_add
    assert "Add limit reached" in decision.reason
    assert "final stop" in decision.reason


def test_capped_averaging_keeps_a_stop_after_the_last_add():
    """The whole point of the capped policy: a floor still exists."""
    risk = manager(ExitPolicy.CAPPED_AVERAGING, max_adds=2)
    position = make_position(entry=1000.0)
    position.adds = 2
    position.entry_price = 950.0

    stoploss, target = risk.levels_after_add(position, atr=10.0)
    assert stoploss is not None
    assert stoploss < position.entry_price
    assert target > position.entry_price


def test_final_stop_sits_beyond_the_last_add():
    """A stop at the plain ATR distance would be hit by the noise that triggered
    the add itself."""
    risk = manager(ExitPolicy.CAPPED_AVERAGING, max_adds=2)

    mid = make_position(entry=1000.0)
    mid.adds = 1
    mid_stop, _ = risk.levels_after_add(mid, atr=10.0)

    final = make_position(entry=1000.0)
    final.adds = 2
    final_stop, _ = risk.levels_after_add(final, atr=10.0)

    assert final_stop < mid_stop


def test_capped_averaging_still_honours_the_stop_on_exit():
    risk = manager(ExitPolicy.CAPPED_AVERAGING)
    position = make_position()
    result = risk.check_exit(position, high=995.0, low=980.0)
    assert result is not None
    assert result[0] is ExitReason.STOPLOSS


# ----------------------------------------------------------------------
# AVERAGE_NO_STOP
# ----------------------------------------------------------------------
def test_no_stop_policy_ignores_the_stoploss_on_exit():
    """The policy removes the floor; honouring a stop would reintroduce it."""
    risk = manager(ExitPolicy.AVERAGE_NO_STOP)
    position = make_position()  # stop at 985
    assert risk.check_exit(position, high=995.0, low=980.0) is None


def test_no_stop_policy_still_exits_at_target():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP)
    position = make_position()  # target 1025
    result = risk.check_exit(position, high=1030.0, low=1010.0)
    assert result is not None
    assert result[0] is ExitReason.TARGET


def test_no_stop_policy_returns_no_stop_level():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP)
    position = make_position()
    stoploss, target = risk.levels_after_add(position, atr=10.0)
    assert stoploss is None
    assert target > 0


def test_no_stop_policy_ignores_the_add_limit():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP, max_adds=2)
    position = make_position(entry=1000.0, quantity=1)
    position.adds = 5
    position.update_price(900.0)

    decision = risk.should_average_down(position, 900.0, atr=10.0, equity=EQUITY)
    assert decision.should_add, "unlimited averaging must not stop at max_adds"


def test_trailing_stop_is_disabled_without_a_stoploss():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP)
    position = make_position()
    position.update_price(1100.0)
    assert not risk.update_trailing_stop(position, atr=10.0)


# ----------------------------------------------------------------------
# Exposure ceiling — the only backstop under AVERAGE_NO_STOP
# ----------------------------------------------------------------------
def test_exposure_ceiling_blocks_further_adds():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP, max_symbol_exposure_pct=10.0)
    # 10% of 100k = 10,000 ceiling; this position is already at it.
    position = make_position(entry=1000.0, quantity=10)
    position.update_price(900.0)

    decision = risk.should_average_down(position, 900.0, atr=10.0, equity=EQUITY)
    assert not decision.should_add
    assert "ceiling" in decision.reason


def test_add_is_trimmed_to_the_remaining_headroom():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP, max_symbol_exposure_pct=15.0)
    position = make_position(entry=1000.0, quantity=10)  # 10,000 of a 15,000 ceiling
    position.update_price(980.0)

    decision = risk.should_average_down(position, 980.0, atr=10.0, equity=EQUITY)
    assert decision.should_add
    # Only ~5,000 of headroom, so the add is smaller than the original 10.
    assert decision.quantity < 10
    assert decision.exposure_after <= EQUITY * 0.15 + 1


def test_no_add_when_headroom_is_under_one_share():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP, max_symbol_exposure_pct=10.05)
    position = make_position(entry=1000.0, quantity=10)
    position.update_price(980.0)

    decision = risk.should_average_down(position, 980.0, atr=10.0, equity=EQUITY)
    assert not decision.should_add
    assert "headroom" in decision.reason


def test_ceiling_scales_with_equity():
    risk = manager(ExitPolicy.AVERAGE_NO_STOP, max_symbol_exposure_pct=10.0)
    position = make_position(entry=1000.0, quantity=10)
    position.update_price(980.0)

    # Same position, ten times the equity: headroom now exists.
    assert not risk.should_average_down(position, 980.0, 10.0, 100_000).should_add
    assert risk.should_average_down(position, 980.0, 10.0, 1_000_000).should_add


def test_invalid_inputs_are_refused():
    risk = manager(ExitPolicy.CAPPED_AVERAGING)
    position = make_position()
    position.update_price(900.0)

    for atr, equity in ((0.0, EQUITY), (10.0, 0.0)):
        assert not risk.should_average_down(position, 900.0, atr, equity).should_add


# ----------------------------------------------------------------------
# Status reporting
# ----------------------------------------------------------------------
def test_status_reports_the_active_policy():
    status = manager(ExitPolicy.AVERAGE_NO_STOP).status(EQUITY)
    assert status["exit_policy"] == "average_no_stop"
    assert status["has_stoploss"] is False
    assert status["averages_down"] is True
    assert status["trailing_stop"] is False
    assert status["exit_policy_note"]
