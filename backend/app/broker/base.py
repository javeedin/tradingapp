"""Broker interface.

`PaperBroker` and `BreezeBroker` implement this identically, so the trading
engine never knows which one it is driving and switching between them is a
config change rather than a code change.

This abstraction is not optional polish here: Breeze has no sandbox or
paper-trading environment, so the only way to exercise the system without real
money is to simulate the broker ourselves.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime

from app.config import settings
from app.models import (
    DEFAULT_INTRADAY_PRODUCT,
    ExitReason,
    OptionContract,
    Order,
    Position,
    ProductType,
    Side,
    Trade,
)

logger = logging.getLogger(__name__)


class BrokerError(RuntimeError):
    """Order placement or account access failed."""


class Broker(ABC):
    """Common surface for order placement and position tracking."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []
        self._orders: list[Order] = []

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    @property
    @abstractmethod
    def mode(self) -> str:
        """'paper' or 'live' — surfaced in the UI so the mode is never ambiguous."""

    @property
    @abstractmethod
    def equity(self) -> float:
        """Total account value: cash plus open position value."""

    @property
    @abstractmethod
    def cash(self) -> float:
        """Cash available for new positions."""

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    @abstractmethod
    def buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stoploss: float,
        target: float,
        product: ProductType = DEFAULT_INTRADAY_PRODUCT,
        timestamp: datetime | None = None,
        contract: OptionContract | None = None,
    ) -> Order:
        """Open a long position with a protective stop.

        `contract` names the F&O series when the product is options or futures.
        Equity orders leave it None, and `symbol` is the position key either way.
        """

    @abstractmethod
    def sell(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stoploss: float,
        target: float,
        product: ProductType = DEFAULT_INTRADAY_PRODUCT,
        timestamp: datetime | None = None,
        contract: OptionContract | None = None,
    ) -> Order:
        """Open a short position with a protective stop."""

    @abstractmethod
    def add_to_position(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stoploss: float | None = None,
        target: float | None = None,
        timestamp: datetime | None = None,
    ) -> Order:
        """Average into an open position.

        `stoploss=None` means the active exit policy has no stop, and any resting
        stop should be cancelled rather than left behind at a level the strategy
        no longer intends to honour.
        """

    @abstractmethod
    def close_position(
        self,
        symbol: str,
        price: float,
        reason: ExitReason = ExitReason.SIGNAL,
        timestamp: datetime | None = None,
    ) -> Trade | None:
        """Flatten a position and record the completed trade."""

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def open_position_count(self) -> int:
        return len(self._positions)

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    @property
    def orders(self) -> list[Order]:
        return list(self._orders)

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def update_price(self, symbol: str, price: float) -> None:
        position = self._positions.get(symbol)
        if position is not None and price > 0:
            position.update_price(price)

    def update_prices(self, prices: dict[str, float]) -> None:
        for symbol, price in prices.items():
            self.update_price(symbol, price)

    def close_all(
        self,
        prices: dict[str, float],
        reason: ExitReason = ExitReason.KILL_SWITCH,
        timestamp: datetime | None = None,
    ) -> list[Trade]:
        """Flatten everything — used by the kill switch and the square-off job."""
        closed: list[Trade] = []
        for symbol in list(self._positions):
            price = prices.get(symbol) or self._positions[symbol].last_price
            trade = self.close_position(symbol, price, reason, timestamp)
            if trade is not None:
                closed.append(trade)
        return closed

    # ------------------------------------------------------------------
    # Costs
    # ------------------------------------------------------------------
    @staticmethod
    def brokerage(notional: float) -> float:
        """Brokerage on one side of a trade.

        A percentage approximation of ICICI Direct's plan. It deliberately
        ignores STT, exchange charges, stamp duty, and GST, which together add
        materially on intraday trades — so backtest P&L computed with this is
        optimistic, not conservative. Set BROKERAGE_PCT high enough to absorb
        them, or extend this into a full charge model before trusting the
        numbers.
        """
        return abs(notional) * settings.brokerage_pct / 100

    @staticmethod
    def slippage(price: float) -> float:
        """Expected price movement between decision and fill, per side."""
        return price * settings.slippage_pct / 100

    # ------------------------------------------------------------------
    # Shared bookkeeping
    # ------------------------------------------------------------------
    def _record_trade(
        self,
        position: Position,
        exit_price: float,
        reason: ExitReason,
        costs: float,
        timestamp: datetime | None = None,
    ) -> Trade:
        exit_time = timestamp or datetime.now()
        gross = (exit_price - position.entry_price) * position.quantity * position.side.sign

        trade = Trade(
            symbol=position.symbol,
            side=position.side,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            entry_time=position.entry_time,
            exit_time=exit_time,
            pnl=gross,
            costs=costs,
            exit_reason=reason,
            product=position.product,
            contract=position.contract,
        )
        self._trades.append(trade)

        logger.info(
            "[%s] CLOSED %s %s x%d @ %.2f (%s) net ₹%.2f",
            self.mode,
            position.side.value.upper(),
            position.symbol,
            position.quantity,
            exit_price,
            reason.value,
            trade.net_pnl,
        )
        return trade

    def summary(self) -> dict[str, object]:
        realised = sum(t.net_pnl for t in self._trades)
        unrealised = sum(p.unrealized_pnl for p in self._positions.values())
        wins = sum(1 for t in self._trades if t.is_win)

        return {
            "mode": self.mode,
            "equity": round(self.equity, 2),
            "cash": round(self.cash, 2),
            "open_positions": len(self._positions),
            "realised_pnl": round(realised, 2),
            "unrealised_pnl": round(unrealised, 2),
            "total_pnl": round(realised + unrealised, 2),
            "total_trades": len(self._trades),
            "wins": wins,
            "losses": len(self._trades) - wins,
            "win_rate": round(wins / len(self._trades) * 100, 2) if self._trades else 0.0,
        }


def side_for_action(action: str) -> Side:
    return Side.BUY if action == "buy" else Side.SELL
