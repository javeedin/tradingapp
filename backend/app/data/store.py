"""DuckDB-backed persistence for candles, signals, and trades.

DuckDB is chosen over Postgres/TimescaleDB deliberately: it is an embedded file,
so there is no server to run on the VPS, and it is columnar, so the scans a
backtest performs over millions of bars are fast. If the dataset ever outgrows a
single file, the schema below ports to Timescale unchanged.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.config import settings
from app.models import Candle, Order, Signal, Trade

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol        VARCHAR   NOT NULL,
    exchange      VARCHAR   NOT NULL DEFAULT 'NSE',
    interval      VARCHAR   NOT NULL,
    timestamp     TIMESTAMP NOT NULL,
    open          DOUBLE    NOT NULL,
    high          DOUBLE    NOT NULL,
    low           DOUBLE    NOT NULL,
    close         DOUBLE    NOT NULL,
    volume        DOUBLE    NOT NULL,
    PRIMARY KEY (symbol, exchange, interval, timestamp)
);

CREATE TABLE IF NOT EXISTS signals (
    symbol        VARCHAR   NOT NULL,
    timestamp     TIMESTAMP NOT NULL,
    action        VARCHAR   NOT NULL,
    score         DOUBLE    NOT NULL,
    price         DOUBLE    NOT NULL,
    atr           DOUBLE,
    regime        DOUBLE,
    trend         DOUBLE,
    momentum      DOUBLE,
    volatility    DOUBLE,
    volume_factor DOUBLE,
    reasons       VARCHAR,
    created_at    TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS trades (
    symbol        VARCHAR   NOT NULL,
    side          VARCHAR   NOT NULL,
    quantity      INTEGER   NOT NULL,
    entry_price   DOUBLE    NOT NULL,
    exit_price    DOUBLE    NOT NULL,
    entry_time    TIMESTAMP NOT NULL,
    exit_time     TIMESTAMP NOT NULL,
    pnl           DOUBLE    NOT NULL,
    costs         DOUBLE    NOT NULL,
    net_pnl       DOUBLE    NOT NULL,
    exit_reason   VARCHAR   NOT NULL,
    product       VARCHAR   NOT NULL,
    mode          VARCHAR   NOT NULL DEFAULT 'paper',
    created_at    TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS orders (
    order_id      VARCHAR,
    symbol        VARCHAR   NOT NULL,
    side          VARCHAR   NOT NULL,
    quantity      INTEGER   NOT NULL,
    price         DOUBLE    NOT NULL,
    product       VARCHAR   NOT NULL,
    status        VARCHAR   NOT NULL,
    stoploss      DOUBLE,
    target        DOUBLE,
    filled_price  DOUBLE,
    filled_quantity INTEGER DEFAULT 0,
    message       VARCHAR,
    origin        VARCHAR   NOT NULL DEFAULT 'manual',
    mode          VARCHAR   NOT NULL DEFAULT 'paper',
    placed_at     TIMESTAMP NOT NULL,
    created_at    TIMESTAMP DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles (symbol, interval, timestamp);
CREATE INDEX IF NOT EXISTS idx_orders_placed ON orders (placed_at);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals (symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_exit ON trades (exit_time);
"""

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


class DatabaseLockedError(RuntimeError):
    """The DuckDB file is held by another process.

    DuckDB allows one writer, so running two backends against the same file is
    not possible. This exists to say that plainly instead of surfacing a raw
    IOException.
    """


