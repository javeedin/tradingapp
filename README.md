# Trading App — ICICI Direct (Breeze)

A signal-driven trading system for the Indian market. It reads price data from
ICICI Direct's Breeze API, scores each instrument with a multi-factor model,
sizes positions from an ATR-based stoploss, and places bracketed orders — with a
paper-trading mode that runs the identical code path without touching real money.

```
Breeze API ──► Ingest ──► DuckDB ──► Signal engine ──► Risk manager ──┐
(REST + WS)                                                           │
                                              ┌───────────────────────┤
                                              ▼                       ▼
                                       PaperBroker              BreezeBroker
                                      (simulated fills)         (real orders)
                                              └───────────┬───────────┘
                                                          ▼
                                                FastAPI ──► React dashboard
```

---

## Why it is built this way

Three constraints of the Breeze API drove most of the design:

| Constraint | Consequence |
|---|---|
| **No sandbox / paper environment** | The `Broker` interface has two implementations. `PaperBroker` simulates fills against live prices with brokerage and slippage modelled. It is the only way to exercise the system without real money. |
| **Orders must originate from a registered static IP** | Live trading only works from a host with a fixed IP registered in your Breeze app. Paper mode has no such requirement and runs anywhere. |
| **Session token expires daily** | There is no refresh-token flow. Every trading day you fetch a token by hand and submit it through the dashboard. The bot cannot start itself on a Monday morning without you. |

Two smaller ones: historical requests cap at **1000 candles** (the loader pages
around it), and Breeze converts market orders into aggressive limit orders (so
the code sends explicit marketable limits instead of relying on that).

---

## The strategy

"Read the market and decide" is implemented as five factors, each scoring the
current bar in `[-1, +1]`:

| Factor | Indicators | Question it answers | Weight |
|---|---|---|---|
| **Trend** | EMA 20/50 separation (in ATR units), MACD histogram + crossovers, price vs EMA 200, Supertrend | Which way is it moving? | 40% |
| **Momentum** | RSI(14) with exhaustion zones, 10-bar rate of change, RSI acceleration | Is the move strong or fading? | 25% |
| **Volatility** | ATR% of price, Bollinger %B | Is it tradable — enough movement to cover costs, not so much that stops are noise? | 20% |
| **Volume** | Volume vs 20-period average | Is the move backed by participation? | 15% |
| **Regime** | Benchmark (NIFTY) vs EMA 50/200, ADX | Is the broad market with us or against us? | *multiplier* |

The first four combine into a weighted composite. **Regime then scales that
result rather than adding to it** — a strong stock-level buy during a broad
downtrend isn't slightly worse, it's categorically riskier, because index
drawdowns drag correlated equities down regardless of their own setup. A hostile
regime damps the score to 35%, which is usually enough to veto the trade.

An `ADX < 20` reading halves the trend factor, because trend-following signals
whipsaw in a range.

Every signal carries its per-factor breakdown and a list of plain-English
reasons, both surfaced on the dashboard. A score you cannot explain is a score
you cannot tune.

**Tuning:** weights live in `FactorWeights` (`app/strategy/signals.py`);
thresholds and ATR multiples are environment variables.

---

## Risk model

Position size is **derived from** the stoploss, never chosen independently:

```
stop distance = ATR x ATR_STOP_MULTIPLIER
quantity      = (equity x RISK_PER_TRADE_PCT) / stop distance
```

A volatile stock with a wide stop gets a small position; a quiet one with a
tight stop gets a larger position. Either way a stopped-out trade costs the same
fixed slice of capital. Sizing by a fixed rupee amount or a fixed share count
instead would let one volatile name do many times the damage of a quiet one.

Stops are ATR multiples rather than fixed percentages for the same reason — a 2%
stop is noise on one stock and a mile away on another.

Layered on top:

- **Trailing stops** ratchet toward price and never widen.
- **Daily loss limit** halts new entries once the day's drawdown breaches `MAX_DAILY_LOSS_PCT`.
- **Exposure caps** — `MAX_OPEN_POSITIONS` and `MAX_POSITION_PCT`.
- **Intraday square-off** force-closes MIS positions at `INTRADAY_SQUAREOFF_TIME`.
- **Kill switch** flattens everything and stops trading.

### One thing to know about sizing

With tight ATR stops, the risk formula often asks for a position far larger than
`MAX_POSITION_PCT` allows — at 1% risk and a 1.5% stop distance, the "correct"
position is ~66% of equity. The exposure cap then quietly becomes the real
sizing rule, and actual per-trade risk lands well below what you configured.

That is not a bug, but it is invisible unless you look, so every `SizingResult`
reports `binding_constraint` (`risk_per_trade`, `max_position_pct`,
`available_cash`, or `lot_size`) and `was_capped`. If you want the risk rule to
actually bind, raise `MAX_POSITION_PCT` — and understand you are then relying on
intraday leverage to hold those positions.

