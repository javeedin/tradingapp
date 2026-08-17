"""End-to-end averaging through the paper broker.

The unit tests cover the decision; these cover the money actually moving —
cash deducted, average recomputed, levels repositioned, rejections surfaced.
"""

from __future__ import annotations

import pytest

from app.broker.paper import PaperBroker
from app.models import ExitPolicy, ProductType
from app.risk.manager import RiskManager

CAPITAL = 200_000.0


@pytest.fixture
def broker() -> PaperBroker:
    return PaperBroker(CAPITAL)


def opened(broker: PaperBroker, entry: float = 1000.0, quantity: int = 10):
    broker.buy("TEST", quantity, entry, stoploss=entry - 15, target=entry + 25,
               product=ProductType.DELIVERY)
    return broker.get_position("TEST")


def test_averaging_recomputes_the_position(broker):
    position = opened(broker, 1000.0, 10)
    original_cash = broker.cash

    order = broker.add_to_position("TEST", 10, 900.0, stoploss=880.0, target=1000.0)

    assert order.status.value == "filled"
    assert position.quantity == 20
    assert position.adds == 1
    # Weighted average of the two fills, both including slippage.
    assert 940 < position.entry_price < 960
    assert position.stoploss == pytest.approx(880.0)
    assert broker.cash < original_cash


def test_averaging_without_a_stop_leaves_the_level_untouched(broker):
    """Under a no-stop policy the caller passes None; nothing should invent one."""
    position = opened(broker, 1000.0, 10)
    before = position.stoploss

    broker.add_to_position("TEST", 10, 900.0, stoploss=None, target=1000.0)
    assert position.stoploss == pytest.approx(before)


def test_averaging_an_unknown_symbol_is_rejected(broker):
    order = broker.add_to_position("NOPE", 10, 900.0)
    assert order.status.value == "rejected"
    assert "No open position" in order.message


def test_averaging_beyond_cash_is_rejected(broker):
    small = PaperBroker(12_000)
    small.buy("TEST", 10, 1000.0, stoploss=985, target=1025, product=ProductType.DELIVERY)

    order = small.add_to_position("TEST", 100, 900.0)
    assert order.status.value == "rejected"
    assert "Insufficient cash" in order.message


def test_averaging_rejects_nonsense_quantities(broker):
    opened(broker)
    assert broker.add_to_position("TEST", 0, 900.0).status.value == "rejected"
    assert broker.add_to_position("TEST", 10, 0.0).status.value == "rejected"


def test_equity_reflects_the_averaged_position(broker):
    """Averaging moves cash into stock; equity should only lose the costs."""
    opened(broker, 1000.0, 10)
    before = broker.equity

    broker.add_to_position("TEST", 10, 900.0, stoploss=880.0, target=1000.0)
    broker.update_price("TEST", 900.0)

    # Equity fell because the position is underwater, not because cash vanished.
    assert broker.equity < before
    assert broker.equity > CAPITAL * 0.9


def test_full_capped_ladder_then_a_final_stop(broker):
    """Walk a position down through both permitted adds and confirm a stop remains."""
    risk = RiskManager(
        exit_policy=ExitPolicy.CAPPED_AVERAGING,
        max_adds=2,
        add_trigger_atr=1.0,
        max_symbol_exposure_pct=100.0,
        max_position_pct=100.0,
    )
    position = opened(broker, 1000.0, 10)
    atr = 10.0

    for expected_add in (1, 2):
        # Each add needs a wider adverse move than the last.
        price = position.initial_entry_price - atr * 1.0 * expected_add - 1
        position.update_price(price)

        decision = risk.should_average_down(position, price, atr, broker.equity)
        assert decision.should_add, f"add #{expected_add} should fire: {decision.reason}"

        stoploss, target = risk.levels_after_add(position, atr)
        broker.add_to_position("TEST", decision.quantity, price, stoploss, target)
        assert position.adds == expected_add

    # Third add refused; the final stop now governs.
    price = position.initial_entry_price - 100
    position.update_price(price)
    blocked = risk.should_average_down(position, price, atr, broker.equity)
    assert not blocked.should_add
    assert "Add limit reached" in blocked.reason

    stoploss, _ = risk.levels_after_add(position, atr)
    assert stoploss is not None, "capped averaging must retain a floor"
    assert risk.check_exit(position, high=price, low=stoploss - 1) is not None


def test_unlimited_averaging_is_bounded_only_by_exposure(broker):
    """The ceiling caps exposure, not loss — worth asserting it does at least that."""
    risk = RiskManager(
        exit_policy=ExitPolicy.AVERAGE_NO_STOP,
        add_trigger_atr=1.0,
        max_symbol_exposure_pct=10.0,  # 20,000 of 200,000
        max_position_pct=100.0,
    )
    position = opened(broker, 1000.0, 10)
    atr = 10.0

    for _ in range(10):
        price = position.initial_entry_price - atr * (position.adds + 1) - 1
        position.update_price(price)
        decision = risk.should_average_down(position, price, atr, broker.equity)
        if not decision.should_add:
            break
        broker.add_to_position("TEST", decision.quantity, price, None, position.target)

    exposure = position.entry_price * position.quantity
    assert exposure <= broker.equity * 0.10 * 1.05, "exposure ceiling was breached"
    # And there is genuinely no stop protecting it.
    assert risk.check_exit(position, high=position.last_price, low=1.0) is None
