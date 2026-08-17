"""Risk management: stops, position sizing, and the limits that halt trading.

The core rule is fixed-fractional risk. Position size is derived *from* the
stoploss distance, never chosen independently:

    quantity = (equity x risk_per_trade) / (entry - stoploss)

That inversion is what makes the strategy survivable. A volatile stock with a
wide stop gets a small position; a quiet one with a tight stop gets a larger
position; either way a stopped-out trade costs the same fixed slice of capital.
Sizing by a fixed rupee amount or a fixed share count instead lets one volatile
name do many times the damage of a quiet one.

Stops are ATR multiples rather than fixed percentages for the same reason: a 2%
stop is noise on one stock and a mile away on another.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time

from app.config import settings
from app.models import ExitReason, Position, ProductType, Side

logger = logging.getLogger(__name__)

# Below this, brokerage and slippage eat any realistic edge.
MIN_STOP_DISTANCE_PCT = 0.1


@dataclass(slots=True)
class SizingResult:
    """Outcome of a position-sizing request.

    `binding_constraint` records which rule actually determined the size. This
    matters more than it looks: with tight ATR stops the risk formula often asks
    for a position far larger than the exposure cap allows, so the cap silently
    becomes the real sizing rule and true per-trade risk lands well below the
    configured percentage. Surfacing the binding constraint keeps that visible
    instead of leaving you to believe you are risking 1% when you are risking
    0.3%.
    """

    approved: bool
    quantity: int = 0
    stoploss: float = 0.0
    target: float = 0.0
    risk_amount: float = 0.0
    notional: float = 0.0
    reason: str = ""
    binding_constraint: str = ""
    intended_quantity: int = 0
    intended_risk_amount: float = 0.0

    @property
    def was_capped(self) -> bool:
        return self.approved and self.binding_constraint != "risk_per_trade"

    def effective_risk_pct(self, equity: float) -> float:
        """Capital actually at risk, as a percentage of equity."""
        return (self.risk_amount / equity * 100) if equity else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "approved": self.approved,
            "quantity": self.quantity,
            "stoploss": round(self.stoploss, 2),
            "target": round(self.target, 2),
            "risk_amount": round(self.risk_amount, 2),
            "notional": round(self.notional, 2),
            "reason": self.reason,
            "binding_constraint": self.binding_constraint,
            "intended_quantity": self.intended_quantity,
            "intended_risk_amount": round(self.intended_risk_amount, 2),
            "was_capped": self.was_capped,
        }


class RiskManager:
    """Enforces per-trade sizing and account-level trading limits."""

    def __init__(
        self,
        risk_per_trade_pct: float | None = None,
        max_daily_loss_pct: float | None = None,
        max_open_positions: int | None = None,
        max_position_pct: float | None = None,
        atr_stop_multiplier: float | None = None,
        atr_target_multiplier: float | None = None,
        use_trailing_stop: bool | None = None,
        trail_atr_multiplier: float | None = None,
    ) -> None:
        self.risk_per_trade_pct = (
            risk_per_trade_pct
            if risk_per_trade_pct is not None
            else settings.risk_per_trade_pct
        )
        self.max_daily_loss_pct = (
            max_daily_loss_pct if max_daily_loss_pct is not None else settings.max_daily_loss_pct
        )
        self.max_open_positions = (
            max_open_positions if max_open_positions is not None else settings.max_open_positions
        )
        self.max_position_pct = (
            max_position_pct if max_position_pct is not None else settings.max_position_pct
        )
        self.atr_stop_multiplier = (
            atr_stop_multiplier
            if atr_stop_multiplier is not None
            else settings.atr_stop_multiplier
        )
        self.atr_target_multiplier = (
            atr_target_multiplier
            if atr_target_multiplier is not None
            else settings.atr_target_multiplier
        )
        self.use_trailing_stop = (
            use_trailing_stop if use_trailing_stop is not None else settings.use_trailing_stop
        )
        self.trail_atr_multiplier = (
            trail_atr_multiplier
            if trail_atr_multiplier is not None
            else settings.trail_atr_multiplier
        )

        self._daily_pnl: float = 0.0
        self._daily_pnl_date: date | None = None
        self._halted_reason: str = ""

    # ------------------------------------------------------------------
    # Stop and target levels
    # ------------------------------------------------------------------
    def stop_and_target(self, entry: float, atr: float, side: Side) -> tuple[float, float]:
        """ATR-based stoploss and target for a new position."""
        stop_distance = max(atr * self.atr_stop_multiplier, entry * MIN_STOP_DISTANCE_PCT / 100)
        target_distance = atr * self.atr_target_multiplier

        if side is Side.BUY:
            return entry - stop_distance, entry + target_distance
        return entry + stop_distance, entry - target_distance

    def risk_reward_ratio(self) -> float:
        if self.atr_stop_multiplier <= 0:
            return 0.0
        return self.atr_target_multiplier / self.atr_stop_multiplier

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------
    def size_position(
        self,
        entry: float,
        atr: float,
        side: Side,
        equity: float,
        available_cash: float,
        open_positions: int = 0,
        lot_size: int = 1,
    ) -> SizingResult:
        """Size a new position, or reject it with a reason.

        `lot_size` > 1 (options, futures) rounds the quantity down to whole lots;
        if even one lot exceeds the risk budget the trade is rejected rather than
        silently oversized.
        """
        if self._halted_reason:
            return SizingResult(approved=False, reason=self._halted_reason)

        if entry <= 0 or atr <= 0:
            return SizingResult(approved=False, reason="Invalid entry price or ATR")

        if equity <= 0:
            return SizingResult(approved=False, reason="No equity available")

        if open_positions >= self.max_open_positions:
            return SizingResult(
                approved=False,
                reason=f"Max open positions reached ({self.max_open_positions})",
            )

        stoploss, target = self.stop_and_target(entry, atr, side)
        stop_distance = abs(entry - stoploss)
        if stop_distance <= 0:
            return SizingResult(approved=False, reason="Stop distance computed as zero")

        # The intended size: the quantity that puts exactly risk_per_trade_pct
        # of equity at risk if the stop is hit.
        risk_budget = equity * (self.risk_per_trade_pct / 100)
        intended_quantity = int(risk_budget / stop_distance)

        if intended_quantity < 1:
            return SizingResult(
                approved=False,
                binding_constraint="risk_per_trade",
                reason=(
                    f"Risk budget ₹{risk_budget:,.0f} too small for a "
                    f"₹{stop_distance:,.2f} stop distance"
                ),
            )

        quantity = intended_quantity
        binding = "risk_per_trade"

        # Cap by the single-position exposure limit.
        max_notional = equity * (self.max_position_pct / 100)
        if quantity * entry > max_notional:
            quantity = int(max_notional / entry)
            binding = "max_position_pct"
            if quantity < 1:
                return SizingResult(
                    approved=False,
                    binding_constraint="max_position_pct",
                    intended_quantity=intended_quantity,
                    reason=f"Position cap ₹{max_notional:,.0f} below one share at ₹{entry:,.2f}",
                )

        # Cap by cash actually on hand.
        if quantity * entry > available_cash:
            quantity = int(available_cash / entry)
            binding = "available_cash"
            if quantity < 1:
                return SizingResult(
                    approved=False,
                    binding_constraint="available_cash",
                    intended_quantity=intended_quantity,
                    reason=f"Insufficient cash: ₹{available_cash:,.0f} for ₹{entry:,.2f} entry",
                )

        # Round down to whole lots for derivatives.
        if lot_size > 1:
            lots = quantity // lot_size
            if lots < 1:
                return SizingResult(
                    approved=False,
                    binding_constraint="lot_size",
                    intended_quantity=intended_quantity,
                    reason=(
                        f"One lot ({lot_size} units) exceeds the budget — "
                        f"affordable quantity was {quantity}"
                    ),
                )
            if lots * lot_size != quantity:
                binding = "lot_size" if binding == "risk_per_trade" else binding
            quantity = lots * lot_size

        actual_risk = quantity * stop_distance
        actual_risk_pct = actual_risk / equity * 100 if equity else 0.0

        if binding == "risk_per_trade":
            reason = (
                f"Risking ₹{actual_risk:,.0f} ({actual_risk_pct:.2f}% of ₹{equity:,.0f}) "
                f"on {quantity} @ ₹{entry:,.2f}, stop ₹{stoploss:,.2f}"
            )
        else:
            reason = (
                f"Size cut from {intended_quantity} to {quantity} by {binding}; "
                f"risking ₹{actual_risk:,.0f} ({actual_risk_pct:.2f}%) instead of the "
                f"configured {self.risk_per_trade_pct}%"
            )

        return SizingResult(
            approved=True,
            quantity=quantity,
            stoploss=stoploss,
            target=target,
            risk_amount=actual_risk,
            notional=quantity * entry,
            reason=reason,
            binding_constraint=binding,
            intended_quantity=intended_quantity,
            intended_risk_amount=intended_quantity * stop_distance,
        )

    # ------------------------------------------------------------------
    # Trailing stops
    # ------------------------------------------------------------------
    def update_trailing_stop(self, position: Position, atr: float) -> bool:
        """Ratchet the stop toward price. Returns True if the stop moved.

        Trailing stops only ever tighten. Widening one to avoid being stopped out
        converts a bounded loss into an unbounded one, which is the single
        fastest way to blow up an account.
        """
        if not self.use_trailing_stop or atr <= 0:
            return False

        trail_distance = atr * self.trail_atr_multiplier

        if position.side is Side.BUY:
            candidate = position.highest_price - trail_distance
            if candidate > position.stoploss:
                position.stoploss = candidate
                return True
        else:
            candidate = position.lowest_price + trail_distance
            if candidate < position.stoploss:
                position.stoploss = candidate
                return True

        return False

    # ------------------------------------------------------------------
    # Exit checks
    # ------------------------------------------------------------------
    def check_exit(
        self,
        position: Position,
        high: float,
        low: float,
        now: datetime | None = None,
    ) -> tuple[ExitReason, float] | None:
        """Whether a bar's range triggers an exit, and at what price.

        Stops are checked before targets. When a single bar spans both levels
        there is no way to know from OHLC alone which came first, so assuming the
        stop keeps the backtest honest — the optimistic assumption inflates
        results in exactly the volatile conditions where it matters most.
        """
        if position.stop_hit(low, high):
            return ExitReason.STOPLOSS, position.stoploss

        if position.target_hit(low, high):
            return ExitReason.TARGET, position.target

        if position.product.is_intraday and self.past_squareoff(now):
            return ExitReason.SQUAREOFF, position.last_price

        return None

    @staticmethod
    def past_squareoff(now: datetime | None = None, cutoff: time | None = None) -> bool:
        """Whether the intraday square-off cutoff has passed."""
        current = (now or datetime.now()).time()
        return current >= (cutoff or settings.squareoff_time)

    # ------------------------------------------------------------------
    # Account-level limits
    # ------------------------------------------------------------------
    def record_pnl(self, pnl: float, when: datetime | None = None) -> None:
        """Record a realised P&L and trip the daily loss limit if breached."""
        today = (when or datetime.now()).date()
        if self._daily_pnl_date != today:
            self._daily_pnl = 0.0
            self._daily_pnl_date = today
            self._halted_reason = ""

        self._daily_pnl += pnl

    def check_daily_limit(self, equity: float, when: datetime | None = None) -> bool:
        """Halt trading if today's drawdown breaches the limit. Returns True if halted."""
        today = (when or datetime.now()).date()
        if self._daily_pnl_date != today:
            self._daily_pnl = 0.0
            self._daily_pnl_date = today
            self._halted_reason = ""
            return False

        if equity <= 0:
            return bool(self._halted_reason)

        loss_pct = -self._daily_pnl / equity * 100
        if loss_pct >= self.max_daily_loss_pct:
            self._halted_reason = (
                f"Daily loss limit hit: {loss_pct:.2f}% "
                f"(limit {self.max_daily_loss_pct}%). Trading halted until tomorrow."
            )
            logger.warning(self._halted_reason)
            return True

        return False

    def halt(self, reason: str) -> None:
        """Manually stop new entries — the kill switch."""
        self._halted_reason = reason
        logger.warning("Trading halted: %s", reason)

    def resume(self) -> None:
        self._halted_reason = ""
        logger.info("Trading resumed")

    def reset_day(self, when: datetime | None = None) -> None:
        self._daily_pnl = 0.0
        self._daily_pnl_date = (when or datetime.now()).date()
        self._halted_reason = ""

    @property
    def is_halted(self) -> bool:
        return bool(self._halted_reason)

    @property
    def halted_reason(self) -> str:
        return self._halted_reason

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    def status(self, equity: float) -> dict[str, object]:
        return {
            "halted": self.is_halted,
            "halted_reason": self._halted_reason,
            "daily_pnl": round(self._daily_pnl, 2),
            "daily_pnl_pct": round(self._daily_pnl / equity * 100, 2) if equity else 0.0,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_open_positions": self.max_open_positions,
            "risk_reward_ratio": round(self.risk_reward_ratio(), 2),
            "trailing_stop": self.use_trailing_stop,
        }


def product_for(intraday: bool, is_option: bool = False) -> ProductType:
    """Map an intent to the Breeze product type."""
    if is_option:
        return ProductType.OPTIONS
    return ProductType.INTRADAY if intraday else ProductType.DELIVERY
