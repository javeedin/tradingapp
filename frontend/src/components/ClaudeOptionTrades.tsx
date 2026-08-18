import { useCallback, useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { OptionChainResponse, OptionPositionsResponse } from '../types'

interface ClaudeDecision {
  strike: number
  side: 'BUY' | 'SELL'
  confidence: number
  reasoning: string
  entry_target: number
  stop_loss: number
  target: number
  risk_reward_ratio: number
}

export default function ClaudeOptionTrades() {
  const [chain, setChain] = useState<OptionChainResponse | null>(null)
  const [positions, setPositions] = useState<OptionPositionsResponse | null>(null)
  const [decision, setDecision] = useState<ClaudeDecision | null>(null)
  const [loading, setLoading] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [placing, setPlacing] = useState(false)
  const [underlying, setUnderlying] = useState('NIFTY')

  const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [chainData, posData] = await Promise.all([
        api.optionChain(underlying),
        api.optionPositions(),
      ])
      setChain(chainData)
      setPositions(posData)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [underlying])

  const askClaudeDecision = useCallback(async () => {
    if (!chain) return

    setAnalyzing(true)
    try {
      const response = await fetch('/api/options/claude-decision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          underlying,
          chain: chain,
        }),
      })

      if (!response.ok) throw new Error('Failed to get Claude decision')
      const result = await response.json()
      setDecision(result)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setAnalyzing(false)
    }
  }, [chain, underlying])

  const placeOrder = useCallback(async () => {
    if (!decision) return

    if (!confirm(`Place ${decision.side} call at strike ${decision.strike}?\n\nEntry: ₹${formatNumber(decision.entry_target)}\nStop Loss: ₹${formatNumber(decision.stop_loss)}\nTarget: ₹${formatNumber(decision.target)}`)) {
      return
    }

    setPlacing(true)
    try {
      await api.placeOptionOrder({
        underlying,
        expiry: chain?.expiry || '',
        strike: decision.strike,
        right: 'call',
        side: decision.side === 'BUY' ? 'buy' : 'sell',
        lots: 1,
      })

      setError(null)
      setDecision(null)
      await fetchData()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setPlacing(false)
    }
  }, [decision, chain, underlying, fetchData])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [fetchData])

  const tradePositions = positions?.positions ?? []
  const confidenceColor = decision ? (
    decision.confidence >= 80 ? 'rgba(18,138,77,0.2)' :
    decision.confidence >= 60 ? 'rgba(251,188,5,0.2)' :
    'rgba(211,47,54,0.2)'
  ) : ''

  return (
    <div className="panel">
      <div className="panel-head">
        <span>🤖 Claude Option Trades</span>
        <div className="spacer" />
        <select
          value={underlying}
          onChange={(e) => setUnderlying(e.target.value)}
          style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)' }}
        >
          {UNDERLYINGS.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
        <button
          className="primary"
          onClick={askClaudeDecision}
          disabled={analyzing || !chain}
          title="Ask Claude to analyze strikes and recommend a trade"
        >
          {analyzing ? 'Analyzing…' : 'Ask Claude'}
        </button>
      </div>

      <div className="panel-body">
        {error && <div className="notice error" style={{ marginBottom: 12 }}>{error}</div>}

        {/* Claude Decision Section */}
        {decision && (
          <div style={{ marginBottom: 20, padding: 12, background: confidenceColor, borderRadius: 6, border: '1px solid var(--border)' }}>
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Claude's Analysis
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                {decision.reasoning}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12, fontSize: 12 }}>
              <div>
                <span style={{ color: 'var(--text-secondary)' }}>Strike: </span>
                <strong>{decision.strike}</strong>
              </div>
              <div>
                <span style={{ color: 'var(--text-secondary)' }}>Confidence: </span>
                <strong>{decision.confidence}%</strong>
              </div>
              <div>
                <span style={{ color: 'var(--text-secondary)' }}>Entry Target: </span>
                <strong>₹{formatNumber(decision.entry_target)}</strong>
              </div>
              <div>
                <span style={{ color: 'var(--text-secondary)' }}>Risk/Reward: </span>
                <strong>{decision.risk_reward_ratio.toFixed(2)}</strong>
              </div>
              <div>
                <span style={{ color: 'rgba(211,47,54,0.7)' }}>Stop Loss: </span>
                <strong>₹{formatNumber(decision.stop_loss)}</strong>
              </div>
              <div>
                <span style={{ color: 'rgba(18,138,77,0.7)' }}>Target: </span>
                <strong>₹{formatNumber(decision.target)}</strong>
              </div>
            </div>

            <button
              className="primary"
              onClick={placeOrder}
              disabled={placing}
              style={{ width: '100%' }}
            >
              {placing ? 'Placing Order…' : `Place ${decision.side} Call Order`}
            </button>
          </div>
        )}

        {/* Open Positions Section */}
        {tradePositions.length > 0 && (
          <>
            <div style={{ marginBottom: 12, fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>
              Open Option Trades ({tradePositions.length})
            </div>

            <div className="table-scroll" style={{ maxHeight: 400, overflowY: 'auto', marginBottom: 20 }}>
              <table style={{ fontSize: 12 }}>
                <thead>
                  <tr style={{ background: 'var(--bg-secondary)' }}>
                    <th>Contract</th>
                    <th className="num">Entry</th>
                    <th className="num">Current</th>
                    <th className="num">P&L</th>
                    <th className="num">Stop</th>
                    <th className="num">Target</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {tradePositions.map((p) => {
                    const pnl = p.unrealized_pnl
                    const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)'
                    return (
                      <tr key={p.symbol}>
                        <td style={{ fontWeight: 600 }}>
                          <div>{p.contract.label}</div>
                          <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>
                            {Math.round(p.quantity / p.contract.lot_size)} lot(s)
                          </div>
                        </td>
                        <td className="num">{formatNumber(p.entry_price, 2)}</td>
                        <td className="num">{formatNumber(p.last_price, 2)}</td>
                        <td className="num" style={{ color: pnlColor, fontWeight: 600 }}>
                          {formatCurrency(pnl)}
                          <div style={{ fontSize: 10, marginTop: 2 }}>
                            {p.unrealized_pnl_pct >= 0 ? '+' : ''}{formatNumber(p.unrealized_pnl_pct, 1)}%
                          </div>
                        </td>
                        <td className="num" style={{ color: 'rgba(211,47,54,0.7)' }}>
                          {formatNumber(p.stoploss, 2)}
                        </td>
                        <td className="num" style={{ color: 'rgba(18,138,77,0.7)' }}>
                          {formatNumber(p.target, 2)}
                        </td>
                        <td>
                          <button
                            className="mini"
                            onClick={() => {
                              if (confirm(`Close ${p.contract.label}?`)) {
                                api.closePosition(p.symbol).then(() => fetchData())
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

        {tradePositions.length === 0 && !decision && (
          <div style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '20px 0' }}>
            No open option trades. Click "Ask Claude" to get a recommendation.
          </div>
        )}

        {loading && (
          <div style={{ color: 'var(--text-secondary)', textAlign: 'center', padding: '20px 0' }}>
            Loading option chain data…
          </div>
        )}
      </div>
    </div>
  )
}
