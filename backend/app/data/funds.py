"""Normalise the Breeze funds payload.

`get_funds` returns a flat dict whose keys vary by account type and SDK version,
mixing bank balance, per-segment allocations, and margin already blocked by open
trades. This turns it into a fixed shape with one number that actually matters
for a new order — `available` — plus the segment detail behind it.

Every field is looked up through a list of candidates, and anything unrecognised
is preserved in `raw` rather than dropped, so a payload change degrades to
"some detail missing" instead of a wrong balance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Ordered by preference: the first present key wins.
AVAILABLE_KEYS = (
    "available_margin",
    "unallocated_balance",
    "cash_balance",
    "limit_available",
    "net_available",
)
BANK_KEYS = ("total_bank", "bank_account", "bank_balance")
ALLOCATED_EQUITY_KEYS = ("allocated_equity", "allocated_cash")
ALLOCATED_FNO_KEYS = ("allocated_fno", "allocated_futures_options")
BLOCKED_KEYS = ("block_by_trade_balance", "blocked_amount", "amount_blocked")
BLOCKED_EQUITY_KEYS = ("block_by_trade_equity",)
BLOCKED_FNO_KEYS = ("block_by_trade_fno", "block_by_trade_futures_options")


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        parsed = _num(payload.get(key))
        if parsed is not None:
            return parsed
    return None


@dataclass(slots=True)
class Funds:
    """Account funds, reduced to what an order decision needs."""

    available: float = 0.0
    bank_balance: float = 0.0
    allocated_equity: float = 0.0
    allocated_fno: float = 0.0
    blocked: float = 0.0
    source: str = "breeze"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_allocated(self) -> float:
        return self.allocated_equity + self.allocated_fno

    def can_afford(self, required: float) -> bool:
        return required <= self.available

    def shortfall(self, required: float) -> float:
        """How much more is needed; 0 when the order is affordable."""
        return max(0.0, required - self.available)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": round(self.available, 2),
            "bank_balance": round(self.bank_balance, 2),
            "allocated_equity": round(self.allocated_equity, 2),
            "allocated_fno": round(self.allocated_fno, 2),
            "total_allocated": round(self.total_allocated, 2),
            "blocked": round(self.blocked, 2),
            "source": self.source,
            # Kept so an unexpected payload can be inspected from the dashboard
            # rather than requiring a log dive.
            "raw": self.raw,
        }


def normalise_funds(payload: dict[str, Any] | None, source: str = "breeze") -> Funds:
    """Map a Breeze funds payload onto `Funds`."""
    if not payload:
        return Funds(source=source)

    blocked = _first(payload, BLOCKED_KEYS)
    if blocked is None:
        # Some payloads only break the blocked amount out per segment.
        equity = _first(payload, BLOCKED_EQUITY_KEYS) or 0.0
        fno = _first(payload, BLOCKED_FNO_KEYS) or 0.0
        blocked = equity + fno

    funds = Funds(
        available=_first(payload, AVAILABLE_KEYS) or 0.0,
        bank_balance=_first(payload, BANK_KEYS) or 0.0,
        allocated_equity=_first(payload, ALLOCATED_EQUITY_KEYS) or 0.0,
        allocated_fno=_first(payload, ALLOCATED_FNO_KEYS) or 0.0,
        blocked=blocked,
        source=source,
        raw=payload,
    )

    if funds.available == 0.0 and funds.bank_balance == 0.0:
        # Neither key matched: better to say so than to imply a zero balance.
        logger.warning(
            "Could not read any balance from the funds payload; keys present: %s",
            sorted(payload)[:15],
        )

    return funds


def paper_funds(cash: float) -> Funds:
    """Funds for paper mode, where the simulated cash balance is the truth."""
    return Funds(available=cash, bank_balance=cash, source="paper")


def affordability(
    funds: Funds,
    quantity: int,
    price: float,
    brokerage_pct: float = 0.0,
    buffer_pct: float = 0.5,
) -> dict[str, Any]:
    """Whether an order fits inside the available funds.

    A small buffer is applied because the fill price is not the quoted price —
    a marketable limit fills slightly through the touch, and brokerage lands on
    top. Checking against the bare notional would approve orders that then get
    rejected by the broker for being a few rupees short.
    """
    notional = quantity * price
    costs = notional * brokerage_pct / 100
    buffer = notional * buffer_pct / 100
    required = notional + costs + buffer

    return {
        "quantity": quantity,
        "price": round(price, 2),
        "notional": round(notional, 2),
        "estimated_costs": round(costs, 2),
        "buffer": round(buffer, 2),
        "required": round(required, 2),
        "available": round(funds.available, 2),
        "affordable": funds.can_afford(required),
        "shortfall": round(funds.shortfall(required), 2),
        "max_affordable_quantity": _max_quantity(funds.available, price, brokerage_pct, buffer_pct),
    }


def _max_quantity(
    available: float, price: float, brokerage_pct: float, buffer_pct: float
) -> int:
    """Largest quantity the available funds cover, same buffer applied."""
    if price <= 0:
        return 0
    per_unit = price * (1 + (brokerage_pct + buffer_pct) / 100)
    return int(available / per_unit) if per_unit > 0 else 0
