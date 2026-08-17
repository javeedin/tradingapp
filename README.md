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

### From the repo root

The root `package.json` is a task runner only — it has no dependencies of its
own. It exists so the obvious command works from the obvious place, instead of
failing on a missing `package.json` because you were one directory up:

```bash
npm run setup       # install frontend + desktop dependencies
npm run build       # build the dashboard
npm run desktop     # build, then launch the desktop app
npm run dev         # Vite dev server on :5173
npm run backend     # Python API on :8000
npm test            # pytest + frontend typecheck
```

The per-directory commands below still work and are equivalent.

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
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

### Desktop app (optional)

Runs everything in one window and starts the Python backend for you — no
terminals, no two-server dance:

```bash
cd frontend && npm install && npm run build    # build the dashboard once
cd ../desktop && npm install
npm start
```

The Electron main process spawns the backend, waits for `/api/health`, then
loads the dashboard **which the backend itself serves** — so the UI and API
share one origin and there is no CORS or proxy involved. Closing the window
shuts the backend down, so quitting can never leave a trading process running
in the background.

To produce an installer (`.exe` / `.dmg` / `.AppImage`):

```bash
cd desktop && npm run dist
```

Note that packaging bundles the backend source but **not** a Python runtime —
the target machine still needs Python and the installed dependencies.

### Placing orders and monitoring positions

The **Trade** tab places orders with an ATR stoploss and target attached
automatically, and lists every position held at ICICI Direct — including ones
bought by hand.

**What the API will and will not do**, because this is not obvious and matters:

| Product | Place via API | Monitor here |
|---|---|---|
| Delivery (cash) | yes | yes |
| Futures | yes | yes |
| Options | yes | yes |
| **Margin** (intraday leverage) | **no** | yes |
| **MTF** | **no** | yes |

ICICI prohibits placing, modifying, or cancelling **Margin and Option Plus**
orders through Breeze, and **MTF** order support is undocumented — an
[open issue asking about it](https://github.com/Idirect-Tech/Breeze-Python-SDK/issues/197)
has no maintainer reply. The live broker refuses those products outright rather
than letting the order fail at the exchange with an opaque error.

*Reading* positions is not restricted, though. So an MTF holding you bought in
ICICI Direct still appears in the Trade tab with an ATR stoploss and target
computed from the same rules the automated strategy uses, its live P&L, and a
stop→target progress bar. When a level is reached the dashboard says so — but
the exit has to be placed in ICICI Direct, and the row is labelled to make that
unambiguous.

This is also why `DEFAULT_INTRADAY_PRODUCT` is `cash` and not `margin`: whether
a position is squared off before the cutoff is a strategy decision carried by an
explicit `intraday` flag, not a property of the Breeze product. Deriving it from
the product meant "trade intraday" implied the MARGIN product, which cannot be
placed via the API at all.

### Analysing a single stock

The **Analyse** tab answers "should I buy this, and where do I get out" for any
Breeze code — including instruments outside the configured universe, whose
history is fetched on demand. It returns entry, ATR stoploss, target, position
size, risk in rupees, risk:reward, the per-factor score breakdown, and the
reasons behind the verdict.

It uses the same `SignalEngine` and `RiskManager` as the live loop, so an ad-hoc
lookup can never disagree with what the bot would actually do — a property the
test suite asserts directly.

A HOLD still returns the levels a trade *would* use. That is deliberate: a bare
"no" throws away the useful half of the answer, and the levels are what let you
set an alert instead of re-checking by hand.

**No LLM is involved, and that is a deliberate choice.** The analysis is
deterministic — same inputs, same output — which is what makes it backtestable,
free, and instant. An LLM would break all three. (Where one *would* earn its
place is news and earnings sentiment; that is not built yet.)

### Live prices

The ticker tape draws from two sources on purpose:

- **Websocket** — Breeze pushes ticks as they happen. Near-instant, but the
  payload's field names and instrument identifier vary between segments and SDK
  versions.
- **REST polling** — `get_quotes` every few seconds. Slower, but a stable shape.

The stream is treated as an upgrade over polling, not a replacement. A tick that
cannot be confidently attributed to a watched symbol is logged once and dropped
rather than guessed at — attributing a price to the wrong stock is worse than a
slightly stale one — and polling keeps the tape moving regardless.

> The websocket path could not be verified against live Breeze during
> development, since that needs real credentials and market hours. If the tape
> shows `polling` rather than `stream`, check the backend log for the one-off
> "unattributable tick" line: it prints a sample payload, which is all that is
> needed to fix the field mapping.

### Option chain

The **Options** tab shows calls and puts around the ATM strike for a chosen
underlying and expiry. Expiry dates are offered as *candidates* only — NSE has
changed index expiry weekdays more than once and holidays shift an expiry
earlier, so Breeze's acceptance of the date is the real check.

### Themes

Light by default; the moon/sun button in the top bar toggles dark, and the
choice is remembered. The price chart is canvas-based and cannot inherit CSS,
so it reads the active theme's custom properties and re-colours in place rather
than keeping a second palette in sync by hand.

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
├── api.ts, types.ts, theme.ts
└── components/            # chart, positions, signals, trades, backtest, log

desktop/
├── main.js                # Electron: spawns the backend, owns its lifecycle
└── package.json           # electron-builder packaging config
```

---

## Disclaimer

This is trading software. It can lose money, and a strategy that backtests well
frequently does not survive contact with a live market — slippage, costs, and
overfitting all conspire against it. Use paper mode until the track record
justifies otherwise. Nothing here is financial advice.
