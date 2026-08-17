import { useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { BacktestResponse } from '../types'
import TradesTable from './TradesTable'

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

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      setResult(
        await api.backtest({
          days,
          symbols,
          intraday,
          entry_threshold: threshold,
        }),
      )
    } catch (err) {
      setError((err as Error).message)
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const stats = result?.stats

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <span>Backtest</span>
        </div>
        <div className="panel-body">
          <div className="notice info">
            Backtests run over candles already stored locally, using the same signal engine and
            risk rules as live trading. Results exclude liquidity, partial fills, and statutory
            charges beyond brokerage — treat them as an upper bound, not a forecast.
          </div>

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
            <button className="primary" onClick={run} disabled={busy}>
              {busy ? 'Running…' : 'Run backtest'}
            </button>
          </div>

          {error && (
            <div className="notice error" style={{ marginTop: 12 }}>
              {error}
            </div>
          )}
        </div>
      </div>

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
