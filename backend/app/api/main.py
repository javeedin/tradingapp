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
from app.data.expiry import expiry_candidates, to_breeze_expiry
from app.data.funds import Funds, affordability, normalise_funds, paper_funds
from app.data.store import MarketStore
from app.data.ticker import LiveTicker
from app.engine import monitor
from app.engine.analysis import AnalysisError, analyse_symbol
from app.engine.backtest import Backtester
from app.engine.live import LiveTrader, is_market_open
from app.models import (
    DEFAULT_INTRADAY_PRODUCT,
    Order,
    OrderStatus,
    ProductType,
    Side,
)
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

state: dict[str, Any] = {"trader": None, "scheduler": None, "ticker": None}


def get_ticker() -> LiveTicker | None:
    return state.get("ticker")


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
        product=DEFAULT_INTRADAY_PRODUCT,
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
    # The ticker runs on its own threads and needs a handle on this loop to
    # push updates back out over the websocket.
    state["loop"] = asyncio.get_running_loop()

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
        ticker = state.get("ticker")
        if ticker is not None:
            with contextlib.suppress(Exception):
                ticker.stop()
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


class PlaceOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    # Inline regex flags must lead the pattern in Python's re, so the case
    # variants are spelled out rather than using (?i).
    side: str = Field(default="buy", pattern="^([bB][uU][yY]|[sS][eE][lL][lL])$")
    product: str = Field(default="cash", description="cash | futures | options")
    quantity: int = Field(default=0, ge=0, description="0 means size it from risk rules")
    price: float | None = Field(default=None, gt=0, description="None means last price")
    stoploss: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)
    lot_size: int = Field(default=1, ge=1)


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


def push_quotes(quotes: list[dict[str, Any]]) -> None:
    """Forward ticker updates to websocket clients from a background thread."""
    loop = state.get("loop")
    if loop is None or not quotes:
        return
    # The ticker's threads cannot touch the event loop directly.
    asyncio.run_coroutine_threadsafe(
        broadcast({"type": "ticker", "data": quotes}), loop
    )


def start_ticker() -> dict[str, Any]:
    """Start (or restart) the live price feed for the current universe."""
    trader = get_trader()

    existing = state.get("ticker")
    if existing is not None:
        with contextlib.suppress(Exception):
            existing.stop()

    ticker = LiveTicker(trader.client, trader.symbols, on_update=push_quotes)
    state["ticker"] = ticker
    return ticker.start()


@app.post("/api/session")
async def create_session(request: SessionRequest) -> dict[str, Any]:
    """Submit the daily Breeze session token, backfill history, start the feed."""
    trader = get_trader()
    try:
        trader.connect(request.session_token.strip())
    except BreezeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    response: dict[str, Any] = {"connected": True}

    try:
        filled = await asyncio.to_thread(trader.backfill)
        response["backfilled"] = filled
        response["message"] = (
            f"Session established. Backfilled {sum(filled.values())} candles."
        )
    except BreezeError as exc:
        # The session is valid even if backfill partly failed — report both.
        response["backfill_error"] = str(exc)
        response["message"] = "Session established, but historical backfill failed."

    # The live feed is a bonus, never a reason to fail the connect.
    try:
        response["ticker"] = await asyncio.to_thread(start_ticker)
    except Exception as exc:
        logger.warning("Could not start the live ticker: %s", exc)
        response["ticker_error"] = str(exc)

    return response


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
# Live prices
# ----------------------------------------------------------------------
@app.get("/api/ticker")
async def ticker() -> dict[str, Any]:
    """Latest price per watched symbol."""
    live = get_ticker()
    if live is None:
        return {"quotes": [], "status": {"streaming": False, "polling": False}}
    return {"quotes": live.quotes(), "status": live.status()}


@app.post("/api/ticker/start")
async def ticker_start() -> dict[str, Any]:
    trader = get_trader()
    if not trader.is_connected:
        raise HTTPException(status_code=401, detail="Establish a Breeze session first")
    try:
        return await asyncio.to_thread(start_ticker)
    except BreezeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ticker/watch")
async def ticker_watch(symbols: list[str]) -> dict[str, Any]:
    """Replace the watch list — used when analysing a symbol outside the universe."""
    live = get_ticker()
    if live is None:
        raise HTTPException(status_code=409, detail="The ticker is not running")
    live.set_symbols(symbols)
    return live.status()