---

## Quick start

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp ../.env.example ../.env      # then fill in BREEZE_API_KEY / BREEZE_API_SECRET

python -m app.api.main          # serves on :8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                     # serves on :5173, proxies /api to :8000
```

Open <http://localhost:5173>.

### Tests

```bash
cd backend && pytest            # 115 tests
cd frontend && npm run build    # typecheck + build
```

---

## The daily ritual

Breeze session tokens are same-day only, and there is no refresh-token flow, so
this cannot be fully automated. Every trading morning:

1. Click **Open Breeze login** on the dashboard.
2. Log in to ICICI Direct.

That's it. Breeze redirects back to the dashboard with the token in the URL; the
session panel reads it, connects automatically, and strips it from the address
bar so it doesn't linger in browser history. The manual paste box is still there
as a fallback.

For this to work, the **Redirect URL** registered on your Breeze app must point
at the dashboard — `http://localhost:5173` for local development, or your
deployed dashboard URL on a server.

The system backfills history automatically on connect and starts scoring on the
next cycle. Without this step the engine runs but every cycle reports
`no Breeze session`.

---

## Going live

Paper is the default and requires nothing beyond API credentials. Live mode has
a deliberate two-key interlock:

```bash
TRADING_MODE=live
LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_THE_RISK
```

Both are required. Setting only `TRADING_MODE=live` falls back to paper and logs
a warning — a stray env var or a copied deploy config must never be enough to
start sending real orders.

**Before you flip it:**

- [ ] Run paper trading for a meaningful stretch — enough trades to distinguish edge from luck.
- [ ] Confirm the backtest and the paper run broadly agree. If they diverge badly, the backtest is lying about something.
- [ ] Register your server's static IP in the Breeze app settings. Orders from any other IP are rejected.
- [ ] Set `BROKERAGE_PCT` to your actual ICICI Direct plan rate.
- [ ] Start with `STARTING_CAPITAL` far below what you intend to trade.
- [ ] Verify the kill switch works before you need it.

---

## Known limitations

Stated plainly, because a backtest that looks good for the wrong reason is worse
than no backtest:

- **Cost modelling is incomplete.** `BROKERAGE_PCT` is a percentage
  approximation. STT, exchange transaction charges, SEBI fees, stamp duty, and
  GST are *not* modelled and together add materially on intraday trades. Backtest
  P&L is therefore optimistic. Set `BROKERAGE_PCT` high enough to absorb them, or
  extend `Broker.brokerage()` into a full charge model.
- **No liquidity or queue modelling.** Fills assume your order is absorbed at the
  quoted price plus fixed slippage. Illiquid names will perform worse live.
- **Gap risk is not modelled.** A stop at ₹985 assumes a fill at ₹985; a gap down
  opens well below it. Real losses can exceed the "fixed" per-trade risk.
- **No trading-holiday calendar.** The engine relies on Breeze returning no
  candles on holidays.
- **Options support is partial.** Order plumbing, lot-size rounding, and the
  option-chain endpoint exist, but there is no greeks handling, no
  expiry-roll logic, and no strike-selection strategy. Equities are the
  well-trodden path.
- **DuckDB is single-writer.** One process may open the database for writing. Do
  not run a backfill script while the API is running — the API owns the file.
- **The API has no authentication.** It can place real orders. Bind it to
  localhost or put it behind a VPN; never expose it to the internet.

---

## Layout

```
backend/app/
├── config.py              # settings, live-mode interlock
├── models.py              # Candle, Signal, Order, Position, Trade
├── data/
│   ├── breeze_client.py   # Breeze SDK wrapper, chunked backfill, session handling
│   └── store.py           # DuckDB persistence
├── strategy/
│   ├── indicators.py      # EMA, RSI, MACD, ATR, ADX, Bollinger, VWAP, Supertrend
│   └── signals.py         # multi-factor scoring, regime gating
├── risk/manager.py        # sizing, stops, trailing, daily limits
├── broker/
│   ├── base.py            # Broker interface
│   ├── paper.py           # simulated fills
│   └── breeze_broker.py   # live orders
├── engine/
│   ├── backtest.py        # next-bar-open replay
│   └── live.py            # market-hours trading loop
└── api/main.py            # FastAPI + WebSocket + scheduler

frontend/src/
├── App.tsx                # layout, polling, controls
├── api.ts, types.ts
└── components/            # chart, positions, signals, trades, backtest, log
```

---

## Disclaimer

This is trading software. It can lose money, and a strategy that backtests well
frequently does not survive contact with a live market — slippage, costs, and
overfitting all conspire against it. Use paper mode until the track record
justifies otherwise. Nothing here is financial advice.
