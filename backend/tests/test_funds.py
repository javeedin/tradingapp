"""Funds normalisation and affordability checks."""

from __future__ import annotations

import pytest

from app.data.funds import Funds, affordability, normalise_funds, paper_funds


# ----------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------
def test_reads_a_standard_payload():
    funds = normalise_funds(
        {
            "available_margin": "50000.50",
            "total_bank": "120000",
            "allocated_equity": "30000",
            "allocated_fno": "20000",
            "block_by_trade_balance": "5000",
        }
    )
    assert funds.available == pytest.approx(50000.50)
    assert funds.bank_balance == pytest.approx(120000)
    assert funds.total_allocated == pytest.approx(50000)
    assert funds.blocked == pytest.approx(5000)


def test_falls_back_through_alternative_keys():
    """Key names vary by account type and SDK version."""
    assert normalise_funds({"unallocated_balance": 42000}).available == pytest.approx(42000)
    assert normalise_funds({"cash_balance": 7000}).available == pytest.approx(7000)
    assert normalise_funds({"bank_account": 9000}).bank_balance == pytest.approx(9000)


def test_blocked_sums_per_segment_when_no_total_is_given():
    funds = normalise_funds(
        {"block_by_trade_equity": 3000, "block_by_trade_fno": 2000, "available_margin": 1}
    )
    assert funds.blocked == pytest.approx(5000)


def test_empty_and_none_payloads_are_safe():
    assert normalise_funds(None).available == 0.0
    assert normalise_funds({}).available == 0.0


def test_unparseable_values_do_not_raise():
    funds = normalise_funds({"available_margin": "not-a-number", "total_bank": None})
    assert funds.available == 0.0


def test_raw_payload_is_preserved():
    """An unexpected payload must be inspectable rather than silently reduced."""
    payload = {"available_margin": 100, "some_new_field": "xyz"}
    assert normalise_funds(payload).raw == payload


def test_paper_funds_use_the_simulated_balance():
    funds = paper_funds(75000)
    assert funds.available == pytest.approx(75000)
    assert funds.source == "paper"


# ----------------------------------------------------------------------
# Affordability
# ----------------------------------------------------------------------
def test_affordable_order_passes():
    check = affordability(paper_funds(100_000), quantity=10, price=1000.0)
    assert check["affordable"]
    assert check["shortfall"] == 0.0
    assert check["notional"] == pytest.approx(10_000)


def test_unaffordable_order_reports_the_shortfall_and_a_workable_quantity():
    """A bare "insufficient funds" leaves the user guessing what would fit."""
    check = affordability(paper_funds(10_000), quantity=100, price=1000.0)
    assert not check["affordable"]
    assert check["shortfall"] > 0
    assert 0 < check["max_affordable_quantity"] < 100


def test_required_exceeds_the_bare_notional():
    """Costs and a slippage buffer are included, because the fill is not the quote."""
    check = affordability(
        paper_funds(1_000_000), quantity=10, price=1000.0, brokerage_pct=0.03
    )
    assert check["required"] > check["notional"]
    assert check["estimated_costs"] > 0
    assert check["buffer"] > 0


def test_the_buffer_makes_the_check_stricter_than_the_notional():
    """An order costing exactly the balance must not be approved.

    Regression guard: checking the bare notional approves orders the broker then
    rejects for being a few rupees short once charges land.
    """
    exact = affordability(paper_funds(10_000), quantity=10, price=1000.0, brokerage_pct=0.03)
    assert not exact["affordable"]


def test_max_affordable_quantity_is_itself_affordable():
    funds = paper_funds(50_000)
    max_qty = affordability(funds, 1000, 1000.0, brokerage_pct=0.03)[
        "max_affordable_quantity"
    ]
    assert affordability(funds, max_qty, 1000.0, brokerage_pct=0.03)["affordable"]


def test_one_more_than_max_is_not_affordable():
    funds = paper_funds(50_000)
    max_qty = affordability(funds, 1000, 1000.0, brokerage_pct=0.03)[
        "max_affordable_quantity"
    ]
    assert not affordability(funds, max_qty + 1, 1000.0, brokerage_pct=0.03)["affordable"]


def test_zero_price_yields_no_affordable_quantity():
    assert affordability(paper_funds(50_000), 10, 0.0)["max_affordable_quantity"] == 0


def test_can_afford_and_shortfall_agree():
    funds = Funds(available=1000.0)
    assert funds.can_afford(999.0)
    assert funds.shortfall(999.0) == 0.0
    assert not funds.can_afford(1001.0)
    assert funds.shortfall(1001.0) == pytest.approx(1.0)


def test_serialisation_shape():
    payload = paper_funds(1234.567).to_dict()
    assert payload["available"] == pytest.approx(1234.57)
    assert set(payload) >= {"available", "bank_balance", "blocked", "source", "raw"}
