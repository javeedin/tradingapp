"""Domain types shared across the data, strategy, risk, and broker layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        """+1 for long, -1 for short — used for P&L arithmetic."""
        return 1 if self is Side.BUY else -1


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class ProductType(str, Enum):
    """Breeze product types.

    Not all of these can be traded through the API. ICICI prohibits *placing,
    modifying, or cancelling* Margin and Option Plus orders via Breeze, and MTF
    order support is undocumented — so MARGIN and MTF positions can be read and
    monitored but not opened or closed programmatically. `placeable_via_api`
    encodes that, and the live broker refuses rather than letting the order fail
    at the exchange with an opaque error.
    """

    # Placeable through the API.
    DELIVERY = "cash"  # CNC — held overnight
    OPTIONS = "options"
    FUTURES = "futures"

    # Readable and monitorable, but NOT placeable through the API.
    MARGIN = "margin"  # ICICI's intraday leveraged product
    MTF = "mtf"  # Margin Trading Facility — leveraged delivery

    @property
    def placeable_via_api(self) -> bool:
        """Whether Breeze permits order placement for this product."""
        return self in {ProductType.DELIVERY, ProductType.OPTIONS, ProductType.FUTURES}

    @property
    def is_leveraged(self) -> bool:
        return self in {ProductType.MARGIN, ProductType.MTF, ProductType.FUTURES}

    # Note: there is deliberately no `is_intraday` here. Whether a position gets
    # squared off before the cutoff is a decision about the *strategy*, not a
    # property of the Breeze product — cash positions are routinely traded
    # intraday. Conflating the two meant "trade intraday" implied the MARGIN
    # product, which the API cannot place. The engines carry an explicit
    # `intraday` flag instead.

    @classmethod
    def from_breeze(cls, value: str | None) -> ProductType:
        """Map a Breeze product string onto this enum, defaulting to delivery."""
        if not value:
            return cls.DELIVERY
        normalised = str(value).strip().lower()
        for member in cls:
            if member.value == normalised:
                return member
        # Breeze uses several spellings for the leveraged delivery product.
        if normalised in {"mtf", "margin_trading", "marginfunding", "emargin"}:
            return cls.MTF
        return cls.DELIVERY


# The intraday product the engine trades by default.
#
# Deliberately DELIVERY, not MARGIN: MARGIN would give intraday leverage but
# Breeze refuses to place those orders via API, so an engine configured that way
# cannot trade live at all. Cash intraday means no leverage, and positions are
# still squared off by the scheduler rather than by the product type.
DEFAULT_INTRADAY_PRODUCT = ProductType.DELIVERY


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ExitReason(str, Enum):
    STOPLOSS = "stoploss"
    TARGET = "target"
    SIGNAL = "signal"
    SQUAREOFF = "intraday_squareoff"
    KILL_SWITCH = "kill_switch"
    DAILY_LOSS_LIMIT = "daily_loss_limit"


@dataclass(slots=True)
class Candle:
    """One OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(slots=True)
class FactorScores:
    """Per-factor contributions behind a composite signal.

    Kept alongside every signal so the dashboard can show *why* a trade fired
    rather than just that it did.
    """

    regime: float = 0.0
    trend: float = 0.0
    momentum: float = 0.0
    volatility: float = 0.0
    volume: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "regime": round(self.regime, 4),
            "trend": round(self.trend, 4),
            "momentum": round(self.momentum, 4),
            "volatility": round(self.volatility, 4),
            "volume": round(self.volume, 4),
        }


@dataclass(slots=True)
class Signal:
    """A scored trading decision for one instrument at one point in time."""

    symbol: str
    timestamp: datetime
    action: SignalAction
    score: float
    price: float
    atr: float
    factors: FactorScores = field(default_factory=FactorScores)
    reasons: list[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.action is not SignalAction.HOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action.value,
            "score": round(self.score, 4),
            "price": self.price,
            "atr": round(self.atr, 4),
            "factors": self.factors.to_dict(),
            "reasons": self.reasons,
        }


@dataclass(slots=True)
class Order:
    symbol: str
    side: Side
    quantity: int
    price: float
    product: ProductType
    timestamp: datetime
    order_id: str = ""
    status: OrderStatus = OrderStatus.PENDING
    stoploss: float | None = None
    target: float | None = None
    filled_price: float | None = None
    filled_quantity: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "price": self.price,
            "product": self.product.value,
            "timestamp": self.timestamp.isoformat(),
            "status": self.status.value,
            "stoploss": self.stoploss,
            "target": self.target,
            "filled_price": self.filled_price,
            "filled_quantity": self.filled_quantity,
            "message": self.message,
        }


@dataclass(slots=True)
class Position:
    """An open position with its live stop and target."""

    symbol: str
    side: Side
    quantity: int
    entry_price: float
    entry_time: datetime
    product: ProductType
    stoploss: float
    target: float
    last_price: float = 0.0
    highest_price: float = 0.0  # for trailing stops on longs
    lowest_price: float = 0.0  # for trailing stops on shorts
    order_id: str = ""

    def __post_init__(self) -> None:
        if self.last_price == 0.0:
            self.last_price = self.entry_price
        if self.highest_price == 0.0:
            self.highest_price = self.entry_price
        if self.lowest_price == 0.0:
            self.lowest_price = self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.last_price - self.entry_price) * self.quantity * self.side.sign

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return ((self.last_price - self.entry_price) / self.entry_price) * 100 * self.side.sign

    @property
    def value(self) -> float:
        return self.last_price * self.quantity

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stoploss)

    @property
    def reserved_margin(self) -> float:
        """Cash set aside at entry to carry this position.

        Approximated as the full entry notional. Real intraday margin is a
        fraction of that, so this is the conservative assumption — it
        understates buying power rather than overstating it.
        """
        return self.entry_price * self.quantity

    def update_price(self, price: float) -> None:
        self.last_price = price
        self.highest_price = max(self.highest_price, price)
        self.lowest_price = min(self.lowest_price, price)

    def stop_hit(self, low: float, high: float) -> bool:
        """Whether the bar's range breached the stop."""
        return low <= self.stoploss if self.side is Side.BUY else high >= self.stoploss

    def target_hit(self, low: float, high: float) -> bool:
        return high >= self.target if self.side is Side.BUY else low <= self.target

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat(),
            "product": self.product.value,
            "stoploss": round(self.stoploss, 2),
            "target": round(self.target, 2),
            "last_price": self.last_price,
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 2),
            "value": round(self.value, 2),
            "order_id": self.order_id,
        }


@dataclass(slots=True)
class Trade:
    """A completed round trip."""

    symbol: str
    side: Side
    quantity: int
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    costs: float
    exit_reason: ExitReason
    product: ProductType = DEFAULT_INTRADAY_PRODUCT

    @property
    def net_pnl(self) -> float:
        return self.pnl - self.costs

    @property
    def return_pct(self) -> float:
        invested = self.entry_price * self.quantity
        return (self.net_pnl / invested * 100) if invested else 0.0

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0

    @property
    def holding_period_minutes(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "pnl": round(self.pnl, 2),
            "costs": round(self.costs, 2),
            "net_pnl": round(self.net_pnl, 2),
            "return_pct": round(self.return_pct, 2),
            "exit_reason": self.exit_reason.value,
            "product": self.product.value,
            "holding_minutes": round(self.holding_period_minutes, 1),
        }
