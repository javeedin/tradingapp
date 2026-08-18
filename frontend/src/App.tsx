import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatCurrency, formatNumber } from './api'
import AnalysePanel from './components/AnalysePanel'
import BacktestPanel from './components/BacktestPanel'
import EventLog from './components/EventLog'
import OptionChain from './components/OptionChain'
import OrdersPanel from './components/OrdersPanel'
import PositionsTable from './components/PositionsTable'
import PriceChart from './components/PriceChart'
import RoboticPanel from './components/RoboticPanel'
import ServerControl from './components/ServerControl'
import SessionPanel from './components/SessionPanel'
import SettingsPanel from './components/SettingsPanel'
import SignalsPanel from './components/SignalsPanel'
import StockScreener from './components/StockScreener'
import TickerTape from './components/TickerTape'
import TradePanel from './components/TradePanel'
import TradesTable from './components/TradesTable'
import { useTheme } from './theme'
import type {
  EngineEvent,
  Position,
  Quote,
  Signal,
  Status,
  TickerStatus,
  Trade,
} from './types'

const POLL_MS = 5000
// Prices are polled faster than the rest of the dashboard — a ticker that lags
// five seconds behind does not read as live.
const TICKER_POLL_MS = 1000

interface NavTab {
  id: Tab
  label: string
  hint: string
}

type Tab =
  | 'live'
  | 'analyse'
  | 'options'
  | 'screener'
  | 'robotic'
  | 'trade'
  | 'orders'
  | 'settings'
  | 'history'
  | 'backtest'

// Grouped so the nav reads as three jobs rather than eight pages: watch the
// market, act on it, and review what happened.
const NAV: { group: string; tabs: NavTab[] }[] = [
  {
    group: 'Market',
    tabs: [
      { id: 'live', label: 'Live', hint: 'Chart, positions, and signals' },
      { id: 'screener', label: 'Screener', hint: 'High-beta stock scanner' },
      { id: 'analyse', label: 'Analyse', hint: 'Score any symbol on demand' },
      { id: 'options', label: 'Options', hint: 'Chain, option orders, option legs' },
    ],
  },
  {
    group: 'Trade',
    tabs: [
      { id: 'trade', label: 'Place order', hint: 'Manual equity orders' },
      { id: 'robotic', label: 'Robotic', hint: 'Automated trading picks' },
      { id: 'orders', label: 'Orders', hint: 'Every order attempt, and why it failed' },
      { id: 'settings', label: 'Settings', hint: 'Exit policy, risk, robotic trading' },
    ],
  },
  {
    group: 'Review',
    tabs: [
      { id: 'history', label: 'History', hint: 'Closed trades' },
      { id: 'backtest', label: 'Backtest', hint: 'Replay the strategy on stored candles' },
    ],
  },
]

