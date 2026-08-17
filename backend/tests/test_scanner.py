"""Universe scanning and candidate selection for robotic trading."""

from __future__ import annotations

import pytest

from app.data.store import MarketStore
from app.engine.scanner import Candidate, Scanner, sector_of
from app.models import Candle, SignalAction
from app.strategy.signals import SignalEngine

from .conftest import make_ohlcv


def seed(store: MarketStore, symbol: str, bars: int = 300, **kwargs) -> None:
    frame = make_ohlcv(bars=bars, **kwargs)
    store.save_candles(
        symbol,
        [Candle(ts, r.open, r.high, r.low, r.close, r.volume) for ts, r in frame.iterrows()],
        "5minute",
    )


@pytest.fixture
def store(tmp_path) -> MarketStore:
    s = MarketStore(tmp_path / "scanner.duckdb")
    yield s
    s.close()


def scanner(store: MarketStore, universe: list[str], **kwargs) -> Scanner:
    defaults = dict(
        store=store,
        engine=SignalEngine(entry_threshold=0.05),
        client=None,  # no network in tests
        universe=universe,
        interval="5minute",
    )
    defaults.update(kwargs)
    return Scanner(**defaults)


# ----------------------------------------------------------------------
# Sector mapping
# ----------------------------------------------------------------------
def test_known_symbols_map_to_sectors():
    assert sector_of("HDFBAN") == "bank"
    assert sector_of("ICIBAN") == "bank"
    assert sector_of("TCS") == "it"


def test_unknown_symbol_becomes_its_own_sector():
    """Erring toward allowing a trade rather than silently blocking one."""
    assert sector_of("WHATEVER") == "WHATEVER"


# ----------------------------------------------------------------------
# Scanning
# ----------------------------------------------------------------------
def test_scan_scores_and_ranks_by_strength(store):
    for i, symbol in enumerate(["AAA", "BBB", "CCC"]):
        seed(store, symbol, seed=i + 1)

    candidates = scanner(store, ["AAA", "BBB", "CCC"]).scan(refresh=False)

    assert len(candidates) == 3
    strengths = [c.strength for c in candidates]
    assert strengths == sorted(strengths, reverse=True)


def test_symbols_without_data_are_skipped_with_a_reason(store):
    seed(store, "AAA")
    s = scanner(store, ["AAA", "MISSING"])
    candidates = s.scan(refresh=False)

    assert [c.symbol for c in candidates] == ["AAA"]
    assert "MISSING" in s.skipped
    assert "no stored candles" in s.skipped["MISSING"]


def test_symbols_with_too_little_history_are_skipped(store):
    seed(store, "SHORT", bars=50)
    s = scanner(store, ["SHORT"])
    assert s.scan(refresh=False) == []
    assert "need" in s.skipped["SHORT"]


def test_universe_size_limits_the_scan(store):
    for symbol in ["AAA", "BBB", "CCC", "DDD"]:
        seed(store, symbol)
    candidates = scanner(store, ["AAA", "BBB", "CCC", "DDD"]).scan(
        universe_size=2, refresh=False
    )
    assert len(candidates) == 2


def test_summary_reports_the_scan(store):
    seed(store, "AAA")
    s = scanner(store, ["AAA", "MISSING"])
    summary = s.summary(s.scan(refresh=False))

    assert summary["scored"] == 1
    assert "MISSING" in summary["skipped"]
    assert summary["universe_size"] == 2


# ----------------------------------------------------------------------
# Picking
# ----------------------------------------------------------------------
def fake_candidate(symbol: str, score: float, action: SignalAction, sector: str = ""):
    """A hand-built candidate, so selection is tested independently of scoring."""
    from datetime import datetime

    from app.models import Signal

    return Candidate(
        symbol=symbol,
        sector=sector or symbol,
        signal=Signal(
            symbol=symbol,
            timestamp=datetime(2025, 1, 1, 10, 0),
            action=action,
            score=score,
            price=1000.0,
            atr=10.0,
        ),
    )


def test_pick_respects_the_slot_limit(store):
    s = scanner(store, [])
    candidates = [
        fake_candidate(f"S{i}", 0.9 - i * 0.01, SignalAction.BUY) for i in range(10)
    ]
    picked, rejected = s.pick(candidates, limit=3)

    assert len(picked) == 3
    assert any("slot limit" in r["reason"] for r in rejected)


def test_pick_skips_symbols_already_held(store):
    s = scanner(store, [])
    candidates = [
        fake_candidate("AAA", 0.9, SignalAction.BUY),
        fake_candidate("BBB", 0.8, SignalAction.BUY),
    ]
    picked, rejected = s.pick(candidates, limit=5, held={"AAA"})

    assert [c.symbol for c in picked] == ["BBB"]
    assert any(r["reason"] == "already holding" for r in rejected)