class MarketStore:
    """Thread-safe store for market data and trading history.

    DuckDB connections are not safe to share across threads, so every operation
    holds a lock. Access is low-frequency (a handful of writes per candle), so
    serialising is cheaper than a connection pool.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        try:
            self._conn = duckdb.connect(str(self.db_path))
        except duckdb.IOException as exc:
            # DuckDB permits a single writing process. The raw error is a wall
            # of traceback that buries the actual cause, which is almost always
            # a second copy of the app rather than a corrupt database.
            raise DatabaseLockedError(
                f"Cannot open {self.db_path} — it is locked by another process.\n"
                "Another backend is already running (a terminal running "
                "'python -m app.api.main', or the desktop app). Close it and retry.\n"
                f"Original error: {exc}"
            ) from exc

        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            for statement in filter(None, (s.strip() for s in SCHEMA.split(";"))):
                self._conn.execute(statement)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> MarketStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Candles
    # ------------------------------------------------------------------
    def save_candles(
        self,
        symbol: str,
        candles: list[Candle],
        interval: str,
        exchange: str = "NSE",
    ) -> int:
        """Upsert candles. Returns the number of rows written.

        Re-fetching an overlapping range is normal (backfill windows overlap at
        the edges, and the latest bar is refetched as it completes), so existing
        rows are deleted and rewritten rather than skipped — the newer copy of a
        still-forming bar is the correct one.
        """
        if not candles:
            return 0

        frame = pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "interval": interval,
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                }
                for c in candles
            ]
        )
        frame = frame.drop_duplicates(subset=["symbol", "exchange", "interval", "timestamp"])

        with self._lock:
            self._conn.register("incoming", frame)
            try:
                self._conn.execute(
                    """
                    DELETE FROM candles
                    WHERE (symbol, exchange, interval, timestamp) IN (
                        SELECT symbol, exchange, interval, timestamp FROM incoming
                    )
                    """
                )
                self._conn.execute("INSERT INTO candles SELECT * FROM incoming")
            finally:
                self._conn.unregister("incoming")

        return len(frame)

    def load_candles(
        self,
        symbol: str,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
        exchange: str = "NSE",
    ) -> pd.DataFrame:
        """Return candles as a DataFrame indexed by timestamp, oldest first.

        `limit` takes the most recent N bars — the window a live strategy needs —
        but the result is still returned oldest-first so indicators compute
        correctly.
        """
        interval = interval or settings.candle_interval
        clauses = ["symbol = ?", "interval = ?", "exchange = ?"]
        params: list[Any] = [symbol, interval, exchange]

        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(end)

        where = " AND ".join(clauses)
        if limit is not None:
            query = f"""
                SELECT * FROM (
                    SELECT {", ".join(CANDLE_COLUMNS)} FROM candles
                    WHERE {where}
                    ORDER BY timestamp DESC
                    LIMIT ?
                ) ORDER BY timestamp ASC
            """
            params.append(limit)
        else:
            query = f"""
                SELECT {", ".join(CANDLE_COLUMNS)} FROM candles
                WHERE {where}
                ORDER BY timestamp ASC
            """

        with self._lock:
            frame = self._conn.execute(query, params).fetchdf()

        if frame.empty:
            return pd.DataFrame(columns=CANDLE_COLUMNS).set_index("timestamp")

        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        return frame.set_index("timestamp")

    def latest_candle_time(
        self, symbol: str, interval: str, exchange: str = "NSE"
    ) -> datetime | None:
        """Newest stored bar, used to resume a backfill instead of refetching."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT MAX(timestamp) FROM candles
                WHERE symbol = ? AND interval = ? AND exchange = ?
                """,
                [symbol, interval, exchange],
            ).fetchone()
        return row[0] if row and row[0] else None

    def candle_count(self, symbol: str, interval: str, exchange: str = "NSE") -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) FROM candles
                WHERE symbol = ? AND interval = ? AND exchange = ?
                """,
                [symbol, interval, exchange],
            ).fetchone()
        return int(row[0]) if row else 0

    def symbols(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT symbol FROM candles ORDER BY symbol"
            ).fetchall()
        return [r[0] for r in rows]

    def coverage(
        self, interval: str, exchange: str = "NSE"
    ) -> list[dict[str, Any]]:
        """What history is actually stored, per symbol.

        A backtest that finds no trades has two very different causes — the
        strategy saw nothing worth trading, or there was nothing to see — and
        without this the two are indistinguishable. Trading days are counted
        rather than calendar days, since a 60-day window over 20 stored sessions
        is a 20-session backtest whatever the request said.
        """
        with self._lock:
            frame = self._conn.execute(
                """
                SELECT
                    symbol,
                    COUNT(*)                         AS candles,
                    MIN(timestamp)                   AS first_candle,
                    MAX(timestamp)                   AS last_candle,
                    COUNT(DISTINCT CAST(timestamp AS DATE)) AS trading_days
                FROM candles
                WHERE interval = ? AND exchange = ?
                GROUP BY symbol
                ORDER BY symbol
                """,
                [interval, exchange],
            ).fetchdf()

        if frame.empty:
            return []

        return [
            {
                "symbol": row["symbol"],
                "candles": int(row["candles"]),
                "first_candle": row["first_candle"].isoformat(),
                "last_candle": row["last_candle"].isoformat(),
                "trading_days": int(row["trading_days"]),
            }
            for _, row in frame.iterrows()
        ]

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    def save_signal(self, signal: Signal) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO signals (
                    symbol, timestamp, action, score, price, atr,
                    regime, trend, momentum, volatility, volume_factor, reasons
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    signal.symbol,
                    signal.timestamp,
                    signal.action.value,
                    signal.score,
                    signal.price,
                    signal.atr,
                    signal.factors.regime,
                    signal.factors.trend,
                    signal.factors.momentum,
                    signal.factors.volatility,
                    signal.factors.volume,
                    " | ".join(signal.reasons),
                ],
            )

    def recent_signals(self, limit: int = 100, symbol: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM signals"
        params: list[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            frame = self._conn.execute(query, params).fetchdf()
        return frame.to_dict("records") if not frame.empty else []

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def save_order(
        self, order: Order, origin: str = "manual", mode: str | None = None
    ) -> None:
        """Record an order attempt.

        Rejections are stored too — "why didn't my order go through" is exactly
        the question an order history needs to answer, and a log that only keeps
        successes cannot.
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO orders (
                    order_id, symbol, side, quantity, price, product, status,
                    stoploss, target, filled_price, filled_quantity, message,
                    origin, mode, placed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    order.order_id,
                    order.symbol,
                    order.side.value,
                    order.quantity,
                    order.price,
                    order.product.value,
                    order.status.value,
                    order.stoploss,
                    order.target,
                    order.filled_price,
                    order.filled_quantity,
                    order.message,
                    origin,
                    mode or settings.trading_mode,
                    order.timestamp,
                ],
            )

    def recent_orders(
        self,
        limit: int = 200,
        mode: str | None = None,
        symbol: str | None = None,
        product: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if product:
            clauses.append("product = ?")
            params.append(product.lower())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with self._lock:
            frame = self._conn.execute(
                f"SELECT * FROM orders {where} ORDER BY placed_at DESC LIMIT ?", params
            ).fetchdf()

        return frame.to_dict("records") if not frame.empty else []

    def order_stats(self, mode: str | None = None) -> dict[str, Any]:
        where = "WHERE mode = ?" if mode else ""
        params: list[Any] = [mode] if mode else []

        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT
                    COUNT(*)                                     AS total,
                    COUNT(*) FILTER (WHERE status = 'filled')    AS filled,
                    COUNT(*) FILTER (WHERE status = 'rejected')  AS rejected,
                    COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                    COUNT(*) FILTER (WHERE status = 'pending')   AS pending
                FROM orders {where}
                """,
                params,
            ).fetchone()

        total, filled, rejected, cancelled, pending = row or (0, 0, 0, 0, 0)
        return {
            "total": int(total or 0),
            "filled": int(filled or 0),
            "rejected": int(rejected or 0),
            "cancelled": int(cancelled or 0),
            "pending": int(pending or 0),
        }

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    def save_trade(self, trade: Trade, mode: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trades (
                    symbol, side, quantity, entry_price, exit_price,
                    entry_time, exit_time, pnl, costs, net_pnl,
                    exit_reason, product, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    trade.symbol,
                    trade.side.value,
                    trade.quantity,
                    trade.entry_price,
                    trade.exit_price,
                    trade.entry_time,
                    trade.exit_time,
                    trade.pnl,
                    trade.costs,
                    trade.net_pnl,
                    trade.exit_reason.value,
                    trade.product.value,
                    mode or settings.trading_mode,
                ],
            )

    def recent_trades(self, limit: int = 100, mode: str | None = None) -> list[dict[str, Any]]:
        """Stored trades, newest first.

        `return_pct` and `holding_minutes` are computed here rather than stored.
        They are properties on the `Trade` dataclass, so a row read straight back
        out of the table lacks them — which surfaced in the UI as "NaN%" and
        "NaNm". Deriving them in SQL keeps a stored row and a live `Trade`
        serialising to the same shape.
        """
        query = """
            SELECT
                *,
                CASE
                    WHEN entry_price * quantity <> 0
                    THEN net_pnl / (entry_price * quantity) * 100
                    ELSE 0
                END AS return_pct,
                date_diff('minute', entry_time, exit_time) AS holding_minutes
            FROM trades
        """
        params: list[Any] = []
        if mode:
            query += " WHERE mode = ?"
            params.append(mode)
        query += " ORDER BY exit_time DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            frame = self._conn.execute(query, params).fetchdf()
        return frame.to_dict("records") if not frame.empty else []

    def trade_stats(self, mode: str | None = None) -> dict[str, Any]:
        """Aggregate performance across stored trades."""
        where = "WHERE mode = ?" if mode else ""
        params: list[Any] = [mode] if mode else []

        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT
                    COUNT(*)                                        AS total_trades,
                    COALESCE(SUM(net_pnl), 0)                       AS total_pnl,
                    COUNT(*) FILTER (WHERE net_pnl > 0)             AS wins,
                    COUNT(*) FILTER (WHERE net_pnl <= 0)            AS losses,
                    COALESCE(AVG(net_pnl) FILTER (WHERE net_pnl > 0), 0)  AS avg_win,
                    COALESCE(AVG(net_pnl) FILTER (WHERE net_pnl <= 0), 0) AS avg_loss,
                    COALESCE(MAX(net_pnl), 0)                       AS best_trade,
                    COALESCE(MIN(net_pnl), 0)                       AS worst_trade,
                    COALESCE(SUM(costs), 0)                         AS total_costs
                FROM trades {where}
                """,
                params,
            ).fetchone()

        if not row or not row[0]:
            return {
                "total_trades": 0,
                "total_pnl": 0.0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "total_costs": 0.0,
            }

        total, pnl, wins, losses, avg_win, avg_loss, best, worst, costs = row
        gross_win = avg_win * wins
        gross_loss = abs(avg_loss * losses)
        return {
            "total_trades": int(total),
            "total_pnl": round(float(pnl), 2),
            "wins": int(wins),
            "losses": int(losses),
            "win_rate": round(wins / total * 100, 2) if total else 0.0,
            "avg_win": round(float(avg_win), 2),
            "avg_loss": round(float(avg_loss), 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 0.0,
            "best_trade": round(float(best), 2),
            "worst_trade": round(float(worst), 2),
            "total_costs": round(float(costs), 2),
        }
