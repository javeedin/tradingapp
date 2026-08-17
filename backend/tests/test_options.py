"""Option contracts, premium-based levels, and option order planning."""

from __future__ import annotations

from datetime import date

import pytest

from app.broker.paper import PaperBroker
from app.data.funds import paper_funds
from app.engine.options import (
    build_contract,
    max_affordable_lots,
    plan_option_order,
    premium_levels,
)
from app.models import (
    LOT_SIZES,
    ExitReason,
    OptionContract,
    OptionRight,
    ProductType,
    Side,
    lot_size_for,
    margin_for,
)

EXPIRY = date(2026, 9, 29)


def contract(**kwargs) -> OptionContract:
    defaults = dict(
        underlying="NIFTY",
        expiry=EXPIRY,
        strike=24300.0,
        right=OptionRight.CALL,
        lot_size=75,
    )
    defaults.update(kwargs)
    return OptionContract(**defaults)


# ----------------------------------------------------------------------
# Rights
# ----------------------------------------------------------------------
def test_right_parses_every_spelling_used_on_screen():
    for value in ("call", "CALL", "ce", "C"):
        assert OptionRight.parse(value) is OptionRight.CALL
    for value in ("put", "PUT", "pe", "p"):
        assert OptionRight.parse(value) is OptionRight.PUT


def test_unknown_right_is_rejected_rather_than_defaulted():
    """Defaulting a bad right to 'call' would trade the wrong side of the market."""
    with pytest.raises(ValueError):
        OptionRight.parse("straddle")


def test_right_has_a_screen_abbreviation():
    assert OptionRight.CALL.short == "CE"
    assert OptionRight.PUT.short == "PE"


# ----------------------------------------------------------------------
# Contracts
# ----------------------------------------------------------------------
def test_key_identifies_the_series_not_the_underlying():
    """Two strikes on one index must be two positions, not one."""
    call = contract().key
    put = contract(right=OptionRight.PUT).key
    other_strike = contract(strike=24400.0).key

    assert len({call, put, other_strike}) == 3


def test_key_is_legible_because_it_shows_up_in_history():
    assert contract().key == "NIFTY 24300 CE 29SEP26"


def test_lots_convert_to_quantity_through_the_lot_size():
    assert contract().lots_to_quantity(2) == 150


def test_negative_lots_never_produce_a_negative_quantity():
    assert contract().lots_to_quantity(-3) == 0


def test_breeze_params_send_the_underlying_not_the_contract_key():
    """The key is ours; Breeze only recognises the underlying's code."""
    params = contract().breeze_params()

    assert params["stock_code"] == "NIFTY"
    assert params["strike_price"] == "24300"
    assert params["right"] == "call"
    assert params["expiry_date"].startswith("2026-09-29T06:00:00")


def test_known_index_lot_sizes_are_resolved():
    assert lot_size_for("NIFTY") == LOT_SIZES["NIFTY"]
    assert lot_size_for("cnxban") == LOT_SIZES["CNXBAN"]


def test_unknown_underlying_falls_back_to_one():
    """So a wrong multiple is impossible — the caller must pass the lot size."""
    assert lot_size_for("SOMESTOCK") == 1


def test_build_contract_accepts_an_iso_expiry_string():
    built = build_contract("NIFTY", "2026-09-29", 24300, "ce")
    assert built.expiry == EXPIRY
    assert built.lot_size == LOT_SIZES["NIFTY"]


def test_build_contract_rejects_a_non_positive_strike():
    with pytest.raises(ValueError):
        build_contract("NIFTY", "2026-09-29", 0, "call")


def test_explicit_lot_size_overrides_the_table():
    """The exchange revises lot sizes; a stale table must not win over the caller."""
    built = build_contract("NIFTY", "2026-09-29", 24300, "call", lot_size=50)
    assert built.lot_size == 50


# ----------------------------------------------------------------------
# Premium levels
# ----------------------------------------------------------------------
def test_buyer_stop_is_below_and_target_above():
    stop, target = premium_levels(100.0, Side.BUY, stop_pct=40, target_pct=80)
    assert stop == 60.0
    assert target == 180.0


def test_writer_levels_are_the_mirror_image():
    """A writer profits as the premium falls, so target is below and stop above."""
    stop, target = premium_levels(100.0, Side.SELL, stop_pct=40, target_pct=80)
    assert stop == 140.0
    assert target == 20.0


def test_a_full_percent_stop_still_leaves_a_triggerable_level():
    """Premium floors at zero, so a 100% stop would never fire."""
    stop, _ = premium_levels(10.0, Side.BUY, stop_pct=100)
    assert stop > 0


def test_a_writer_target_beyond_the_premium_stays_positive():
    _, target = premium_levels(10.0, Side.SELL, target_pct=150)
    assert target > 0


def test_zero_premium_is_rejected():
    with pytest.raises(ValueError):
        premium_levels(0.0, Side.BUY)


# ----------------------------------------------------------------------
# Margin
# ----------------------------------------------------------------------
def test_buying_an_option_costs_the_premium():
    assert margin_for(Side.BUY, 120.0, 75, contract()) == pytest.approx(9000.0)


def test_writing_an_option_blocks_margin_against_the_underlying():
    """The premium collected bears no relation to the margin blocked."""
    blocked = margin_for(Side.SELL, 120.0, 75, contract())

    assert blocked > 120.0 * 75 * 10
    assert blocked == pytest.approx(24300.0 * 75 * 0.15)


def test_equity_shorts_are_unaffected_by_the_option_rule():
    assert margin_for(Side.SELL, 1000.0, 10) == pytest.approx(10_000.0)


