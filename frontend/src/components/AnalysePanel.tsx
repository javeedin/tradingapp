import { useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { Analysis, FactorBreakdown } from '../types'

const FACTORS: { key: keyof FactorBreakdown; label: string; hint: string }[] = [
  { key: 'regime', label: 'Regime', hint: 'Broad market direction (NIFTY vs its EMAs)' },
  { key: 'trend', label: 'Trend', hint: 'EMA structure, MACD, Supertrend' },
  { key: 'momentum', label: 'Momentum', hint: 'RSI position and rate of change' },
  { key: 'volatility', label: 'Volatility', hint: 'ATR — is the move tradable?' },
  { key: 'volume', label: 'Volume', hint: 'Participation behind the move' },
]

const INTERVALS = ['5minute', '30minute', '1day']

function verdictTone(action: string): string {
  if (action === 'buy') return 'ok'
  if (action === 'sell') return 'live'
  return 'off'
}

export default function AnalysePanel({ universe }: { universe: string[] }) {
  const [symbol, setSymbol] = useState('')
  const [interval, setInterval] = useState('5minute')
  const [intraday, setIntraday] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Analysis | null>(null)

  const run = async (target?: string) => {
    const query = (target ?? symbol).trim()
    if (!query) return
    if (target) setSymbol(target)

    setBusy(true)
    setError(null)
    try {
      setResult(await api.analyse(query, { interval, intraday }))
    } catch (err) {
      setError((err as Error).message)
      setResult(null)
    } finally {
      setBusy(false)
    }
  }

  const plan = result?.plan
  const market = result?.market
  const isBuy = result?.side === 'buy'

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <span>Analyse a Stock</span>
        </div>
        <div className="panel-body">
          <div className="row">
            <input
              style={{ flex: 1, minWidth: 200 }}
              placeholder="Breeze stock code, e.g. RELIND, TCS, INFTEC"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === 'Enter' && run()}
            />
            <select value={interval} onChange={(e) => setInterval(e.target.value)}>
              {INTERVALS.map((i) => (
                <option key={i} value={i}>
                  {i}
                </option>
              ))}
            </select>
            <label className="dim row" style={{ gap: 6 }}>
              <input
                type="checkbox"
                checked={intraday}
                onChange={(e) => setIntraday(e.target.checked)}
              />
              Intraday
            </label>
            <button className="primary" onClick={() => run()} disabled={busy || !symbol.trim()}>
              {busy ? 'Analysing…' : 'Analyse'}
            </button>
          </div>

          {universe.length > 0 && (
            <div className="row" style={{ marginTop: 10, gap: 6 }}>
              <span className="dim" style={{ fontSize: 11 }}>
                Watchlist:
              </span>
              {universe.map((s) => (
                <button
                  key={s}
                  onClick={() => run(s)}
                  disabled={busy}
                  style={{ padding: '3px 9px', fontSize: 12 }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {error && (
            <div className="notice error" style={{ marginTop: 12, marginBottom: 0 }}>
              {error}
            </div>
          )}
        </div>
      </div>

      {result && plan && market && (
        <>
          <div className="panel">
            <div className="panel-head">
              <span>{result.symbol}</span>
              <span className={`badge ${verdictTone(result.action)}`}>
                {result.action.toUpperCase()}
              </span>
              <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
                {result.conviction} · score {result.score >= 0 ? '+' : ''}
                {formatNumber(result.score, 3)} (threshold ±
                {formatNumber(result.entry_threshold, 2)})
              </span>
              <div className="spacer" />
              <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
                {result.interval} · {result.candles_used} bars ·{' '}
                {new Date(result.as_of).toLocaleString('en-IN', { hour12: false })}
              </span>
            </div>

            <div className="stats">
              <div className="stat">
                <div className="stat-label">{isBuy ? 'Buy at' : 'Sell at'}</div>
                <div className="stat-value">{formatNumber(plan.entry)}</div>
                <div className="stat-sub">current price</div>
              </div>
              <div className="stat">
                <div className="stat-label">Stoploss</div>
                <div className="stat-value neg">{formatNumber(plan.stoploss)}</div>
                <div className="stat-sub">
                  −{formatNumber(plan.stop_distance_pct)}% · {formatNumber(plan.stop_distance)} pts
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">Target</div>
                <div className="stat-value pos">{formatNumber(plan.target)}</div>
                <div className="stat-sub">
                  +{formatNumber(plan.target_distance_pct)}% ·{' '}
                  {formatNumber(plan.target_distance)} pts
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">Quantity</div>
                <div className="stat-value">{plan.quantity}</div>
                <div className="stat-sub">{formatCurrency(plan.notional)} exposure</div>
              </div>
              <div className="stat">
                <div className="stat-label">Risk</div>
                <div className="stat-value">{formatCurrency(plan.risk_amount)}</div>
                <div className="stat-sub">
                  {formatNumber(plan.risk_pct_of_equity)}% of equity
                </div>
              </div>
              <div className="stat">
                <div className="stat-label">Risk : Reward</div>
                <div className="stat-value">1:{formatNumber(plan.risk_reward, 2)}</div>
                <div className="stat-sub">{plan.product}</div>
              </div>
            </div>

            <div className="panel-body">
              {result.action === 'hold' && (
                <div className="notice info">
                  <strong>No entry yet.</strong> The score is below the threshold, so the engine
                  would not open a position. The levels above are what it{' '}
                  <em>would</em> use — useful for setting an alert and waiting.
                </div>
              )}

              {plan.was_capped && (
                <div className="notice warn">
                  Size reduced by <code>{plan.binding_constraint}</code>. {plan.sizing_note}
                </div>
              )}

              <div className="factors" style={{ marginBottom: 14 }}>
                {FACTORS.map(({ key, label, hint }) => {
                  const value = result.factors[key]
                  const tone = value > 0.05 ? 'pos' : value < -0.05 ? 'neg' : 'dim'
                  return (
                    <div className="factor" key={key} title={hint}>
                      <div className="factor-name">{label}</div>
                      <div className={`factor-value ${tone}`}>
                        {value >= 0 ? '+' : ''}
                        {formatNumber(value, 2)}
                      </div>
                    </div>
                  )
                })}
              </div>

              {result.reasons.length > 0 && (
                <>
                  <div className="stat-label" style={{ marginBottom: 6 }}>
                    Why
                  </div>
                  <ul
                    style={{
                      margin: 0,
                      paddingLeft: 18,
                      fontSize: 13,
                      lineHeight: 1.7,
                      color: 'var(--text-dim)',
                    }}
                  >
                    {result.reasons.map((reason, i) => (
                      <li key={i}>{reason}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span>Market Context</span>
              <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
                {market.trend}
              </span>
            </div>
            <div className="panel-body flush">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Price</th>
                      <th className="num">RSI</th>
                      <th className="num">ADX</th>
                      <th className="num">ATR</th>
                      <th className="num">ATR %</th>
                      <th className="num">EMA 20</th>
                      <th className="num">EMA 50</th>
                      <th className="num">EMA 200</th>
                      <th className="num">VWAP</th>
                      <th className="num">Vol vs avg</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {formatNumber(market.price)}
                      </td>
                      <td
                        className={`num ${market.rsi > 70 ? 'neg' : market.rsi < 30 ? 'pos' : ''}`}
                      >
                        {formatNumber(market.rsi, 1)}
                      </td>
                      <td className={`num ${market.adx >= 25 ? 'pos' : 'dim'}`}>
                        {formatNumber(market.adx, 1)}
                      </td>
                      <td className="num">{formatNumber(market.atr)}</td>
                      <td className="num">{formatNumber(market.atr_pct)}%</td>
                      <td className="num">{formatNumber(market.ema_20)}</td>
                      <td className="num">{formatNumber(market.ema_50)}</td>
                      <td className="num">{formatNumber(market.ema_200)}</td>
                      <td className={`num ${market.above_vwap ? 'pos' : 'neg'}`}>
                        {formatNumber(market.vwap)}
                      </td>
                      <td
                        className={`num ${market.volume_ratio >= 1.2 ? 'pos' : 'dim'}`}
                      >
                        {formatNumber(market.volume_ratio)}x
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
