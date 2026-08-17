"""Simulated broker.

Breeze has no sandbox, so this is the only way to run the strategy without real
money. It executes against real live prices and models the two costs that decide
whether a strategy is actually profitable — brokerage and slippage — but it
cannot model queue position, partial fills, or liquidity, so a strategy trading
illiquid names will look better here than it will in the market.

Slippage is always applied against you: buys fill above the decision price, sells
below. A simulator that fills at the decision price makes almost any high-
frequency strategy look profitable.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from app.broker.base import Broker, BrokerError
from app.config import settings
from app.models import (
    DEFAULT_INTRADAY_PRODUCT,
    ExitReason,
    Order,
    OrderStatus,
    Position,
    ProductType,
    Side,
    Trade,
)

logger = logging.getLogger(__name__)


class PaperBroker(Broker):
    """In-memory broker that simulates fills against live prices."""

    def __init__(self, starting_capital: float | None = None) -> None:
        super().__init__()
        self.starting_capital = (
            starting_capital if starting_capital is not None else settings.starting_capital
        )
        self._cash = self.starting_capital

    @property
    def mode(self) -> str:
        return "paper"

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def equity(self) -> float:
        """Cash plus the mark-to-market value of open positions.

        Longs contribute their current market value. Shorts contribute the
        margin reserved at entry *plus* their unrealised P&L: opening a short
        moves that margin out of cash, but it is still the account's money, so
        omitting it here would make equity collapse by the full notional the
        moment a short opens.

        This matters beyond display — `equity` drives position sizing and the
        daily-loss-limit check, so understating it silently shrinks every
        subsequent trade and can trip the loss limit on a position that is
        actually flat.
        """
        total = self._cash
        for position in self._positions.values():
            if position.side is Side.BUY:
                total += position.value
            else:
                total += position.reserved_margin + position.unrealized_pnl
        return total

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------
    def buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stoploss: float,
        target: float,
        product: ProductType = DEFAULT_INTRADAY_PRODUCT,
        timestamp: datetime | None = None,
    ) -> Order:
        return self._open(
            symbol, Side.BUY, quantity, price, stoploss, target, product, timestamp
        )

    def sell(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stoploss: float,
        target: float,
        product: ProductType = DEFAULT_INTRADAY_PRODUCT,
        timestamp: datetime | None = None,
    ) -> Order:
        return self._open(
            symbol, Side.SELL, quantity, price, stoploss, target, product, timestamp
        )

    def _open(
        self,
        symbol: str,
        side: Side,
        quantity: int,
        price: float,
        stoploss: float,
        target: float,
        product: ProductType,
        timestamp: datetime | None,
    ) -> Order:
        now = timestamp or datetime.now()
        order = Order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            product=product,
            timestamp=now,
            order_id=f"PAPER-{uuid.uuid4().hex[:10].upper()}",
            stoploss=stoploss,
            target=target,
        )
        self._orders.append(order)

        if quantity <= 0 or price <= 0:
            order.status = OrderStatus.REJECTED
            order.message = "Quantity and price must both be positive"
            return order

        if symbol in self._positions:
            order.status = OrderStatus.REJECTED
            order.message = f"Position already open in {symbol}"
            return order

        # Slippage moves the fill against the trader on entry.
        fill_price = price + self.slippage(price) * side.sign
        notional = fill_price * quantity
        costs = self.brokerage(notional)

        # Longs consume cash; shorts require margin, approximated here as the
        # full notional. Real intraday margin is a fraction of this, so this is
        # the conservative assumption.
        required = notional + costs
        if required > self._cash:
            order.status = OrderStatus.REJECTED
            order.message = (
                f"Insufficient cash: need ₹{required:,.2f}, have ₹{self._cash:,.2f}"
            )
            return order

        self._cash -= required

        self._positions[symbol] = Position(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=fill_price,
            entry_time=now,
            product=product,
            stoploss=stoploss,
            target=target,
            last_price=fill_price,
            order_id=order.order_id,
        )

        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = quantity
        order.message = f"Filled at ₹{fill_price:,.2f} (slippage applied)"

        logger.info(
            "[paper] OPEN %s %s x%d @ %.2f | stop %.2f target %.2f",
            side.value.upper(),
            symbol,
            quantity,
            fill_price,
            stoploss,
            target,
        )
        return order

    # ------------------------------------------------------------------
    # Exits
    # ------------------------------------------------------------------
    def close_position(
        self,
        symbol: str,
        price: float,
        reason: ExitReason = ExitReason.SIGNAL,
        timestamp: datetime | None = None,
    ) -> Trade | None:
        position = self._positions.get(symbol)
        if position is None:
            return None

        if price <= 0:
            raise BrokerError(f"Cannot close {symbol} at non-positive price {price}")

        # Slippage again works against the trader: exits fill worse than quoted.
        fill_price = price - self.slippage(price) * position.side.sign

        entry_notional = position.entry_price * position.quantity
        exit_notional = fill_price * position.quantity
        # Entry brokerage was already deducted from cash; charge the exit side
        # here and report the round-trip total on the trade record.
        exit_costs = self.brokerage(exit_notional)
        total_costs = self.brokerage(entry_notional) + exit_costs

        if position.side is Side.BUY:
            self._cash += exit_notional - exit_costs
        else:
            # Short: entry reserved the notional as margin; release it and
            # settle the P&L.
            gross = (position.entry_price - fill_price) * position.quantity
            self._cash += entry_notional + gross - exit_costs

        order = Order(
            symbol=symbol,
            side=position.side.opposite,
            quantity=position.quantity,
            price=fill_price,
            product=position.product,
            timestamp=timestamp or datetime.now(),
            order_id=f"PAPER-{uuid.uuid4().hex[:10].upper()}",
            status=OrderStatus.FILLED,
            filled_price=fill_price,
            filled_quantity=position.quantity,
            message=f"Exit: {reason.value}",
        )
        self._orders.append(order)

        trade = self._record_trade(position, fill_price, reason, total_costs, timestamp)
        del self._positions[symbol]
        return trade

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------
    def check_stops(
        self,
        bars: dict[str, tuple[float, float]],
        timestamp: datetime | None = None,
    ) -> list[Trade]:
        """Trigger stops and targets from each symbol's (high, low) range.

        The live broker gets this for free — the exchange holds the stop order —
        but in simulation nothing fires unless we check it, so this must be
        called on every bar.
        """
        closed: list[Trade] = []

        for symbol, (high, low) in bars.items():
            position = self._positions.get(symbol)
            if position is None:
                continue

            # Stop before target: when one bar spans both, assume the worse fill.
            if position.stop_hit(low, high):
                trade = self.close_position(
                    symbol, position.stoploss, ExitReason.STOPLOSS, timestamp
                )
            elif position.target_hit(low, high):
                trade = self.close_position(
                    symbol, position.target, ExitReason.TARGET, timestamp
                )
            else:
                continue

            if trade is not None:
                closed.append(trade)

        return closed

    def reset(self) -> None:
        """Clear all state — used between backtest runs."""
        self._cash = self.starting_capital
        self._positions.clear()
        self._trades.clear()
        self._orders.clear()

    def summary(self) -> dict[str, object]:
        base = super().summary()
        base["starting_capital"] = round(self.starting_capital, 2)
        base["return_pct"] = (
            round((self.equity - self.starting_capital) / self.starting_capital * 100, 2)
            if self.starting_capital
            else 0.0
        )
        return base
