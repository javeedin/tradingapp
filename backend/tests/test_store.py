"""Persistence layer."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.data.store import MarketStore
from app.models import (
    Candle,
    ExitReason,
    FactorScores,
    ProductType,
    Side,
    Signal,
    SignalAction,
    Trade,
)


@pytest.fixture
def store(tmp_path) -> MarketStore:
    s = MarketStore(tmp_path / "test.duckdb")
    yield s
    s.close()


def make_candles(n: int = 10, start: datetime | None = None) -> list[Candle]:
    origin = start or datetime(2025, 1, 1, 9, 15)
    return [
        Candle(
            timestamp=origin + timedelta(minutes=5 * i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0 * (i + 1),
        )
        for i in range(n)
    ]


def test_save_and_load_roundtrip(store):
    assert store.save_candles("TEST", make_candles(10), "5minute") == 10

    frame = store.load_candles("TEST", "5minute")
    assert len(frame) == 10
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index.is_monotonic_increasing


def test_saving_empty_list_is_a_no_op(store):
    assert store.save_candles("TEST", [], "5minute") == 0


def test_reinserting_the_same_range_upserts(store):
    """Backfill windows overlap and the newest bar is refetched — no duplicates."""
    candles = make_candles(10)
    store.save_candles("TEST", candles, "5minute")
    store.save_candles("TEST", candles, "5minute")
    assert store.candle_count("TEST", "5minute") == 10


def test_upsert_keeps_the_newer_copy(store):
    original = make_candles(1)
    store.save_candles("TEST", original, "5minute")

    updated = [
        Candle(original[0].timestamp, 100.0, 200.0, 50.0, 199.0, 9999.0)
    ]
    store.save_candles("TEST", updated, "5minute")

    frame = store.load_candles("TEST", "5minute")
    assert len(frame) == 1
    assert frame.iloc[0]["close"] == pytest.approx(199.0)


def test_limit_returns_the_newest_bars_oldest_first(store):
    """A live strategy wants the most recent window, but indicators need it in order."""
    store.save_candles("TEST", make_candles(50), "5minute")
    frame = store.load_candles("TEST", "5minute", limit=10)
    assert len(frame) == 10
    assert frame.index.is_monotonic_increasing

    full = store.load_candles("TEST", "5minute")
    assert frame.index[-1] == full.index[-1]


def test_date_range_filtering(store):
    candles = make_candles(20)
    store.save_candles("TEST", candles, "5minute")
    frame = store.load_candles(
        "TEST", "5minute", start=candles[5].timestamp, end=candles[10].timestamp
    )
    assert len(frame) == 6


def test_intervals_are_isolated(store):
    store.save_candles("TEST", make_candles(10), "5minute")
    store.save_candles("TEST", make_candles(5), "1day")
    assert store.candle_count("TEST", "5minute") == 10
    assert store.candle_count("TEST", "1day") == 5


def test_missing_symbol_returns_empty_frame(store):
    frame = store.load_candles("NOPE", "5minute")
    assert frame.empty


def test_latest_candle_time_supports_resume(store):
    candles = make_candles(10)
    store.save_candles("TEST", candles, "5minute")
    assert store.latest_candle_time("TEST", "5minute") == candles[-1].timestamp
    assert store.latest_candle_time("NOPE", "5minute") is None


def test_symbols_listing(store):
    store.save_candles("AAA", make_candles(3), "5minute")
    store.save_candles("BBB", make_candles(3), "5minute")
    assert store.symbols() == ["AAA", "BBB"]


def test_signal_persistence(store):
    signal = Signal(
        symbol="TEST",
        timestamp=datetime(2025, 1, 1, 10, 0),
        action=SignalAction.BUY,
        score=0.62,
        price=1000.0,
        atr=12.5,
        factors=FactorScores(regime=0.5, trend=0.7, momentum=0.3),
        reasons=["ADX confirmed", "volume 1.4x"],
    )
    store.save_signal(signal)

    rows = store.recent_signals(limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "TEST"
    assert rows[0]["action"] == "buy"
    assert rows[0]["score"] == pytest.approx(0.62)
    assert "ADX confirmed" in rows[0]["reasons"]


def make_trade(pnl: float, symbol: str = "TEST") -> Trade:
    return Trade(
        symbol=symbol,
        side=Side.BUY,
        quantity=10,
        entry_price=1000.0,
        exit_price=1000.0 + pnl / 10,
        entry_time=datetime(2025, 1, 1, 10, 0),
        exit_time=datetime(2025, 1, 1, 11, 0),
        pnl=pnl,
        costs=5.0,
        exit_reason=ExitReason.TARGET if pnl > 0 else ExitReason.STOPLOSS,
        product=ProductType.DELIVERY,
    )


def test_trade_persistence_and_stats(store):
    for pnl in (500.0, 300.0, -200.0, -100.0):
        store.save_trade(make_trade(pnl), mode="paper")

    stats = store.trade_stats(mode="paper")
    assert stats["total_trades"] == 4
    assert stats["wins"] == 2
    assert stats["losses"] == 2
    assert stats["win_rate"] == pytest.approx(50.0)
    # net_pnl subtracts ₹5 costs per trade.
    assert stats["total_pnl"] == pytest.approx(500 - 5 + 300 - 5 - 205 - 105)
    assert stats["profit_factor"] > 0


def test_trade_stats_on_empty_table(store):
    stats = store.trade_stats()
    assert stats["total_trades"] == 0
    assert stats["win_rate"] == 0.0
    assert stats["profit_factor"] == 0.0


def test_modes_are_isolated(store):
    store.save_trade(make_trade(100.0), mode="paper")
    store.save_trade(make_trade(-100.0), mode="live")
    assert store.trade_stats(mode="paper")["total_trades"] == 1
    assert store.trade_stats(mode="live")["total_trades"] == 1
    assert store.trade_stats()["total_trades"] == 2


def test_context_manager_closes(tmp_path):
    with MarketStore(tmp_path / "ctx.duckdb") as s:
        s.save_candles("TEST", make_candles(3), "5minute")
        assert s.candle_count("TEST", "5minute") == 3
