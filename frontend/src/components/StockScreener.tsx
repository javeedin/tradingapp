import { useEffect, useState } from 'react'
import { formatNumber } from '../api'

interface Candidate {
  symbol: string
  current_price: number
  signal_score: number
  signals: {
    volume_spike: number
    breakout: number
    rsi: number
    macd: number
    beta: number
  }
  entry_price: number
  stop_loss: number
  target_1: number
  target_2: number
  target_3: number
  volume_current: number
  volume_avg: number
  rsi: number
  beta: number
}

interface ScreenerData {
  scan_timestamp: string
  candidates: Candidate[]
  total_candidates: number
}

export default function StockScreener() {
  const [data, setData] = useState<ScreenerData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchScreenerResults = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/screener/latest')
      if (!response.ok) throw new Error('Failed to fetch screener data')
      const result = await response.json()
      setData(result)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const triggerScan = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/screener/scan', { method: 'POST' })
      if (!response.ok) throw new Error('Failed to trigger scan')
      const result = await response.json()
      setData(result)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchScreenerResults()
    const interval = setInterval(fetchScreenerResults, 5 * 60 * 1000) // Refresh every 5 mins
    return () => clearInterval(interval)
  }, [])

  const getScoreBadgeColor = (score: number) => {
    if (score >= 80) return 'rgba(18,138,77,0.2)' // Green
    if (score >= 60) return 'rgba(251,188,5,0.2)' // Yellow
    return 'rgba(211,47,54,0.2)' // Red
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <span>🔍 Stock Screener (High Beta 2%+ Potential)</span>
        <div className="spacer" />
        <button
          className="primary"
          onClick={triggerScan}
          disabled={loading}
          title="Manually trigger a stock scan"
        >
          {loading ? 'Scanning...' : 'Scan Now'}
        </button>
      </div>

      <div className="panel-body">
        {error && (
          <div className="notice error" style={{ marginBottom: 12 }}>
            {error}
          </div>
        )}

        {data && (
          <>
            <div style={{ marginBottom: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
              Last scan: {new Date(data.scan_timestamp).toLocaleTimeString()} | Found{' '}
              <strong>{data.total_candidates}</strong> candidates
            </div>

            {data.candidates.length === 0 ? (
              <div className="empty">No candidates found in latest scan.</div>
            ) : (
              <div className="table-scroll" style={{ maxHeight: 500, overflowY: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th className="num">Score</th>
                      <th className="num">Price</th>
                      <th className="num">SL</th>
                      <th className="num">T1</th>
                      <th className="num">T2</th>
                      <th className="num">T3</th>
                      <th className="num">RSI</th>
                      <th className="num">Beta</th>
                      <th className="num">Vol Ratio</th>
                      <th>Signals</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.candidates.map((candidate) => (
                      <tr key={candidate.symbol}>
                        <td style={{ fontWeight: 600 }}>{candidate.symbol}</td>
                        <td className="num">
                          <span
                            style={{
                              padding: '2px 8px',
                              borderRadius: 4,
                              background: getScoreBadgeColor(candidate.signal_score),
                              fontWeight: 600,
                            }}
                          >
                            {candidate.signal_score}
                          </span>
                        </td>
                        <td className="num">{formatNumber(candidate.current_price, 2)}</td>
                        <td className="num" style={{ color: 'rgba(211,47,54,0.7)' }}>
                          {formatNumber(candidate.stop_loss, 2)}
                        </td>
                        <td className="num" style={{ color: 'rgba(18,138,77,0.7)' }}>
                          {formatNumber(candidate.target_1, 2)}
                        </td>
                        <td className="num" style={{ color: 'rgba(18,138,77,0.7)' }}>
                          {formatNumber(candidate.target_2, 2)}
                        </td>
                        <td className="num" style={{ color: 'rgba(18,138,77,0.7)' }}>
                          {formatNumber(candidate.target_3, 2)}
                        </td>
                        <td className="num">{formatNumber(candidate.rsi, 0)}</td>
                        <td className="num">{formatNumber(candidate.beta, 2)}</td>
                        <td className="num">
                          {(candidate.volume_current / candidate.volume_avg).toFixed(1)}x
                        </td>
                        <td style={{ fontSize: 11 }}>
                          <div>V:{candidate.signals.volume_spike}</div>
                          <div>B:{candidate.signals.breakout}</div>
                          <div>R:{candidate.signals.rsi}</div>
                          <div>M:{candidate.signals.macd}</div>
                          <div>β:{candidate.signals.beta}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        <div
          className="notice info"
          style={{ marginTop: 12, marginBottom: 0, fontSize: 12, lineHeight: 1.5 }}
        >
          <strong>How it works:</strong> Scans all liquid NSE stocks every 5 minutes during market
          hours (9:15 AM - 3 PM). Scores based on: Volume spike (25pts), Price breakout (25pts),
          RSI momentum (20pts), MACD positive (15pts), Beta 1.1-3.5 (15pts). SL = Entry - 2%,
          Targets = +2%, +3.5%, +5%
        </div>
      </div>
    </div>
  )
}
