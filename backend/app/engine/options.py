"""Planning and risk for option orders.

Options are not equities with a different code, and the differences all bear on
sizing and stops:

* **Quantity is not free.** It must be a whole multiple of the contract's lot
  size. Breeze rejects anything else, and a wrong lot size silently trades a
  different size than intended, so the lot size is resolved explicitly rather
  than assumed.
* **Stops cannot come from the underlying's ATR.** The premium is a non-linear
  function of the underlying, so a stop derived from spot's ATR is meaningless in
  premium terms. Stops here are a percentage of the *premium*, which is crude but
  at least denominated in what actually gets filled.
* **Buying and writing are not symmetric.** A long option risks the premium and
  nothing more. A written option collects the premium and risks an unbounded
  amount against a margin many times larger. The plan says so, loudly, rather
  than treating a sell as the mirror image of a buy.
* **Time works against a buyer.** Premium decays whether or not the underlying
  moves, and the decay accelerates into expiry. Days-to-expiry is part of the
  plan for that reason.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from app.data.breeze_client import BreezeClient, BreezeError
from app.data.funds import Funds
from app.models import (
    OptionContract,
    OptionRight,
    Side,
    lot_size_for,
    margin_for,
)

logger = logging.getLogger(__name__)

# Default premium-based exit levels. 40% down / 80% up keeps the 1:2 shape the
# equity path uses, sized wide enough that ordinary premium noise does not stop
# a position out on entry — option premiums routinely swing 20% intraday on a
# move the underlying would call unremarkable.
DEFAULT_STOP_PCT = 40.0
DEFAULT_TARGET_PCT = 80.0

# Days to expiry below which decay dominates direction for a buyer.
EXPIRY_WARNING_DAYS = 2


def build_contract(
    underlying: str,
    expiry: date | str,
    strike: float,
    right: str,
    lot_size: int | None = None,
) -> OptionContract:
    """Assemble a contract, resolving the lot size when not given."""
    if isinstance(expiry, str):
        expiry_date = datetime.fromisoformat(expiry.replace("Z", "")).date()
    elif isinstance(expiry, datetime):
        expiry_date = expiry.date()
    else:
        expiry_date = expiry

    if strike <= 0:
        raise ValueError("Strike must be positive")

    code = underlying.strip().upper()
    resolved = lot_size or lot_size_for(code)
    if resolved < 1:
        raise ValueError(f"Lot size must be at least 1, got {resolved}")

    return OptionContract(
        underlying=code,
        expiry=expiry_date,
        strike=float(strike),
        right=OptionRight.parse(right),
        lot_size=resolved,
    )


def fetch_premium(client: BreezeClient, contract: OptionContract) -> float | None:
    """Live premium for one leg, or None when it cannot be read.

    Returns None rather than raising: a chain quote that is briefly unavailable
    should let the caller fall back to a user-supplied price, not fail the whole
    request.
    """
    if client is None or not client.is_connected:
        return None

    try:
        quote = client.get_quote(
            contract.underlying,
            exchange_code="NFO",
            product_type="options",
            expiry_date=contract.expiry.strftime("%Y-%m-%dT06:00:00.000Z"),
            right=contract.right.value,
            strike_price=f"{contract.strike:g}",
        )
    except BreezeError as exc:
        logger.warning("Could not quote %s: %s", contract.key, exc)
        return None

    for key in ("ltp", "last_traded_price", "best_bid_price"):
        value = quote.get(key)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed

    return None


def premium_levels(
    premium: float,
    side: Side,
    stop_pct: float = DEFAULT_STOP_PCT,
    target_pct: float = DEFAULT_TARGET_PCT,
) -> tuple[float, float]:
    """Stop and target as percentages of the premium, in the right direction.

    A buyer's stop is below the premium paid and target above it. A writer's is
    the reverse: the premium falling is the writer's profit, so the target is
    below and the stop above.
    """
    if premium <= 0:
        raise ValueError("Premium must be positive")

    down = premium * (1 - stop_pct / 100)
    up = premium * (1 + stop_pct / 100)

    if side is Side.BUY:
        # A stop can never sit at or below zero — premium floors at zero, so a
        # 100% stop would never trigger and would leave the position unprotected.
        return max(0.05, round(down, 2)), round(premium * (1 + target_pct / 100), 2)

    return round(up, 2), round(max(0.05, premium * (1 - target_pct / 100)), 2)


def plan_option_order(
    contract: OptionContract,
    premium: float,
    side: Side,
    lots: int,
    funds: Funds,
    stop_pct: float = DEFAULT_STOP_PCT,
    target_pct: float = DEFAULT_TARGET_PCT,
    brokerage_pct: float = 0.0,
    today: date | None = None,
) -> dict[str, Any]:
    """Everything needed to review an option order before sending it.

    Deliberately returns warnings rather than refusing: writing options and
    buying expiry-day lottery tickets are legitimate choices, but they should be
    made with the consequence on screen.
    """
    if lots < 1:
        raise ValueError("An option order needs at least one lot")

    today = today or date.today()
    quantity = contract.lots_to_quantity(lots)
    stoploss, target = premium_levels(premium, side, stop_pct, target_pct)

    premium_value = premium * quantity
    costs = premium_value * brokerage_pct / 100
    # A buy pays the premium; a write blocks margin against the underlying and
    # receives the premium instead.
    blocked = margin_for(side, premium, quantity, contract)
    required = blocked + costs

    risk_per_unit = abs(premium - stoploss)
    reward_per_unit = abs(target - premium)
    days_to_expiry = (contract.expiry - today).days

    warnings: list[str] = []
    if side is Side.SELL:
        warnings.append(
            f"Writing this option collects ₹{premium_value:,.0f} in premium but risks "
            "an unbounded loss if the underlying moves against you. The margin shown "
            f"(₹{blocked:,.0f}) is an estimate — the exchange sets the real figure "
            "from its SPAN file and raises it when volatility rises."
        )
    if days_to_expiry < 0:
        warnings.append(
            f"{contract.expiry:%d %b %Y} has already passed — this is not a live contract."
        )
    elif days_to_expiry <= EXPIRY_WARNING_DAYS:
        warnings.append(
            f"Expiry is {'today' if days_to_expiry == 0 else f'in {days_to_expiry} day(s)'}. "
            "Premium decays fastest here, so a buyer needs the move to happen almost "
            "immediately; being right a day late still loses."
        )
    if side is Side.BUY and premium < 5:
        warnings.append(
            f"At ₹{premium:.2f} this is a far out-of-the-money leg. Cheap premiums "
            "usually expire worthless, and the bid-ask spread is a large fraction "
            "of the price."
        )

    return {
        "contract": contract.to_dict(),
        "side": side.value,
        "lots": lots,
        "quantity": quantity,
        "lot_size": contract.lot_size,
        "premium": round(premium, 2),
        "stoploss": stoploss,
        "target": target,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "premium_value": round(premium_value, 2),
        "estimated_costs": round(costs, 2),
        "margin_blocked": round(blocked, 2),
        "required": round(required, 2),
        "available": round(funds.available, 2),
        "affordable": funds.can_afford(required),
        "shortfall": round(funds.shortfall(required), 2),
        "max_affordable_lots": max_affordable_lots(
            funds, contract, premium, side, brokerage_pct
        ),
        "risk_amount": round(risk_per_unit * quantity, 2),
        "reward_amount": round(reward_per_unit * quantity, 2),
        "risk_reward": round(reward_per_unit / risk_per_unit, 2) if risk_per_unit else 0.0,
        "days_to_expiry": days_to_expiry,
        "warnings": warnings,
    }


def max_affordable_lots(
    funds: Funds,
    contract: OptionContract,
    premium: float,
    side: Side,
    brokerage_pct: float = 0.0,
) -> int:
    """Largest whole number of lots the available funds cover."""
    per_lot = margin_for(side, premium, contract.lot_size, contract)
    per_lot += premium * contract.lot_size * brokerage_pct / 100
    if per_lot <= 0:
        return 0
    return int(funds.available / per_lot)