# ----------------------------------------------------------------------
# Symbol analysis
# ----------------------------------------------------------------------
@app.get("/api/analyse/{symbol}")
async def analyse(
    symbol: str,
    interval: str | None = None,
    intraday: bool = True,
    lot_size: int = 1,
) -> dict[str, Any]:
    """Trade plan for any symbol: verdict, entry, stoploss, target, size.

    Works for instruments outside the configured universe — history is fetched on
    demand when a Breeze session exists.
    """
    trader = get_trader()

    try:
        return await asyncio.to_thread(
            analyse_symbol,
            symbol,
            trader.store,
            trader.engine,
            trader.risk,
            client=trader.client if trader.is_connected else None,
            broker=trader.broker,
            interval=interval,
            benchmark_code=trader.benchmark_code,
            product=DEFAULT_INTRADAY_PRODUCT if intraday else ProductType.DELIVERY,
            lot_size=lot_size,
        )
    except AnalysisError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BreezeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# Options
# ----------------------------------------------------------------------
@app.get("/api/expiries")
async def expiries() -> dict[str, Any]:
    """Candidate expiry dates for the option chain picker."""
    return {
        "expiries": expiry_candidates(),
        "note": (
            "Candidates only — NSE has changed index expiry weekdays before, and "
            "holidays shift an expiry earlier. Breeze rejects a date that is not a "
            "real contract."
        ),
    }


@app.get("/api/option-chain")
async def option_chain(
    symbol: str = "NIFTY",
    expiry: str | None = None,
    exchange: str = "NFO",
) -> dict[str, Any]:
    """Calls and puts for one expiry, paired by strike.

    Breeze returns calls and puts as separate lists; they are joined here so the
    dashboard can render a conventional chain.
    """
    trader = get_trader()
    if not trader.is_connected:
        raise HTTPException(status_code=401, detail="Establish a Breeze session first")

    if not expiry:
        candidates = expiry_candidates()
        if not candidates:
            raise HTTPException(status_code=400, detail="No expiry supplied or derivable")
        expiry = candidates[0]["date"]

    breeze_expiry = to_breeze_expiry(expiry)
    symbol = symbol.strip().upper()

    def fetch(right: str) -> list[dict[str, Any]]:
        return trader.client.get_option_chain(
            stock_code=symbol,
            expiry_date=breeze_expiry,
            right=right,
            exchange_code=exchange,
        )

    try:
        calls = await asyncio.to_thread(fetch, "call")
        puts = await asyncio.to_thread(fetch, "put")
    except BreezeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def index_by_strike(rows: list[dict[str, Any]]) -> dict[float, dict[str, Any]]:
        indexed: dict[float, dict[str, Any]] = {}
        for row in rows:
            try:
                strike = float(row.get("strike_price") or 0)
            except (TypeError, ValueError):
                continue
            if strike:
                indexed[strike] = row
        return indexed

    call_map = index_by_strike(calls)
    put_map = index_by_strike(puts)

    def leg(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "ltp": _num(row.get("ltp")),
            "open_interest": _num(row.get("open_interest") or row.get("oi")),
            "volume": _num(row.get("total_quantity_traded") or row.get("volume")),
            "change": _num(row.get("ltp_percent_change") or row.get("change")),
            "bid": _num(row.get("best_bid_price")),
            "ask": _num(row.get("best_offer_price")),
        }

    rows = [
        {"strike": strike, "call": leg(call_map.get(strike)), "put": leg(put_map.get(strike))}
        for strike in sorted(set(call_map) | set(put_map))
    ]

    # The underlying's spot, so the UI can mark the at-the-money strike.
    spot = None
    live = get_ticker()
    if live is not None:
        spot = live.prices().get(symbol)
    if spot is None:
        frame = trader.store.load_candles(symbol, settings.candle_interval, limit=1)
        if not frame.empty:
            spot = float(frame.iloc[-1]["close"])

    return {
        "symbol": symbol,
        "expiry": expiry,
        "exchange": exchange,
        "spot": round(spot, 2) if spot else None,
        "rows": rows,
        "count": len(rows),
    }


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Broker-side positions (including ones bought by hand)
# ----------------------------------------------------------------------
@app.get("/api/broker/positions")
async def broker_positions() -> dict[str, Any]:
    """Positions held at ICICI Direct, with computed stop/target levels.

    Covers holdings bought by hand — MTF included. Reading only needs a Breeze
    session, not live trading mode, so this works while the bot itself is still
    on paper.
    """
    trader = get_trader()
    if not trader.is_connected:
        raise HTTPException(status_code=401, detail="Establish a Breeze session first")

    def fetch() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for source in (trader.client.get_positions, trader.client.get_holdings):
            try:
                rows.extend(source() or [])
            except BreezeError as exc:
                # One endpoint failing should not hide the other's positions.
                logger.warning("%s failed: %s", source.__name__, exc)
        return rows

    rows = await asyncio.to_thread(fetch)

    live = get_ticker()
    described = monitor.collect(
        rows,
        trader.risk,
        trader.store,
        trader.interval,
        live_prices=live.prices() if live else {},
    )

    return {
        "positions": described,
        "alerts": monitor.alerts_from(described),
        "count": len(described),
        "raw_rows": len(rows),
    }