# ----------------------------------------------------------------------
# Order plans
# ----------------------------------------------------------------------
def plan(**kwargs):
    defaults = dict(
        contract=contract(),
        premium=120.0,
        side=Side.BUY,
        lots=1,
        funds=paper_funds(500_000.0),
        today=date(2026, 9, 1),
    )
    defaults.update(kwargs)
    return plan_option_order(**defaults)


def test_plan_sizes_in_whole_lots():
    result = plan(lots=2)
    assert result["quantity"] == 150
    assert result["lot_size"] == 75


def test_plan_rejects_a_fractional_intent():
    with pytest.raises(ValueError):
        plan(lots=0)


def test_buy_plan_risks_only_the_premium_between_entry_and_stop():
    result = plan(premium=100.0, lots=1)
    # 40% of a ₹100 premium across 75 units.
    assert result["risk_amount"] == pytest.approx(3000.0)
    assert result["risk_reward"] == pytest.approx(2.0)


def test_writing_carries_an_explicit_unbounded_loss_warning():
    result = plan(side=Side.SELL)
    assert any("unbounded" in w for w in result["warnings"])


def test_writing_requires_far_more_funds_than_buying():
    buying = plan(side=Side.BUY)
    writing = plan(side=Side.SELL)
    assert writing["required"] > buying["required"] * 10


def test_near_expiry_is_called_out():
    result = plan(today=date(2026, 9, 28))
    assert any("decays" in w for w in result["warnings"])


def test_an_expired_contract_is_flagged_and_dated_negative():
    result = plan(today=date(2026, 10, 1))
    assert result["days_to_expiry"] < 0
    assert any("already passed" in w for w in result["warnings"])


def test_cheap_far_otm_premiums_are_called_out_for_buyers():
    result = plan(premium=2.0)
    assert any("out-of-the-money" in w for w in result["warnings"])


def test_a_comfortable_buy_carries_no_warnings():
    assert plan(premium=150.0)["warnings"] == []


def test_unaffordable_plans_report_the_largest_size_that_fits():
    result = plan(premium=200.0, lots=50, funds=paper_funds(50_000.0))

    assert result["affordable"] is False
    assert result["shortfall"] > 0
    assert 0 < result["max_affordable_lots"] < 50


def test_max_affordable_lots_is_zero_when_one_lot_does_not_fit():
    assert (
        max_affordable_lots(paper_funds(100.0), contract(), 120.0, Side.BUY) == 0
    )


# ----------------------------------------------------------------------
# Round trip through the paper broker
# ----------------------------------------------------------------------
def test_paper_broker_opens_an_option_position_keyed_by_contract():
    broker = PaperBroker(starting_capital=500_000)
    series = contract()

    order = broker.buy(
        symbol=series.key,
        quantity=75,
        price=120.0,
        stoploss=72.0,
        target=216.0,
        product=ProductType.OPTIONS,
        contract=series,
    )

    assert order.status.value == "filled"
    position = broker.get_position(series.key)
    assert position is not None
    assert position.contract == series
    assert position.product is ProductType.OPTIONS


def test_two_strikes_on_one_underlying_are_two_positions():
    broker = PaperBroker(starting_capital=500_000)
    for strike in (24300.0, 24400.0):
        series = contract(strike=strike)
        broker.buy(
            symbol=series.key,
            quantity=75,
            price=100.0,
            stoploss=60.0,
            target=180.0,
            product=ProductType.OPTIONS,
            contract=series,
        )

    assert broker.open_position_count == 2


def test_closing_an_option_position_records_the_contract_on_the_trade():
    broker = PaperBroker(starting_capital=500_000)
    series = contract()
    broker.buy(
        symbol=series.key,
        quantity=75,
        price=100.0,
        stoploss=60.0,
        target=180.0,
        product=ProductType.OPTIONS,
        contract=series,
    )

    trade = broker.close_position(series.key, 180.0, ExitReason.TARGET)

    assert trade is not None
    assert trade.contract == series
    assert trade.to_dict()["contract"]["label"].startswith("NIFTY 24300 CE")


def test_writing_an_option_blocks_and_releases_the_same_margin():
    """A leak here would silently drain or inflate the simulated account."""
    broker = PaperBroker(starting_capital=5_000_000)
    series = contract()
    before = broker.cash

    broker.sell(
        symbol=series.key,
        quantity=75,
        price=100.0,
        stoploss=140.0,
        target=20.0,
        product=ProductType.OPTIONS,
        contract=series,
    )
    blocked = before - broker.cash
    assert blocked > 100.0 * 75  # margin, not premium

    trade = broker.close_position(series.key, 100.0, ExitReason.SIGNAL)

    assert trade is not None
    # Once flat, cash must move by exactly the trade's net P&L. Any mismatch means
    # the margin released differs from the margin blocked.
    assert broker.cash == pytest.approx(before + trade.net_pnl, abs=0.01)


def test_writing_beyond_the_margin_is_refused():
    broker = PaperBroker(starting_capital=50_000)
    series = contract()

    order = broker.sell(
        symbol=series.key,
        quantity=75,
        price=100.0,
        stoploss=140.0,
        target=20.0,
        product=ProductType.OPTIONS,
        contract=series,
    )

    assert order.status.value == "rejected"
    assert "Insufficient cash" in order.message


def test_equity_orders_still_carry_no_contract():
    """The contract field must stay invisible to the equity path."""
    broker = PaperBroker(starting_capital=500_000)
    broker.buy(symbol="RELIND", quantity=10, price=1000.0, stoploss=980.0, target=1040.0)

    position = broker.get_position("RELIND")
    assert position is not None
    assert position.contract is None
    assert "contract" not in position.to_dict()
