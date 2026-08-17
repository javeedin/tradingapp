import { formatNumber } from '../api'
import type { FactorBreakdown, Signal } from '../types'

const FACTOR_LABELS: { key: keyof FactorBreakdown; label: string }[] = [
  { key: 'regime', label: 'Regime' },
  { key: 'trend', label: 'Trend' },
  { key: 'momentum', label: 'Mom' },
  { key: 'volatility', label: 'Vol' },
  { key: 'volume', label: 'Vlm' },
]

function toneFor(value: number): string {
  if (value > 0.05) return 'pos'
  if (value < -0.05) return 'neg'
  return 'dim'
}

/**
 * Renders the composite score as a centre-anchored bar so the sign and the
 * magnitude are both readable at a glance, plus the per-factor breakdown that
 * produced it. Showing the inputs is the point: a score with no visible
 * derivation is impossible to trust or tune.
 */
export default function SignalsPanel({ signals }: { signals: Signal[] }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Live Signals</span>
        <span className="badge off">{signals.length}</span>
      </div>
      <div className="panel-body flush">
        {signals.length === 0 ? (
          <div className="empty">
            No signals yet — they appear once the engine completes a cycle.
          </div>
        ) : (
          <div className="signal-list">
            {signals.map((s) => {
              const pct = Math.min(Math.abs(s.score), 1) * 50
              const positive = s.score >= 0
              const actionClass =
                s.action === 'buy' ? 'ok' : s.action === 'sell' ? 'live' : 'off'

              return (
                <div className="signal" key={`${s.symbol}-${s.timestamp}`}>
                  <div className="signal-head">
                    <span className="signal-symbol">{s.symbol}</span>
                    <span className={`badge ${actionClass}`}>{s.action}</span>
                    <div className="spacer" />
                    <span className={`signal-score ${positive ? 'pos' : 'neg'}`}>
                      {positive ? '+' : ''}
                      {formatNumber(s.score, 3)}
                    </span>
                  </div>

                  <div className="score-bar">
                    <div className="mid" />
                    <div
                      className="fill"
                      style={{
                        left: positive ? '50%' : `${50 - pct}%`,
                        width: `${pct}%`,
                        background: positive ? 'var(--green)' : 'var(--red)',
                      }}
                    />
                  </div>

                  <div className="factors">
                    {FACTOR_LABELS.map(({ key, label }) => (
                      <div className="factor" key={key}>
                        <div className="factor-name">{label}</div>
                        <div className={`factor-value ${toneFor(s.factors[key])}`}>
                          {s.factors[key] >= 0 ? '+' : ''}
                          {formatNumber(s.factors[key], 2)}
                        </div>
                      </div>
                    ))}
                  </div>

                  {s.reasons.length > 0 && (
                    <div className="reasons">{s.reasons.slice(0, 4).join(' · ')}</div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
