import { useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { Position, Signal, Status } from '../types'

interface RoboticState {
  status: Status | null
  positions: Position[]
  signals: Signal[]
  loading: boolean
  error: string | null
}

export default function RoboticPanel() {
  const [state, setState] = useState<RoboticState>({
    status: null,
    positions: [],
    signals: [],
    loading: true,
    error: null,
  })

  useEffect(() => {
    const refresh = async () => {
      try {
        const [statusRes, posRes, sigRes] = await Promise.all([
          api.status(),
          api.positions(),
          api.signals(),
        ])
        setState({
          status: statusRes,
          positions: posRes.positions,
          signals: sigRes.latest,
          loading: false,
          error: null,
        })
      } catch (err) {
        setState((s) => ({
          ...s,
          error: (err as Error).message,
          loading: false,
        }))
      }
    }

    refresh()
    const interval = setInterval(refresh, 5000)
    return () => clearInterval(interval)
  }, [])

  const robotic = state.status?.robotic_trading ?? false
  const live = state.status?.mode === 'live'

  if (!robotic) {
    return (
      <div className="panel">
        <div className="panel-head">
          <span>🤖 Robotic Trading</span>
        </div>
        <div className="panel-body">
          <div className="notice warn">
            <strong>Robotic trading is OFF</strong>
            <p>Enable it from the top-right toggle to start automated trading.</p>
          </div>
        </div>
      </div>
    )
  }

  const getSignalBadgeColor = (signal: string) => {
    switch (signal.toLowerCase()) {
      case 'buy':
        return 'rgba(18,138,77,0.2)'
      case 'sell':
        return 'rgba(211,47,54,0.2)'
      default:
        return 'rgba(100,100,100,0.2)'
    }
  }

  const getSignalTextColor = (signal: string) => {
    switch (signal.toLowerCase()) {
      case 'buy':
        return 'rgba(18,138,77,0.9)'
      case 'sell':
        return 'rgba(211,47,54,0.9)'
      default:
        return 'rgba(100,100,100,0.9)'
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <span>🤖 Robotic Trading</span>
        <div className="spacer" />
        <span className={`badge ${live ? 'live' : 'ok'}`}>
          {live ? '🔴 LIVE TRADING' : '📋 PAPER MODE'}
        </span>
      </div>

      <div className="panel-body">
        {state.error && (
          <div className="notice error" style={{ marginBottom: 12 }}>
            {state.error}
          </div>
        )}

        <div style={{ marginBottom: 16, padding: 12, background: 'rgba(18,138,77,0.1)', borderRadius: 4 }}>
          <strong>Status: ARMED ✓</strong>
          <p style={{ fontSize: 12, margin: '8px 0 0 0', color: 'var(--text-secondary)' }}>
            Scanning universe every {state.status?.interval} and automatically opening positions in the
            strongest candidates.
          </p>
          <p style={{ fontSize: 12, margin: '4px 0 0 0', color: 'var(--text-secondary)' }}>
            Max positions: {state.status?.robotic_max_positions ?? 0} | Open: {state.positions.length}
          </p>
        </div>

        {state.positions.length === 0 ? (
          <div className="empty">
            No active positions. Robotic trading will open positions when it finds candidates matching
            the criteria.
          </div>
        ) : (
          <>
            <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
              <strong>{state.positions.length}</strong> stocks picked by robotic engine
            </div>

            <div className="table-scroll" style={{ maxHeight: 600, overflowY: 'auto' }}>
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th className="num">Entry</th>
                    <th className="num">Current</th>
                    <th className="num">P&L</th>
                    <th className="num">%</th>
                    <th className="num">Qty</th>
                    <th>Signal</th>
                    <th className="num">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {state.positions.map((pos) => {
                    const signal = state.signals.find((s) => s.symbol === pos.symbol)
                    const pnlPercent = ((pos.last_price - pos.entry_price) / pos.entry_price) * 100
                    const isProfit = pos.unrealized_pnl >= 0

                    return (
                      <tr key={pos.symbol}>
                        <td style={{ fontWeight: 600 }}>{pos.symbol}</td>
                        <td className="num">{formatCurrency(pos.entry_price)}</td>
                        <td className="num">{formatCurrency(pos.last_price)}</td>
                        <td className={`num ${isProfit ? 'pos' : 'neg'}`}>{formatCurrency(pos.unrealized_pnl)}</td>
                        <td className={`num ${isProfit ? 'pos' : 'neg'}`}>{formatNumber(pnlPercent, 2)}%</td>
                        <td className="num">{pos.quantity}</td>
                        <td>
                          {signal ? (
                            <span
                              style={{
                                padding: '2px 8px',
                                borderRadius: 4,
                                background: getSignalBadgeColor(signal.action),
                                color: getSignalTextColor(signal.action),
                                fontSize: 11,
                                fontWeight: 600,
                              }}
                            >
                              {signal.action.toUpperCase()}
                            </span>
                          ) : (
                            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>—</span>
                          )}
                        </td>
                        <td className="num">
                          <button
                            className="danger"
                            style={{ fontSize: 11, padding: '4px 8px' }}
                            onClick={() => {
                              if (confirm(`Close ${pos.symbol} position?`)) {
                                api.closePosition(pos.symbol).catch((e) =>
                                  setState((s) => ({ ...s, error: e.message }))
                                )
                              }
                            }}
                          >
                            Close
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div
          className="notice info"
          style={{ marginTop: 12, marginBottom: 0, fontSize: 12, lineHeight: 1.5 }}
        >
          <strong>How it works:</strong> Every {state.status?.interval}, the engine scans the universe,
          scores candidates based on volume, breakout, RSI, MACD, and beta. It automatically opens
          positions in the top candidates if you have available capital. You can manually close any
          position or disable robotic trading anytime.
        </div>
      </div>
    </div>
  )
}