# ----------------------------------------------------------------------
# Funds
# ----------------------------------------------------------------------
def read_funds() -> Funds:
    """Funds for the active mode.

    Paper mode reports the simulated balance, because that is what constrains a
    paper order. Live mode reports the broker's available margin. Reporting the
    real balance while trading on paper would let the dashboard approve an order
    the paper broker then refuses.
    """
    trader = get_trader()

    if trader.broker.mode == "paper":
        return paper_funds(trader.broker.cash)

    try:
        return normalise_funds(trader.client.get_funds())
    except BreezeError as exc:
        logger.error("Could not read funds: %s", exc)
        return Funds(source="unavailable", raw={"error": str(exc)})


@app.get("/api/funds")
async def funds() -> dict[str, Any]:
    """Account funds, plus the broker's real balance when a session exists."""
    trader = get_trader()
    active = await asyncio.to_thread(read_funds)

    payload: dict[str, Any] = {
        "funds": active.to_dict(),
        "mode": trader.broker.mode,
    }

    # In paper mode the real balance is still useful context, so fetch it too —
    # clearly separated so the two are never confused.
    if trader.broker.mode == "paper" and trader.is_connected:
        try:
            broker_funds = await asyncio.to_thread(
                lambda: normalise_funds(trader.client.get_funds())
            )
            payload["broker_funds"] = broker_funds.to_dict()
        except BreezeError as exc:
            payload["broker_funds_error"] = str(exc)

    return payload


# ----------------------------------------------------------------------
# Manual orders
# ----------------------------------------------------------------------
@app.get("/api/orders")
async def order_history(limit: int = 200, symbol: str | None = None) -> dict[str, Any]:
    """Every order this app attempted, newest first, plus the broker's own list.

    Rejections are included: "why did my order not go through" is the main
    question this view exists to answer.
    """
    trader = get_trader()

    payload: dict[str, Any] = {
        "orders": trader.store.recent_orders(
            limit=limit, mode=trader.broker.mode, symbol=symbol
        ),
        "stats": trader.store.order_stats(mode=trader.broker.mode),
        "session_orders": [o.to_dict() for o in trader.broker.orders],
        "mode": trader.broker.mode,
    }

    # The broker's list also shows orders placed elsewhere (the ICICI app or
    # website), which this app never saw.
    if trader.is_connected:
        try:
            payload["broker_orders"] = await asyncio.to_thread(
                trader.client.get_order_list,
                "NSE",
                datetime.now() - timedelta(days=7),
                datetime.now(),
            )
        except BreezeError as exc:
            payload["broker_orders_error"] = str(exc)

    return payload


@app.get("/api/order-products")
async def order_products() -> dict[str, Any]:
    """Which products can be traded through the API, and which cannot."""
    return {
        "placeable": [
            {
                "value": p.value,
                "label": monitor.product_label(p),
                "leveraged": p.is_leveraged,
            }
            for p in ProductType
            if p.placeable_via_api
        ],
        "not_placeable": [
            {"value": p.value, "label": monitor.product_label(p)}
            for p in ProductType
            if not p.placeable_via_api
        ],
        "note": (
            "ICICI prohibits placing, modifying, or cancelling Margin and Option Plus "
            "orders through Breeze, and MTF order support is undocumented. Those "
            "positions can be monitored here but must be traded in ICICI Direct."
        ),
    }


