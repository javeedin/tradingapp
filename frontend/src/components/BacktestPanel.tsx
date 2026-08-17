import { useCallback, useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { BacktestResponse, DataCoverage } from '../types'
import TradesTable from './TradesTable'

function shortDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' })
}

/**
 * What history is actually stored.
 *
 * A backtest returning no trades has two very different causes — the strategy
 * found nothing worth trading, or there was nothing to look at — and this is
 * what tells them apart. Symbols short of the indicator warm-up are called out
 * separately, because they cannot produce a signal at all.
 */
function Coverage({
  coverage,
  selected,
  onToggle,
}: {
  coverage: DataCoverage
  selected: Set<string>
  onToggle: (symbol: string) => void
}) {
  if (coverage.symbols.length === 0) {
    return (
      <div className="notice warn">
        <strong>No stored candles.</strong> Establish a Breeze session on the Live tab —
        the backfill runs automatically once a session exists, and a backtest has nothing
        to replay until it has.
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <span>Stored history</span>
        <span className="badge off">{coverage.total_candles.toLocaleString('en-IN')} candles</span>
        <span className="badge off">{coverage.interval}</span>
        {coverage.insufficient.length > 0 && (
          <span className="badge warn">
            {coverage.insufficient.length} short of warm-up
          </span>
        )}
        <div className="spacer" />
        <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
          click a row to include or exclude it
        </span>
      </div>
      <div className="panel-body flush">
        <div className="table-scroll" style={{ maxHeight: 300 }}>
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="num">Candles</th>
                <th className="num">Sessions</th>
                <th>From</th>
                <th>To</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {coverage.symbols.map((row) => {
                const ready = row.candles >= coverage.warmup_bars
                const on = selected.has(row.symbol)
                return (
                  <tr
                    key={row.symbol}
                    onClick={() => onToggle(row.symbol)}
                    style={{
                      cursor: 'pointer',
                      opacity: on ? 1 : 0.45,
                    }}
                  >
                    <td style={{ fontWeight: 600 }}>
                      <input
                        type="checkbox"
                        checked={on}
                        readOnly
                        style={{ marginRight: 8, verticalAlign: 'middle' }}
                      />
                      {row.symbol}
                    </td>
                    <td className="num">{row.candles.toLocaleString('en-IN')}</td>
                    <td className="num">{row.trading_days}</td>
                    <td className="dim" style={{ fontSize: 12 }}>
                      {shortDate(row.first_candle)}
                    </td>
                    <td className="dim" style={{ fontSize: 12 }}>
                      {shortDate(row.last_candle)}
                    </td>
                    <td>
                      {ready ? (
                        <span className="badge ok">ready</span>
                      ) : (
                        <span
                          className="badge warn"
                          title={`${coverage.warmup_bars} candles are needed before the indicators produce a signal`}
                        >
                          needs {coverage.warmup_bars - row.candles} more
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

/** Minimal inline equity curve — no charting library needed for a sparkline. */
function EquityCurve({ points }: { points: { timestamp: string; equity: number }[] }) {
  if (points.length < 2) return null

  const values = points.map((p) => p.equity)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const width = 800
  const height = 140

  const path = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * width
      const y = height - ((p.equity - min) / span) * height
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  const start = values[0]
  const end = values[values.length - 1]
  const up = end >= start
  const color = up ? 'var(--green)' : 'var(--red)'

  // Where the starting capital sits, so gains and losses read against a baseline.
  const baselineY = height - ((start - min) / span) * height

  return (
    <div style={{ padding: '4px 0 12px' }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        style={{ width: '100%', height: 140, display: 'block' }}
      >
        <line
          x1="0"
          y1={baselineY}
          x2={width}
          y2={baselineY}
          stroke="var(--text-faint)"
          strokeWidth="1"
          strokeDasharray="4 4"
        />
        <path d={path} fill="none" stroke={color} strokeWidth="1.5" />
      </svg>
      <div className="row" style={{ justifyContent: 'space-between', fontSize: 11 }}>
        <span className="dim">Start {formatCurrency(start)}</span>
        <span className={up ? 'pos' : 'neg'}>End {formatCurrency(end)}</span>
      </div>
    </div>
  )
}

export default function BacktestPanel({ symbols }: { symbols: string[] }) {
  const [days, setDays] = useState(60)
  const [threshold, setThreshold] = useState(0.35)
  const [intraday, setIntraday] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BacktestResponse | null>(null)
  const [coverage, setCoverage] = useState<DataCoverage | null>(null)
  const [selected, setSelected] = useState<Set<string> | null>(null)

  const loadCoverage = useCallback(async () => {
    try {
      const data = await api.dataCoverage()
      setCoverage(data)
      // Default to what can actually produce a signal, rather than to everything
      // stored — including a warm-up-short symbol just adds a silent no-op.
      setSelected((current) => current ?? new Set(data.ready))
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    loadCoverage()
  }, [loadCoverage])

  const toggle = (symbol: string) =>
    setSelected((current) => {
      const next = new Set(current ?? [])
      if (next.has(symbol)) next.delete(symbol)
      else next.add(symbol)
      return next
    })

  const chosen = [...(selected ?? new Set(symbols))]

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      setResult(
        await api.backtest({
          days,
          symbols: chosen,
          intraday,
          entry_threshold: threshold,
        }),
      )
      // History grows as the engine runs, so refresh the scope alongside.
      await loadCoverage()
    } catch (err) {
      setError((err as Error).message)
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const stats = result?.stats
  const policyMismatch =
    coverage && coverage.exit_policy !== coverage.backtested_policy

  // The requested window can far exceed what is stored, in which case the
  // backtest silently covers less than it appears to.
  const maxSessions = coverage?.symbols.reduce((m, r) => Math.max(m, r.trading_days), 0) ?? 0

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <span>Backtest</span>
          <div className="spacer" />
          <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
            {chosen.length} symbol(s) selected
          </span>
        </div>
        <div className="panel-body">
          <div className="notice info">
            Backtests run over candles already stored locally, using the same signal engine and
            risk rules as live trading. Results exclude liquidity, partial fills, and statutory
            charges beyond brokerage — treat them as an upper bound, not a forecast.
          </div>

          {/* The single most misleading thing this panel could do is report
              stoploss-only numbers while the engine is set to average down. */}
          {policyMismatch && (
            <div className="notice error">
              <strong>These results will not describe your strategy.</strong> The engine's
              exit policy is <code>{coverage!.exit_policy.replace(/_/g, ' ')}</code>, but the
              backtester only models <code>stoploss only</code>. Averaging down changes
              both the win rate and the shape of the losses — it wins for long stretches
              and then loses much more at once, and none of that appears below. Switch the
              policy on the Settings tab to compare like with like.
            </div>
          )}

          <div className="row">
            <label className="dim">
              Days{' '}
              <input
                type="number"
                min={1}
                max={900}
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                style={{ width: 80 }}
              />
            </label>
            <label className="dim">
              Entry threshold{' '}
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={threshold}
                onChange={(e) => setThreshold(Number(e.target.value))}
                style={{ width: 80 }}
              />
            </label>
            <label className="dim row" style={{ gap: 6 }}>
              <input
                type="checkbox"
                checked={intraday}
                onChange={(e) => setIntraday(e.target.checked)}
              />
              Intraday (square off daily)
            </label>
            <div className="spacer" />
            <button onClick={loadCoverage} disabled={busy}>
              Refresh data
            </button>
            <button className="primary" onClick={run} disabled={busy || chosen.length === 0}>
              {busy ? 'Running…' : 'Run backtest'}
            </button>
          </div>

          {maxSessions > 0 && days > maxSessions && (
            <div className="notice warn" style={{ marginTop: 12, marginBottom: 0 }}>
              You asked for {days} days but only {maxSessions} trading session(s) are
              stored, so this is a {maxSessions}-session backtest. Statistics over a
              window this short are noise, not evidence.
            </div>
          )}

          {error && (
            <div className="notice error" style={{ marginTop: 12, marginBottom: 0 }}>
              {error}
            </div>
          )}
        </div>
      </div>

      {coverage && (
        <Coverage
          coverage={coverage}
          selected={selected ?? new Set(symbols)}
          onToggle={toggle}
        />
      )}

      {stats && (
        <>
          <div className="panel">
            <div className="panel-head">
              <span>Results</span>
              <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
                {result!.symbols.join(', ')}
              </span>
            </div>
            <div className="panel-body flush">
              {stats.total_trades === 0 ? (
                <div className="empty">{stats.note ?? 'No trades generated'}</div>
              ) : (
                <>
                  <div className="stats">
                    <div className="stat">
                      <div className="stat-label">Net P&L</div>
                      <div className={`stat-value ${stats.net_pnl >= 0 ? 'pos' : 'neg'}`}>
                        {formatCurrency(stats.net_pnl)}
                      </div>
                      <div className="stat-sub">
                        {stats.total_return_pct >= 0 ? '+' : ''}
                        {formatNumber(stats.total_return_pct)}% return
                      </div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Trades</div>
                      <div className="stat-value">{stats.total_trades}</div>
                      <div className="stat-sub">
                        {stats.wins}W / {stats.losses}L
                      </div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Win rate</div>
                      <div className="stat-value">{formatNumber(stats.win_rate)}%</div>
                      <div className="stat-sub">
                        expectancy {formatCurrency(stats.expectancy ?? 0)}
                      </div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Max drawdown</div>
                      <div className="stat-value neg">
                        −{formatNumber(stats.max_drawdown_pct)}%
                      </div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Sharpe</div>
                      <div
                        className={`stat-value ${stats.sharpe_ratio >= 0 ? 'pos' : 'neg'}`}
                      >
                        {formatNumber(stats.sharpe_ratio)}
                      </div>
                      <div className="stat-sub">daily, annualised</div>
                    </div>
                    <div className="stat">
                      <div className="stat-label">Profit factor</div>
                      <div
                        className={`stat-value ${stats.profit_factor >= 1 ? 'pos' : 'neg'}`}
                      >
                        {formatNumber(stats.profit_factor)}
                      </div>
                      <div className="stat-sub">
                        costs {formatCurrency(stats.total_costs ?? 0)}
                      </div>
                    </div>
                  </div>

                  <div style={{ padding: '12px 16px 0' }}>
                    <EquityCurve points={result!.equity_curve} />
                    {stats.exits_by_reason && (
                      <div className="row" style={{ fontSize: 11, paddingBottom: 12 }}>
                        <span className="dim">Exits:</span>
                        {Object.entries(stats.exits_by_reason).map(([k, v]) => (
                          <span key={k} className="badge off">
                            {k.replace(/_/g, ' ')} {v}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>

          {result!.trades.length > 0 && (
            <TradesTable trades={result!.trades.slice(0, 100)} title="Backtest Trades" />
          )}
        </>
      )}
    </div>
  )
}