def test_hold_signals_are_never_promoted(store):
    """Ranking must not turn "best of 20" into "least bad of 20"."""
    s = scanner(store, [])
    candidates = [
        fake_candidate("AAA", 0.02, SignalAction.HOLD),
        fake_candidate("BBB", 0.01, SignalAction.HOLD),
    ]
    picked, rejected = s.pick(candidates, limit=5)

    assert picked == []
    assert all("entry threshold" in r["reason"] for r in rejected)


def test_shorts_can_be_excluded(store):
    s = scanner(store, [])
    candidates = [
        fake_candidate("AAA", -0.9, SignalAction.SELL),
        fake_candidate("BBB", 0.5, SignalAction.BUY),
    ]
    picked, rejected = s.pick(candidates, limit=5, allow_shorts=False)

    assert [c.symbol for c in picked] == ["BBB"]
    assert any("shorts disabled" in r["reason"] for r in rejected)


def test_sector_concentration_is_capped(store):
    """Six top-ranked banks is one position with six times the size."""
    s = scanner(store, [], max_per_sector=2)
    candidates = [
        fake_candidate("HDFBAN", 0.9, SignalAction.BUY, "bank"),
        fake_candidate("ICIBAN", 0.85, SignalAction.BUY, "bank"),
        fake_candidate("STABAN", 0.8, SignalAction.BUY, "bank"),
        fake_candidate("KOTMAH", 0.75, SignalAction.BUY, "bank"),
    ]
    picked, rejected = s.pick(candidates, limit=6)

    assert len(picked) == 2
    assert any("bank" in r["reason"] for r in rejected)


def test_sector_cap_still_allows_diversified_picks(store):
    s = scanner(store, [], max_per_sector=2)
    candidates = [
        fake_candidate("HDFBAN", 0.9, SignalAction.BUY, "bank"),
        fake_candidate("ICIBAN", 0.85, SignalAction.BUY, "bank"),
        fake_candidate("STABAN", 0.8, SignalAction.BUY, "bank"),
        fake_candidate("TCS", 0.7, SignalAction.BUY, "it"),
        fake_candidate("INFTEC", 0.6, SignalAction.BUY, "it"),
    ]
    picked, _ = s.pick(candidates, limit=6)

    assert len(picked) == 4
    assert {c.sector for c in picked} == {"bank", "it"}


def test_sector_cap_can_be_disabled(store):
    s = scanner(store, [], max_per_sector=0)
    candidates = [
        fake_candidate(f"BANK{i}", 0.9 - i * 0.01, SignalAction.BUY, "bank")
        for i in range(5)
    ]
    picked, _ = s.pick(candidates, limit=5)
    assert len(picked) == 5


def test_zero_slots_picks_nothing(store):
    s = scanner(store, [])
    candidates = [fake_candidate("AAA", 0.9, SignalAction.BUY)]
    picked, rejected = s.pick(candidates, limit=0)

    assert picked == []
    assert rejected


def test_every_rejection_carries_a_reason(store):
    """"Why did it not buy X" is the first question asked of automated selection."""
    s = scanner(store, [], max_per_sector=1)
    candidates = [
        fake_candidate("AAA", 0.9, SignalAction.BUY, "bank"),
        fake_candidate("BBB", 0.8, SignalAction.BUY, "bank"),
        fake_candidate("CCC", 0.01, SignalAction.HOLD, "it"),
        fake_candidate("DDD", 0.7, SignalAction.BUY, "it"),
    ]
    _, rejected = s.pick(candidates, limit=1, held={"DDD"})

    assert rejected
    for entry in rejected:
        assert entry["reason"]
        assert "symbol" in entry and "score" in entry


def test_picks_are_ordered_strongest_first(store):
    s = scanner(store, [])
    candidates = [
        fake_candidate("AAA", 0.9, SignalAction.BUY),
        fake_candidate("BBB", 0.5, SignalAction.BUY),
    ]
    picked, _ = s.pick(candidates, limit=2)
    assert [c.symbol for c in picked] == ["AAA", "BBB"]


def test_shorts_rank_alongside_longs_by_absolute_strength(store):
    """A -0.9 short is as strong a signal as a +0.9 long."""
    strong_short = fake_candidate("AAA", -0.9, SignalAction.SELL)
    weak_long = fake_candidate("BBB", 0.2, SignalAction.BUY)
    assert strong_short.strength > weak_long.strength
