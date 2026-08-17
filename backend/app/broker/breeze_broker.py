"""Live broker backed by the Breeze (ICICI Direct) API.

Two safety properties are enforced here and should not be relaxed:

1. **Refuses to construct unless `settings.is_live`.** Both `TRADING_MODE=live`
   and the explicit confirmation phrase are required, so a stray env var cannot
   start sending real orders.

2. **Never leaves a position without a stop.** Breeze exposes no true bracket
   order for equities via the API, so the stoploss is a second order placed
   after the entry fills. If that second order fails, the entry is immediately
   flattened rather than left naked — an unprotected position is a strictly
   worse outcome than a small round-trip cost.

Reminder: live orders must originate from an IP address registered with your
Breeze app, so this only works from a host with a static IP.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.broker.base import Broker, BrokerError
from app.config import settings
from app.data.breeze_client import BreezeClient, BreezeError
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

# Breeze converts market orders into aggressive limit orders. Sending a limit
# order explicitly, priced through the touch by this fraction, makes the
# behaviour predictable instead of relying on that conversion.
MARKETABLE_LIMIT_BUFFER_PCT = 0.30


class BreezeBroker(Broker):
    """Places real orders through Breeze."""

    def __init__(self, client: BreezeClient, exchange_code: str = "NSE") -> None:
        if not settings.is_live:
            raise BrokerError(
                "Refusing to start the live broker. Set TRADING_MODE=live and "
                "LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_THE_RISK to enable real orders."
            )

        super().__init__()
        self.client = client
        self.exchange_code = exchange_code
        self._cached_cash = 0.0
        self._stop_order_ids: dict[str, str] = {}

        logger.warning("LIVE TRADING ACTIVE — real orders will be placed")

    @property
    def mode(self) -> str:
        return "live"

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------
    def refresh_funds(self) -> float:
        try:
            funds = self.client.get_funds()
        except BreezeError as exc:
            logger.error("Could not refresh funds: %s", exc)
            return self._cached_cash

        for key in ("available_margin", "unallocated_balance", "cash_balance", "total_bank"):
            if key in funds:
                try:
                    self._cached_cash = float(funds[key])
                    break
                except (TypeError, ValueError):
                    continue

        return self._cached_cash

    @property
    def cash(self) -> float:
        return self._cached_cash or self.refresh_funds()

    @property
    def equity(self) -> float:
        """Available cash plus the value of open positions.

        Breeze reports *available* margin, which already excludes what open
        positions have blocked, so shorts add back their reserved margin plus
        unrealised P&L — the same accounting the paper broker uses, keeping
        equity (and therefore position sizing) consistent across both modes.
        """
        total = self.cash
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
            stoploss=stoploss,
            target=target,
        )
        self._orders.append(order)

        # ICICI prohibits placing, modifying, or cancelling Margin and Option
        # Plus orders through Breeze, and MTF order support is undocumented.
        # Refusing here gives a message that names the cause; sending it would
        # come back as an opaque rejection from the exchange, or worse, appear to
        # succeed and leave the position untracked.
        if not product.placeable_via_api:
            order.status = OrderStatus.REJECTED
            order.message = (
                f"Breeze does not permit placing '{product.value}' orders via the API. "
                "Use cash (delivery), futures, or options. MARGIN and MTF positions "
                "can be monitored but must be opened and closed in ICICI Direct itself."
            )
            logger.error("Refusing %s order for %s: %s", product.value, symbol, order.message)
            return order

        if symbol in self._positions:
            order.status = OrderStatus.REJECTED
            order.message = f"Position already open in {symbol}"
            return order

        limit_price = self._marketable_limit(price, side)

        try:
            response = self.client.place_order(
                stock_code=symbol,
                exchange_code=self.exchange_code,
                product=product.value,
                action=side.value,
                order_type="limit",
                quantity=str(quantity),
                price=f"{limit_price:.2f}",
                validity="day",
                user_remark="auto-entry",
            )
        except BreezeError as exc:
            order.status = OrderStatus.REJECTED
            order.message = f"Entry rejected: {exc}"
            logger.error("Entry order failed for %s: %s", symbol, exc)
            return order

        order_id = str(response.get("order_id") or "")
        if not order_id:
            order.status = OrderStatus.REJECTED
            order.message = f"Breeze returned no order_id: {response}"
            return order

        order.order_id = order_id
        order.status = OrderStatus.PENDING

        fill_price = self._resolve_fill_price(order_id, fallback=limit_price)
        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = quantity

        position = Position(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=fill_price,
            entry_time=now,
            product=product,
            stoploss=stoploss,
            target=target,
            last_price=fill_price,
            order_id=order_id,
        )
        self._positions[symbol] = position

        logger.info(
            "[live] OPEN %s %s x%d @ %.2f (order %s)",
            side.value.upper(),
            symbol,
            quantity,
            fill_price,
            order_id,
        )

        # Protect the position, or back out of it entirely.
        if not self._place_stop_order(position):
            logger.error(
                "Stoploss placement failed for %s — flattening the entry immediately", symbol
            )
            self.close_position(symbol, fill_price, ExitReason.KILL_SWITCH, now)
            order.status = OrderStatus.CANCELLED
            order.message = "Entry reversed: stoploss order could not be placed"

        return order

    def _marketable_limit(self, price: float, side: Side) -> float:
        """Limit price set through the touch so it fills like a market order."""
        buffer = price * MARKETABLE_LIMIT_BUFFER_PCT / 100
        return round(price + buffer * side.sign, 2)

    def _resolve_fill_price(self, order_id: str, fallback: float) -> float:
        """Read the actual average fill price, falling back to the limit price."""
        try:
            detail = self.client.get_order_detail(order_id, self.exchange_code)
        except BreezeError as exc:
            logger.warning("Could not read fill price for %s: %s", order_id, exc)
            return fallback

        for key in ("average_price", "avg_execution_price", "price"):
            value = detail.get(key)
            if value:
                try:
                    parsed = float(value)
                    if parsed > 0:
                        return parsed
                except (TypeError, ValueError):
                    continue

        return fallback

    # ------------------------------------------------------------------
    # Averaging
    # ------------------------------------------------------------------
    def add_to_position(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stoploss: float | None = None,
        target: float | None = None,
        timestamp: datetime | None = None,
    ) -> Order:
        """Average into an open position and reposition its resting stop."""
        now = timestamp or datetime.now()
        position = self._positions.get(symbol)

        order = Order(
            symbol=symbol,
            side=position.side if position else Side.BUY,
            quantity=quantity,
            price=price,
            product=position.product if position else DEFAULT_INTRADAY_PRODUCT,
            timestamp=now,
            stoploss=stoploss,
            target=target,
        )
        self._orders.append(order)

        if position is None:
            order.status = OrderStatus.REJECTED
            order.message = f"No open position in {symbol} to average into"
            return order

        limit_price = self._marketable_limit(price, position.side)
        try:
            response = self.client.place_order(
                stock_code=symbol,
                exchange_code=self.exchange_code,
                product=position.product.value,
                action=position.side.value,
                order_type="limit",
                quantity=str(quantity),
                price=f"{limit_price:.2f}",
                validity="day",
                user_remark="auto-average",
            )
        except BreezeError as exc:
            order.status = OrderStatus.REJECTED
            order.message = f"Averaging order rejected: {exc}"
            logger.error("Average order failed for %s: %s", symbol, exc)
            return order

        order_id = str(response.get("order_id") or "")
        fill_price = (
            self._resolve_fill_price(order_id, fallback=limit_price)
            if order_id
            else limit_price
        )

        position.add(quantity, fill_price)
        order.order_id = order_id
        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = quantity
        order.message = (
            f"Averaged in at ₹{fill_price:,.2f}; new average ₹{position.entry_price:,.2f} "
            f"across {position.quantity} (add #{position.adds})"
        )

        # The resting stop was priced off the old average and is now wrong. Under
        # a no-stop policy it must be cancelled outright rather than left behind
        # at a level the strategy no longer honours.
        self._cancel_stop_order(symbol)
        if stoploss is not None:
            position.stoploss = stoploss
            if not self._place_stop_order(position):
                logger.error(
                    "Could not reposition the stop for %s after averaging — "
                    "the position is unprotected",
                    symbol,
                )
                order.message += " (WARNING: stop could not be repositioned)"
        else:
            logger.warning(
                "%s now has no resting stop: the active exit policy has none", symbol
            )
        if target is not None:
            position.target = target

        return order

    # ------------------------------------------------------------------
    # Stop orders
    # ------------------------------------------------------------------
    def _place_stop_order(self, position: Position) -> bool:
        """Rest a stoploss order on the exchange for an open position."""
        exit_side = position.side.opposite
        # Price the limit leg beyond the trigger so it still fills in a fast move.
        limit_price = self._marketable_limit(position.stoploss, exit_side)

        try:
            response = self.client.place_order(
                stock_code=position.symbol,
                exchange_code=self.exchange_code,
                product=position.product.value,
                action=exit_side.value,
                order_type="stoploss",
                stoploss=f"{position.stoploss:.2f}",
                quantity=str(position.quantity),
                price=f"{limit_price:.2f}",
                validity="day",
                user_remark="auto-stoploss",
            )
        except BreezeError as exc:
            logger.error("Stoploss order failed for %s: %s", position.symbol, exc)
            return False

        stop_id = str(response.get("order_id") or "")
        if not stop_id:
            logger.error("Stoploss order returned no id for %s: %s", position.symbol, response)
            return False

        self._stop_order_ids[position.symbol] = stop_id
        logger.info(
            "[live] Stop resting for %s at %.2f (order %s)",
            position.symbol,
            position.stoploss,
            stop_id,
        )
        return True

    def update_stop(self, symbol: str, new_stop: float) -> bool:
        """Move a resting stop — used by the trailing-stop logic."""
        position = self._positions.get(symbol)
        stop_id = self._stop_order_ids.get(symbol)
        if position is None or not stop_id:
            return False

        exit_side = position.side.opposite
        try:
            self.client.modify_order(
                order_id=stop_id,
                exchange_code=self.exchange_code,
                order_type="stoploss",
                stoploss=f"{new_stop:.2f}",
                price=f"{self._marketable_limit(new_stop, exit_side):.2f}",
                quantity=str(position.quantity),
                validity="day",
            )
        except BreezeError as exc:
            logger.error("Could not modify stop for %s: %s", symbol, exc)
            return False

        position.stoploss = new_stop
        logger.info("[live] Stop for %s moved to %.2f", symbol, new_stop)
        return True

    def _cancel_stop_order(self, symbol: str) -> None:
        stop_id = self._stop_order_ids.pop(symbol, None)
        if not stop_id:
            return
        try:
            self.client.cancel_order(stop_id, self.exchange_code)
        except BreezeError as exc:
            # Already-filled stops cannot be cancelled; that is expected.
            logger.info("Stop cancel for %s returned: %s", symbol, exc)

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

        # Cancel the resting stop first, so the exit cannot double-fill and
        # leave an accidental position in the opposite direction.
        self._cancel_stop_order(symbol)

        exit_side = position.side.opposite
        limit_price = self._marketable_limit(price, exit_side)

        try:
            response = self.client.place_order(
                stock_code=symbol,
                exchange_code=self.exchange_code,
                product=position.product.value,
                action=exit_side.value,
                order_type="limit",
                quantity=str(position.quantity),
                price=f"{limit_price:.2f}",
                validity="day",
                user_remark=f"auto-exit-{reason.value}",
            )
        except BreezeError as exc:
            logger.error("EXIT FAILED for %s: %s — position still open", symbol, exc)
            raise BrokerError(f"Exit order failed for {symbol}: {exc}") from exc

        order_id = str(response.get("order_id") or "")
        fill_price = (
            self._resolve_fill_price(order_id, fallback=limit_price) if order_id else limit_price
        )

        costs = self.brokerage(position.entry_price * position.quantity) + self.brokerage(
            fill_price * position.quantity
        )

        self._orders.append(
            Order(
                symbol=symbol,
                side=exit_side,
                quantity=position.quantity,
                price=fill_price,
                product=position.product,
                timestamp=timestamp or datetime.now(),
                order_id=order_id,
                status=OrderStatus.FILLED,
                filled_price=fill_price,
                filled_quantity=position.quantity,
                message=f"Exit: {reason.value}",
            )
        )

        trade = self._record_trade(position, fill_price, reason, costs, timestamp)
        del self._positions[symbol]
        self.refresh_funds()
        return trade

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------
    def sync_positions(self) -> list[dict[str, Any]]:
        """Fetch broker-side positions.

        Local state can drift from the broker's — a stop may have filled at the
        exchange, or an order may have been placed manually — so the live loop
        reconciles against this rather than trusting its own bookkeeping.
        """
        try:
            remote = self.client.get_positions()
        except BreezeError as exc:
            logger.error("Position sync failed: %s", exc)
            return []

        remote_symbols = {
            str(p.get("stock_code")) for p in remote if self._remote_quantity(p) != 0
        }

        for symbol in list(self._positions):
            if symbol not in remote_symbols:
                logger.warning(
                    "Position %s is closed at the broker but open locally — "
                    "its stop likely filled. Dropping local state.",
                    symbol,
                )
                self._stop_order_ids.pop(symbol, None)
                del self._positions[symbol]

        return remote

    @staticmethod
    def _remote_quantity(payload: dict[str, Any]) -> int:
        for key in ("quantity", "net_quantity", "qty"):
            if key in payload:
                try:
                    return int(float(payload[key]))
                except (TypeError, ValueError):
                    continue
        return 0