const ALL_TABS: NavTab[] = NAV.flatMap((g) => g.tabs)

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [events, setEvents] = useState<EngineEvent[]>([])
  const [quotes, setQuotes] = useState<Quote[]>([])
  const [tickerStatus, setTickerStatus] = useState<TickerStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('live')
  const [symbol, setSymbol] = useState('')
  const [closing, setClosing] = useState<string | null>(null)
  const [acting, setActing] = useState(false)
  const [theme, toggleTheme] = useTheme()

  // Keeps the chart from resetting the user's symbol choice on every poll.
  const symbolInitialised = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const [s, p, sig, t, e] = await Promise.all([
        api.status(),
        api.positions(),
        api.signals(),
        api.trades(),
        api.events(),
      ])
      setStatus(s)
      setPositions(p.positions)
      setSignals(sig.latest)
      setTrades(t.trades.length ? (t.trades as unknown as Trade[]) : t.session_trades)
      setEvents(e.events)
      setError(null)

      if (!symbolInitialised.current && s.symbols.length) {
        setSymbol(s.symbols[0])
        symbolInitialised.current = true
      }
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, POLL_MS)
    return () => clearInterval(id)
  }, [refresh])

  // Prices on their own faster loop. The websocket pushes ticks when the Breeze
  // stream is live; this keeps the tape moving when it is only polling.
  useEffect(() => {
    let cancelled = false

    const pull = async () => {
      try {
        const data = await api.ticker()
        if (cancelled) return
        setQuotes(data.quotes)
        setTickerStatus(data.status)
      } catch {
        // Prices are non-critical; the main poll surfaces real outages.
      }
    }

    pull()
    const id = setInterval(pull, TICKER_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  // The websocket pushes cycle results the moment they happen, so entries and
  // exits surface without waiting for the next poll.
  useEffect(() => {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    let socket: WebSocket | null = null
    try {
      socket = new WebSocket(`${protocol}://${location.host}/ws`)
      socket.onmessage = (event) => {
        const payload = JSON.parse(event.data)
        if (payload.type === 'cycle' || payload.type === 'kill_switch') refresh()
        if (payload.type === 'ticker' && Array.isArray(payload.data)) {
          // Merge rather than replace: a push may carry only the symbols that
          // moved, and dropping the rest would blank the tape.
          setQuotes((current) => {
            const byMostRecent = new Map(current.map((q) => [q.symbol, q]))
            for (const quote of payload.data as Quote[]) {
              byMostRecent.set(quote.symbol, quote)
            }
            return [...byMostRecent.values()]
          })
        }
      }
    } catch {
      // Polling already covers this; a failed socket is not fatal.
    }
    return () => socket?.close()
  }, [refresh])

  const account = status?.account
  const risk = status?.risk
  const live = status?.mode === 'live'
  const totalPnl = account?.total_pnl ?? 0
  const robotic = status?.robotic_trading ?? false

  const guarded = async (action: () => Promise<unknown>) => {
    setActing(true)
    try {
      await action()
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setActing(false)
    }
  }

  const closePosition = async (sym: string) => {
    setClosing(sym)
    try {
      await api.closePosition(sym)
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setClosing(null)
    }
  }

  const killSwitch = () => {
    if (!confirm('Flatten every open position and stop trading for the day?')) return
    guarded(api.killSwitch)
  }

  const toggleRobotic = () => {
    const enabling = !robotic
    if (
      enabling &&
      !confirm(
        'Arm robotic trading? Every cycle will scan the universe and place ' +
          `${live ? 'REAL' : 'paper'} orders in the strongest candidates without asking.`,
      )
    ) {
      return
    }
    guarded(() => api.updateSettings({ robotic_trading: enabling }))
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">₹</span>
          <span>
            <strong>Trading Desk</strong>
            <span className="brand-sub">ICICI Direct · Breeze</span>
          </span>
        </div>

        <div className="pills">
          <span className={`badge ${live ? 'live' : 'paper'}`}>
            <span className="dot" />
            {live ? 'LIVE TRADING' : 'PAPER'}
          </span>
          <span className={`badge ${status?.connected ? 'ok' : 'off'}`}>
            {status?.connected ? 'Breeze connected' : 'Breeze offline'}
          </span>
          <span className={`badge ${status?.market_open ? 'ok' : 'off'}`}>
            {status?.market_open ? 'Market open' : 'Market closed'}
          </span>
          {risk?.halted && <span className="badge live">HALTED</span>}
        </div>

        <div className="spacer" />

        {/* The robotic switch lives here rather than in Settings because arming
            it is the single most consequential thing on the page, and it must be
            visible — and reversible — from wherever you happen to be. */}
        <button
          className={`robo ${robotic ? 'on' : ''}`}
          onClick={toggleRobotic}
          disabled={acting}
          title={
            robotic
              ? 'Robotic trading is armed — the engine is placing orders on its own'
              : 'Arm robotic trading: scan the universe and open the strongest candidates'
          }
        >
          <span className="switch">
            <span className="switch-track" data-on={robotic || undefined} />
          </span>
          <span>
            Robotic
            <span className="robo-state">{robotic ? 'ARMED' : 'off'}</span>
          </span>
        </button>

        <span className="dim meta">
          {status?.interval} · {status?.cycles ?? 0} cycles
        </span>

        <button
          className="icon"
          onClick={toggleTheme}
          title={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
          aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
        >
          {theme === 'light' ? '🌙' : '☀️'}
        </button>
        <button onClick={() => guarded(api.runCycle)} disabled={acting || !status?.connected}>
          Run cycle
        </button>
        {risk?.halted ? (
          <button onClick={() => guarded(api.resume)} disabled={acting}>
            Resume
          </button>
        ) : (
          <button className="danger" onClick={killSwitch} disabled={acting}>
            Kill switch
          </button>
        )}
      </header>

      <TickerTape
        quotes={quotes}
        status={tickerStatus}
        connected={status?.connected ?? false}
      />

      {error && <div className="notice error">{error}</div>}

      {status?.live_mode_warning && (
        <div className="notice warn">{status.live_mode_warning}</div>
      )}

      {live && (
        <div className="notice error">
          <strong>Live mode is active.</strong> Real orders are being placed against your ICICI
          Direct account with real money.
        </div>
      )}

      {robotic && (
        <div className={`notice ${live ? 'error' : 'warn'}`}>
          <strong>Robotic trading is armed.</strong> Each cycle scans the universe and
          opens up to {status?.robotic_max_positions ?? 0} positions on its own
          {live ? ' with real money' : ' on paper'}. Turn it off in the header, or use the
          kill switch to flatten and stop.
        </div>
      )}

      {risk?.halted && (
        <div className="notice warn">
          <strong>Trading halted.</strong> {risk.halted_reason}
        </div>
      )}

      {status?.last_error && <div className="notice warn">{status.last_error}</div>}

      {account && risk && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="stats">
            <div className="stat">
              <div className="stat-label">Equity</div>
              <div className="stat-value">{formatCurrency(account.equity)}</div>
              <div className="stat-sub">cash {formatCurrency(account.cash)}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Total P&L</div>
              <div className={`stat-value ${totalPnl >= 0 ? 'pos' : 'neg'}`}>
                {formatCurrency(totalPnl)}
              </div>
              <div className="stat-sub">
                realised {formatCurrency(account.realised_pnl)}
              </div>
            </div>
            <div className="stat">
              <div className="stat-label">Today</div>
              <div className={`stat-value ${risk.daily_pnl >= 0 ? 'pos' : 'neg'}`}>
                {formatCurrency(risk.daily_pnl)}
              </div>
              <div className="stat-sub">
                limit −{formatNumber(risk.max_daily_loss_pct)}%
              </div>
            </div>
            <div className="stat">
              <div className="stat-label">Open</div>
              <div className="stat-value">
                {account.open_positions}
                <span className="dim" style={{ fontSize: 14 }}>
                  /{risk.max_open_positions}
                </span>
              </div>
            </div>
            <div className="stat">
              <div className="stat-label">Win rate</div>
              <div className="stat-value">{formatNumber(account.win_rate)}%</div>
              <div className="stat-sub">
                {account.wins}W / {account.losses}L
              </div>
            </div>
            <div className="stat">
              <div className="stat-label">Risk / trade</div>
              <div className="stat-value">{formatNumber(risk.risk_per_trade_pct, 1)}%</div>
              <div className="stat-sub">
                R:R 1:{formatNumber(risk.risk_reward_ratio, 1)}
                {risk.trailing_stop ? ' · trailing' : ''}
              </div>
            </div>
          </div>
        </div>
      )}

      <nav className="nav">
        {NAV.map((section) => (
          <div className="nav-group" key={section.group}>
            <span className="nav-group-label">{section.group}</span>
            {section.tabs.map((t) => (
              <button
                key={t.id}
                className={`tab ${tab === t.id ? 'active' : ''}`}
                onClick={() => setTab(t.id)}
                title={t.hint}
              >
                {t.label}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <p className="tab-hint">{ALL_TABS.find((t) => t.id === tab)?.hint}</p>

      {tab === 'live' && (
        <div className="grid" style={{ gap: 16 }}>
          <div className="grid grid-2">
            <SessionPanel
              connected={status?.connected ?? false}
              loginUrl={status?.login_url}
              sessionAgeHours={status?.session_age_hours ?? null}
              onConnected={refresh}
            />
            <ServerControl />
          </div>

          <div className="grid grid-2">
            <div className="grid" style={{ gap: 16 }}>
              {symbol && status && (
                <PriceChart
                  symbol={symbol}
                  symbols={status.symbols}
                  onSymbolChange={setSymbol}
                  theme={theme}
                />
              )}
              <PositionsTable
                positions={positions}
                onClose={closePosition}
                busy={closing}
              />
            </div>

            <div className="grid" style={{ gap: 16 }}>
              <SignalsPanel signals={signals} />
              <EventLog events={events} />
            </div>
          </div>
        </div>
      )}

      {tab === 'screener' && <StockScreener />}

      {tab === 'analyse' && <AnalysePanel universe={status?.symbols ?? []} />}

      {tab === 'trade' && (
        <TradePanel universe={status?.symbols ?? []} mode={status?.mode ?? 'paper'} />
      )}

      {tab === 'robotic' && <RoboticPanel />}

      {tab === 'orders' && <OrdersPanel />}

      {tab === 'options' && <OptionChain mode={status?.mode ?? 'paper'} />}

      {tab === 'settings' && <SettingsPanel onChanged={refresh} />}

      {tab === 'backtest' && <BacktestPanel symbols={status?.symbols ?? []} />}

      {tab === 'history' && <TradesTable trades={trades} onRefresh={refresh} />}
    </div>
  )
}
