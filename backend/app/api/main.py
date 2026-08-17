"""FastAPI application: REST + WebSocket control plane for the trading engine."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.broker.paper import PaperBroker
from app.config import REPO_ROOT, settings
from app.data.breeze_client import BreezeClient, BreezeError, login_url
from app.data.store import MarketStore
from app.engine.backtest import Backtester
from app.engine.live import LiveTrader, is_market_open
from app.models import ProductType
from app.risk.manager import RiskManager
from app.strategy.signals import SignalEngine

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Seconds between decision cycles, matched to the candle interval.
CYCLE_SECONDS = {
    "1minute": 60,
    "5minute": 300,
    "30minute": 1800,
    "1day": 3600,
}

state: dict[str, Any] = {"trader": None, "scheduler": None}


def build_trader() -> LiveTrader:
    """Construct the trading engine for the configured mode.

    Live mode requires both TRADING_MODE=live and the confirmation phrase; if
    only the former is set, this falls back to paper and logs loudly rather than
    starting to trade real money on a half-configured environment.
    """
    client = BreezeClient()
    store = MarketStore()

    warning = settings.live_mode_error()
    if warning:
        logger.warning(warning)

    if settings.is_live:
        from app.broker.breeze_broker import BreezeBroker

        broker = BreezeBroker(client)
        logger.warning("Engine starting in LIVE mode — real orders will be placed")
    else:
        broker = PaperBroker()
        logger.info("Engine starting in PAPER mode — no real orders")

    return LiveTrader(
        broker=broker,
        client=client,
        store=store,
        engine=SignalEngine(),
        risk=RiskManager(),
        product=ProductType.INTRADAY,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    trader = build_trader()
    state["trader"] = trader

    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    interval = CYCLE_SECONDS.get(settings.candle_interval, 300)

    async def cycle_job() -> None:
        if not is_market_open():
            return
        try:
            # run_cycle is synchronous and I/O-bound; keep it off the event loop.
            result = await asyncio.to_thread(trader.run_cycle)
            if result.get("actions"):
                await broadcast({"type": "cycle", "data": result})
        except Exception:
            logger.exception("Trading cycle failed")

    async def new_day_job() -> None:
        trader.start_day()

    scheduler.add_job(cycle_job, "interval", seconds=interval, id="cycle")
    scheduler.add_job(new_day_job, "cron", hour=9, minute=10, id="new_day")
    scheduler.start()
    state["scheduler"] = scheduler

    logger.info(
        "Trading API ready | mode=%s interval=%s universe=%s",
        trader.broker.mode,
        settings.candle_interval,
        ",".join(trader.symbols),
    )

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        with contextlib.suppress(Exception):
            trader.client.stop_stream()
        trader.store.close()


app = FastAPI(title="Trading App API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Local dashboard only. Widen deliberately if you host the UI elsewhere —
    # this API can place real orders, so it must never be open to the internet.
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_trader() -> LiveTrader:
    trader = state.get("trader")
    if trader is None:
        raise HTTPException(status_code=503, detail="Trading engine is not initialised")
    return trader


# ----------------------------------------------------------------------
# WebSocket fan-out
# ----------------------------------------------------------------------
connections: set[WebSocket] = set()


async def broadcast(message: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections.discard(ws)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    connections.add(ws)
    trader = state.get("trader")

    try:
        if trader is not None:
            await ws.send_json({"type": "status", "data": trader.status()})

        while True:
            # Push a status heartbeat; also detects dropped clients.
            await asyncio.sleep(5)
            if trader is not None:
                await ws.send_json(
                    {
                        "type": "tick",
                        "data": {
                            "status": trader.status(),
                            "positions": [
                                p.to_dict() for p in trader.broker.positions.values()
                            ],
                        },
                    }
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket closed unexpectedly", exc_info=True)
    finally:
        connections.discard(ws)


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------
class SessionRequest(BaseModel):
    session_token: str = Field(..., min_length=1, description="Today's Breeze session token")


class BacktestRequest(BaseModel):
    symbols: list[str] | None = None
    days: int = Field(default=60, ge=1, le=900)
    interval: str | None = None
    starting_capital: float | None = Field(default=None, gt=0)
    entry_threshold: float | None = Field(default=None, ge=0, le=1)
    intraday: bool = True


class ClosePositionRequest(BaseModel):
    symbol: str
    price: float | None = Field(default=None, gt=0)


# ----------------------------------------------------------------------
# Status & session
# ----------------------------------------------------------------------
@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    trader = get_trader()
    payload = trader.status()
    payload["login_url"] = login_url()
    payload["live_mode_warning"] = settings.live_mode_error()
    return payload


@app.post("/api/session")
async def create_session(request: SessionRequest) -> dict[str, Any]:
    """Submit the daily Breeze session token and backfill history."""
    trader = get_trader()
    try:
        trader.connect(request.session_token.strip())
    except BreezeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        filled = await asyncio.to_thread(trader.backfill)
    except BreezeError as exc:
        # The session is valid even if backfill partly failed — report both.
        return {
            "connected": True,
            "backfill_error": str(exc),
            "message": "Session established, but historical backfill failed.",
        }

    return {
        "connected": True,
        "backfilled": filled,
        "message": f"Session established. Backfilled {sum(filled.values())} candles.",
    }


@app.get("/api/session/login-url")
async def get_login_url() -> dict[str, str]:
    return {
        "url": login_url(),
        "instructions": (
            "Open this URL, log in to ICICI Direct, then copy the API_Session "
            "value from the redirect and submit it to POST /api/session. "
            "The token expires daily."
        ),
    }


# ----------------------------------------------------------------------
# Trading state
# ----------------------------------------------------------------------
@app.get("/api/positions")
async def positions() -> dict[str, Any]:
    trader = get_trader()
    return {
        "positions": [p.to_dict() for p in trader.broker.positions.values()],
        "account": trader.broker.summary(),
    }


@app.get("/api/signals")
async def signals(limit: int = 50, symbol: str | None = None) -> dict[str, Any]:
    trader = get_trader()
    return {
        "latest": trader.latest_signals,
        "history": trader.store.recent_signals(limit=limit, symbol=symbol),
    }


@app.get("/api/trades")
async def trades(limit: int = 100) -> dict[str, Any]:
    trader = get_trader()
    return {
        "trades": trader.store.recent_trades(limit=limit, mode=trader.broker.mode),
        "stats": trader.store.trade_stats(mode=trader.broker.mode),
        "session_trades": [t.to_dict() for t in trader.broker.trades],
    }


@app.get("/api/events")
async def events(limit: int = 100) -> dict[str, Any]:
    trader = get_trader()
    return {"events": trader.events[:limit]}


@app.get("/api/candles/{symbol}")
async def candles(symbol: str, limit: int = 300, interval: str | None = None) -> dict[str, Any]:
    """OHLCV for charting, with indicator overlays attached."""
    trader = get_trader()
    frame = trader.store.load_candles(
        symbol.upper(), interval or settings.candle_interval, limit=limit
    )
    if frame.empty:
        return {"symbol": symbol.upper(), "candles": [], "indicators": []}

    from app.strategy import indicators as ind

    enriched = ind.enrich(frame)

    def series(column: str) -> list[dict[str, Any]]:
        if column not in enriched.columns:
            return []
        return [
            {"time": ts.isoformat(), "value": round(float(v), 4)}
            for ts, v in enriched[column].items()
            if v == v  # drop NaN
        ]

    return {
        "symbol": symbol.upper(),
        "candles": [
            {
                "time": ts.isoformat(),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            for ts, r in frame.iterrows()
        ],
        "indicators": {
            "ema_20": series("ema_20"),
            "ema_50": series("ema_50"),
            "vwap": series("vwap"),
            "rsi": series("rsi"),
        },
    }


# ----------------------------------------------------------------------
# Controls
# ----------------------------------------------------------------------
@app.post("/api/kill-switch")
async def kill_switch() -> dict[str, Any]:
    """Flatten every position and stop opening new ones."""
    trader = get_trader()
    result = await asyncio.to_thread(trader.kill_switch)
    await broadcast({"type": "kill_switch", "data": result})
    return result


@app.post("/api/resume")
async def resume() -> dict[str, Any]:
    trader = get_trader()
    trader.resume()
    return {"halted": False, "message": "Trading resumed"}


@app.post("/api/positions/close")
async def close_position(request: ClosePositionRequest) -> dict[str, Any]:
    trader = get_trader()
    symbol = request.symbol.upper()
    position = trader.broker.get_position(symbol)
    if position is None:
        raise HTTPException(status_code=404, detail=f"No open position in {symbol}")

    price = request.price or position.last_price
    trade = await asyncio.to_thread(trader.broker.close_position, symbol, price)
    if trade is None:
        raise HTTPException(status_code=409, detail=f"Could not close {symbol}")

    trader.risk.record_pnl(trade.net_pnl)
    trader.store.save_trade(trade, trader.broker.mode)
    return {"closed": trade.to_dict()}


@app.post("/api/cycle")
async def run_cycle_now() -> dict[str, Any]:
    """Trigger one decision cycle immediately, outside the schedule."""
    trader = get_trader()
    return await asyncio.to_thread(trader.run_cycle)


# ----------------------------------------------------------------------
# Backtesting
# ----------------------------------------------------------------------
@app.post("/api/backtest")
async def run_backtest(request: BacktestRequest) -> dict[str, Any]:
    """Backtest the strategy over stored history."""
    trader = get_trader()
    interval = request.interval or settings.candle_interval
    symbols = [s.upper() for s in (request.symbols or trader.symbols)]
    start = datetime.now() - timedelta(days=request.days)

    data = {}
    for symbol in symbols:
        frame = trader.store.load_candles(symbol, interval, start=start)
        if not frame.empty:
            data[symbol] = frame

    if not data:
        raise HTTPException(
            status_code=400,
            detail=(
                "No stored candles for the requested symbols. "
                "Establish a session and let the backfill run first."
            ),
        )

    benchmark = trader.store.load_candles(trader.benchmark_code, interval, start=start)

    engine = SignalEngine(entry_threshold=request.entry_threshold)
    backtester = Backtester(
        engine=engine,
        risk=RiskManager(),
        starting_capital=request.starting_capital,
        product=ProductType.INTRADAY if request.intraday else ProductType.DELIVERY,
        interval=interval,
    )

    result = await asyncio.to_thread(
        backtester.run, data, benchmark if not benchmark.empty else None
    )

    payload = result.to_dict()
    payload["symbols"] = list(data)
    payload["bars"] = {s: len(f) for s, f in data.items()}
    payload["summary"] = result.summary_text()
    return payload


# ----------------------------------------------------------------------
# Static frontend
# ----------------------------------------------------------------------
# Mounted last so it never shadows an /api route. When the dashboard has been
# built, the API also serves it — that puts the UI and the API on one origin,
# which is what lets the desktop app skip CORS and the dev proxy entirely.
# In development you normally use the Vite server on :5173 instead, and this
# mount simply does not exist because dist/ has not been built.
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="dashboard")
    logger.info("Serving dashboard from %s", FRONTEND_DIST)
else:
    logger.info(
        "No built dashboard at %s — run 'npm run build' in frontend/ to serve it "
        "from this API, or use the Vite dev server.",
        FRONTEND_DIST,
    )


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