@app.post("/api/orders")
async def place_order(request: PlaceOrderRequest) -> dict[str, Any]:
    """Place an order, optionally with an automatic ATR stoploss and target.

    Routes to whichever broker is active, so the same call is a simulated fill in
    paper mode and a real order in live mode.
    """
    trader = get_trader()
    symbol = request.symbol.strip().upper()
    product = ProductType.from_breeze(request.product)

    if not product.placeable_via_api:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Breeze does not permit placing '{product.value}' orders via the API. "
                "Use cash (delivery), futures, or options — or place it in ICICI Direct "
                "and monitor it here."
            ),
        )

    if trader.broker.has_position(symbol):
        raise HTTPException(status_code=409, detail=f"Position already open in {symbol}")

    # Levels come from the strategy's own analysis so a manual order is protected
    # by the same rules as an automated one.
    try:
        plan = await asyncio.to_thread(
            analyse_symbol,
            symbol,
            trader.store,
            trader.engine,
            trader.risk,
            client=trader.client if trader.is_connected else None,
            broker=trader.broker,
            product=product,
            lot_size=request.lot_size,
        )
    except AnalysisError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    entry = request.price or plan["plan"]["entry"]
    side = Side.BUY if request.side.strip().lower() == "buy" else Side.SELL

    # Recompute against the actual entry price when the caller supplied one.
    stoploss, target = trader.risk.stop_and_target(entry, plan["market"]["atr"], side)
    if request.stoploss:
        stoploss = request.stoploss
    if request.target:
        target = request.target

    quantity = request.quantity or plan["plan"]["quantity"]
    if quantity < 1:
        raise HTTPException(
            status_code=400,
            detail=f"Quantity resolved to {quantity}. {plan['plan']['sizing_note']}",
        )

    # Check funds before sending. The broker would refuse anyway, but its message
    # says nothing about how much was short or what quantity would have fitted.
    # Shorts are exempt: they release proceeds rather than consuming cash, and
    # their margin requirement is not derivable from the notional alone.
    account = await asyncio.to_thread(read_funds)
    check = affordability(
        account, quantity, entry, brokerage_pct=settings.brokerage_pct
    )
    if side is Side.BUY and not check["affordable"]:
        message = (
            f"Insufficient funds: {quantity} × ₹{entry:,.2f} needs about "
            f"₹{check['required']:,.2f} but only ₹{check['available']:,.2f} is "
            f"available — short by ₹{check['shortfall']:,.2f}. "
            f"The largest affordable quantity is {check['max_affordable_quantity']}."
        )
        # Recorded even though the broker was never called: an order rejected on
        # funds is exactly the kind of event the history needs to explain, and
        # skipping it here would leave a silent gap.
        trader.store.save_order(
            Order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=entry,
                product=product,
                timestamp=datetime.now(),
                status=OrderStatus.REJECTED,
                stoploss=stoploss,
                target=target,
                message=message,
            ),
            origin="manual",
            mode=trader.broker.mode,
        )
        raise HTTPException(status_code=400, detail=message)

    place = trader.broker.buy if side is Side.BUY else trader.broker.sell
    order = await asyncio.to_thread(
        place,
        symbol=symbol,
        quantity=quantity,
        price=entry,
        stoploss=stoploss,
        target=target,
        product=product,
    )

    # Recorded before the rejection check so failed attempts appear in history —
    # that is precisely what makes the history diagnostic.
    trader.store.save_order(order, origin="manual", mode=trader.broker.mode)

    if order.status is OrderStatus.REJECTED:
        raise HTTPException(status_code=400, detail=order.message)

    trader._log_event(
        "manual_order",
        f"{side.value.upper()} {symbol} x{quantity} @ ₹{entry:,.2f} "
        f"(stop ₹{stoploss:,.2f}, target ₹{target:,.2f})",
    )
    await broadcast({"type": "order", "data": order.to_dict()})

    return {
        "order": order.to_dict(),
        "mode": trader.broker.mode,
        "plan": {
            "entry": round(entry, 2),
            "stoploss": round(stoploss, 2),
            "target": round(target, 2),
            "quantity": quantity,
        },
        "analysis": {
            "action": plan["action"],
            "score": plan["score"],
            "conviction": plan["conviction"],
            "reasons": plan["reasons"],
        },
        "funds": check,
    }


@app.get("/api/orders/preview")
async def preview_order(
    symbol: str,
    side: str = "buy",
    product: str = "cash",
    quantity: int = 0,
) -> dict[str, Any]:
    """Cost and affordability of a prospective order, without placing it.

    Lets the confirmation dialog show whether the order actually fits before the
    user commits, rather than discovering it in a rejection.
    """
    trader = get_trader()
    resolved = ProductType.from_breeze(product)

    try:
        plan = await asyncio.to_thread(
            analyse_symbol,
            symbol,
            trader.store,
            trader.engine,
            trader.risk,
            client=trader.client if trader.is_connected else None,
            broker=trader.broker,
            product=resolved,
        )
    except AnalysisError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    entry = plan["plan"]["entry"]
    qty = quantity or plan["plan"]["quantity"]
    account = await asyncio.to_thread(read_funds)

    return {
        "symbol": plan["symbol"],
        "side": side.lower(),
        "product": resolved.value,
        "placeable": resolved.placeable_via_api,
        "plan": plan["plan"],
        "action": plan["action"],
        "conviction": plan["conviction"],
        "score": plan["score"],
        "reasons": plan["reasons"],
        "funds": affordability(
            account, qty, entry, brokerage_pct=settings.brokerage_pct
        ),
        "account": account.to_dict(),
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
        product=DEFAULT_INTRADAY_PRODUCT if request.intraday else ProductType.DELIVERY,
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
