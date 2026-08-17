import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatCurrency, formatNumber } from './api'
import AnalysePanel from './components/AnalysePanel'
import BacktestPanel from './components/BacktestPanel'
import EventLog from './components/EventLog'
import OptionChain from './components/OptionChain'
import PositionsTable from './components/PositionsTable'
import PriceChart from './components/PriceChart'
import SessionPanel from './components/SessionPanel'
import SignalsPanel from './components/SignalsPanel'
import TickerTape from './components/TickerTape'
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

const TABS = [
  { id: 'live', label: 'Live' },
  { id: 'analyse', label: 'Analyse' },
  { id: 'options', label: 'Options' },
  { id: 'backtest', label: 'Backtest' },
  { id: 'history', label: 'History' },
] as const

type Tab = (typeof TABS)[number]['id']

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

  const killSwitch = async () => {
    if (!confirm('Flatten every open position and stop trading for the day?')) return
    setActing(true)
    try {
      await api.killSwitch()
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setActing(false)
    }
  }

  const resume = async () => {
    setActing(true)
    try {
      await api.resume()
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setActing(false)
    }
  }

  const runCycle = async () => {
    setActing(true)
    try {
      await api.runCycle()
      await refresh()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setActing(false)
    }
  }

  const account = status?.account
  const risk = status?.risk
  const live = status?.mode === 'live'
  const totalPnl = account?.total_pnl ?? 0

  return (
    <div className="app">
      <div className="topbar">
        <h1>Trading Dashboard</h1>
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

        <div className="spacer" />

        <span className="dim" style={{ fontSize: 12 }}>
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
        <button onClick={runCycle} disabled={acting || !status?.connected}>
          Run cycle
        </button>
        {risk?.halted ? (
          <button onClick={resume} disabled={acting}>
            Resume
          </button>
        ) : (
          <button className="danger" onClick={killSwitch} disabled={acting}>
            Kill switch
          </button>
        )}
      </div>

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

      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'live' && (
        <div className="grid" style={{ gap: 16 }}>
          <SessionPanel
            connected={status?.connected ?? false}
            loginUrl={status?.login_url}
            sessionAgeHours={status?.session_age_hours ?? null}
            onConnected={refresh}
          />

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

      {tab === 'analyse' && <AnalysePanel universe={status?.symbols ?? []} />}

      {tab === 'options' && <OptionChain />}

      {tab === 'backtest' && <BacktestPanel symbols={status?.symbols ?? []} />}

      {tab === 'history' && <TradesTable trades={trades} />}
    </div>
  )
}
