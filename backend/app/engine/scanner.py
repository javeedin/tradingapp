"""Rank a universe of stocks and pick the best candidates to trade.

Robotic trading needs more than "does this one stock look good" — it has to
choose *which* of many. That is a ranking problem, and ranking introduces two
failure modes the per-symbol path does not have:

* **Correlation.** The top six by score are frequently six banks, because the
  factors that make one bank look good make them all look good. Six correlated
  longs is one position with six times the size, and the per-trade risk limit
  does not notice.
* **Selection on noise.** Scoring 20 symbols and taking the top 6 selects partly
  for whichever happened to have the most favourable recent noise. A minimum
  score is what separates "best available" from "actually good", so candidates
  below the entry threshold are never promoted just for being top-ranked.

The default universe is NIFTY 50 large caps by Breeze code, chosen for liquidity
— slippage on a thin name eats the edge the ranking was trying to capture.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.data.breeze_client import BreezeClient, BreezeError
from app.data.store import MarketStore
from app.models import Signal, SignalAction
from app.strategy import indicators
from app.strategy.signals import SignalEngine

logger = logging.getLogger(__name__)

# Liquid NIFTY 50 constituents as Breeze stock codes. Verify against the security
# master before trusting any of these — Breeze codes are not NSE tickers, and a
# wrong code returns no data rather than an error.
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "RELIND", "TCS", "INFTEC", "HDFBAN", "ICIBAN", "HINLEV", "ITC", "STABAN",
    "BHAAIR", "LARTOU", "KOTMAH", "AXIBAN", "MARUTI", "ASIPAI", "BAJFI",
    "HCLTEC", "SUNPHA", "TATMOT", "WIPRO", "ULTCEM", "NESIND", "POWGRI",
    "NTPC", "TATSTE", "JSWSTE", "ADAPOR", "COAIND", "BAJAJ", "TECMAH", "GRASIM",
)

# How stale stored candles may be before the scanner refreshes them.
MAX_STALENESS = timedelta(minutes=10)


@dataclass(slots=True)
class Candidate:
    """One scored symbol, ready to be ranked."""

    symbol: str
    signal: Signal
    sector: str = ""

    @property
    def score(self) -> float:
        return self.signal.score

    @property
    def strength(self) -> float:
        """Absolute conviction, for ranking longs and shorts together."""
        return abs(self.signal.score)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            **self.signal.to_dict(),
        }


# Coarse sector map, used only to avoid stacking correlated names. Incomplete by
# design: an unmapped symbol is treated as its own sector, which errs toward
# allowing a trade rather than silently blocking one.
SECTORS: dict[str, str] = {
    "HDFBAN": "bank", "ICIBAN": "bank", "STABAN": "bank", "KOTMAH": "bank",
    "AXIBAN": "bank", "BAJFI": "bank", "BAJAJ": "bank",
    "TCS": "it", "INFTEC": "it", "HCLTEC": "it", "WIPRO": "it", "TECMAH": "it",
    "TATSTE": "metal", "JSWSTE": "metal", "COAIND": "metal", "GRASIM": "metal",
    "HINLEV": "fmcg", "ITC": "fmcg", "NESIND": "fmcg",
    "RELIND": "energy", "NTPC": "energy", "POWGRI": "energy", "ADAPOR": "energy",
    "MARUTI": "auto", "TATMOT": "auto",
}


def sector_of(symbol: str) -> str:
    return SECTORS.get(symbol.upper(), symbol.upper())


class Scanner:
    """Scores a universe and returns the strongest tradable candidates."""

    def __init__(
        self,
        store: MarketStore,
        engine: SignalEngine,
        client: BreezeClient | None = None,
        universe: tuple[str, ...] | list[str] | None = None,
        interval: str | None = None,
        benchmark_code: str = "NIFTY",
        max_per_sector: int = 2,
    ) -> None:
        self.store = store
        self.engine = engine
        self.client = client
        self.universe = list(universe or DEFAULT_UNIVERSE)
        self.interval = interval
        self.benchmark_code = benchmark_code
        self.max_per_sector = max_per_sector
        self._skipped: dict[str, str] = {}

    # ------------------------------------------------------------------
    def scan(
        self, universe_size: int | None = None, refresh: bool = True
    ) -> list[Candidate]:
        """Score every symbol in the universe, strongest first."""
        symbols = self.universe[: universe_size or len(self.universe)]
        self._skipped = {}

        benchmark = self.store.load_candles(self.benchmark_code, self.interval, limit=500)
        bench = benchmark if not benchmark.empty else None
        warmup = indicators.warmup_period()

        candidates: list[Candidate] = []
        for symbol in symbols:
            if refresh:
                self._refresh(symbol)

            frame = self.store.load_candles(symbol, self.interval, limit=500)
            if frame.empty:
                self._skipped[symbol] = "no stored candles"
                continue
            if len(frame) < warmup:
                self._skipped[symbol] = f"only {len(frame)} candles, need {warmup}"
                continue

            try:
                signal = self.engine.evaluate(frame, symbol, benchmark=bench)
            except Exception as exc:
                # One bad symbol must not abort the whole scan.
                logger.warning("Scoring %s failed: %s", symbol, exc)
                self._skipped[symbol] = f"scoring error: {exc}"
                continue

            if signal is None:
                self._skipped[symbol] = "indicators still warming up"
                continue

            candidates.append(
                Candidate(symbol=symbol, signal=signal, sector=sector_of(symbol))
            )

        candidates.sort(key=lambda c: c.strength, reverse=True)
        return candidates

    def _refresh(self, symbol: str) -> None:
        """Top up stored candles if they are stale and a session exists."""
        if self.client is None or not self.client.is_connected:
            return

        latest = self.store.latest_candle_time(symbol, self.interval)
        now = datetime.now()
        if latest is not None and now - latest < MAX_STALENESS:
            return

        start = (latest - timedelta(minutes=5)) if latest else (now - timedelta(days=45))
        try:
            candles = self.client.get_historical_data(
                stock_code=symbol, from_date=start, to_date=now, interval=self.interval
            )
        except BreezeError as exc:
            logger.warning("Could not refresh %s during the scan: %s", symbol, exc)
            return

        if candles:
            self.store.save_candles(symbol, candles, self.interval)

    # ------------------------------------------------------------------
    def pick(
        self,
        candidates: list[Candidate],
        limit: int,
        held: set[str] | None = None,
        allow_shorts: bool = True,
    ) -> tuple[list[Candidate], list[dict[str, Any]]]:
        """Choose up to `limit` candidates to trade, and explain every rejection.

        Returns (picked, rejected). Rejections are returned rather than logged
        away because "why did it not buy X" is the first question asked of any
        automated selection.
        """
        held = held or set()
        picked: list[Candidate] = []
        rejected: list[dict[str, Any]] = []
        sector_counts: dict[str, int] = {}

        for candidate in candidates:
            if len(picked) >= limit:
                rejected.append(
                    {
                        "symbol": candidate.symbol,
                        "score": candidate.score,
                        "reason": f"slot limit reached ({limit})",
                    }
                )
                continue

            if candidate.symbol in held:
                rejected.append(
                    {
                        "symbol": candidate.symbol,
                        "score": candidate.score,
                        "reason": "already holding",
                    }
                )
                continue

            # Ranking must not promote a candidate the engine would not trade on
            # its own — otherwise "best of 20" quietly becomes "least bad of 20".
            if candidate.signal.action is SignalAction.HOLD:
                rejected.append(
                    {
                        "symbol": candidate.symbol,
                        "score": candidate.score,
                        "reason": (
                            f"score {candidate.score:+.3f} below the "
                            f"±{self.engine.entry_threshold:.2f} entry threshold"
                        ),
                    }
                )
                continue

            if not allow_shorts and candidate.signal.action is SignalAction.SELL:
                rejected.append(
                    {
                        "symbol": candidate.symbol,
                        "score": candidate.score,
                        "reason": "short signal, shorts disabled",
                    }
                )
                continue

            # Concentration guard: six top-ranked banks is one position with six
            # times the size, which per-trade risk limits cannot see.
            count = sector_counts.get(candidate.sector, 0)
            if self.max_per_sector and count >= self.max_per_sector:
                rejected.append(
                    {
                        "symbol": candidate.symbol,
                        "score": candidate.score,
                        "reason": (
                            f"already {count} position(s) in {candidate.sector}; "
                            f"limit is {self.max_per_sector}"
                        ),
                    }
                )
                continue

            picked.append(candidate)
            sector_counts[candidate.sector] = count + 1

        return picked, rejected

    @property
    def skipped(self) -> dict[str, str]:
        """Symbols that could not be scored, and why."""
        return dict(self._skipped)

    def summary(self, candidates: list[Candidate]) -> dict[str, Any]:
        actionable = [c for c in candidates if c.signal.is_actionable]
        return {
            "scored": len(candidates),
            "actionable": len(actionable),
            "skipped": self._skipped,
            "universe_size": len(self.universe),
            "top": [c.to_dict() for c in candidates[:10]],
        }
