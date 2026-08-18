import { useEffect, useState } from 'react'
import { api, formatNumber } from '../api'
import type { OptionChainResponse } from '../types'

function num(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '—' : formatNumber(value, digits)
}

export default function StrikePrices() {
  const [underlying, setUnderlying] = useState('NIFTY')
  const [expiry, setExpiry] = useState<string | null>(null)
  const [chain, setChain] = useState<OptionChainResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const UNDERLYINGS = ['NIFTY', 'CNXBAN', 'RELIND', 'TCS', 'INFTEC', 'HDFBAN']
  const STRIKE_WINDOW = 8

  useEffect(() => {
    const fetchExpiries = async () => {
      try {
        const result = await api.expiries(underlying)
        if (result.expiries.length > 0) {
          setExpiry(result.expiries[0].date)
        }
      } catch (err) {
        setError((err as Error).message)
      }
    }
    fetchExpiries()
  }, [underlying])

  useEffect(() => {
    if (!expiry) return

    const fetchChain = async () => {
      setLoading(true)
      setError(null)
      try {
        const result = await api.optionChain(underlying, expiry)
        setChain(result)
      } catch (err) {
        setError((err as Error).message)
      } finally {
        setLoading(false)
      }
    }

    fetchChain()
    const interval = setInterval(fetchChain, 2000)
    return () => clearInterval(interval)
  }, [underlying, expiry])

  if (!chain) {
    return (
      <div className="panel">
        <div className="panel-head">
          <span>📊 Option Chain Strike Prices</span>
        </div>
        <div className="panel-body">
          {loading ? (
            <div className="dim">Loading option chain...</div>
          ) : error ? (
            <div className="notice error">{error}</div>
          ) : (
            <div className="dim">Select an underlying to view strike prices</div>
          )}
        </div>
      </div>
    )
  }

  const spot = chain.spot || 0
  const visibleRows = (chain.rows || []).filter((row) => {
    const distance = Math.abs(row.strike - spot)
    return distance <= (chain.lot_size || 100) * (STRIKE_WINDOW / 4)
  }).sort((a, b) => a.strike - b.strike)

  return (
    <div className="panel">
      <div className="panel-head">
        <span>📊 Option Chain Strike Prices</span>
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
        {expiry && (
          <span className="dim" style={{ fontSize: 11, marginLeft: 8 }}>
            {new Date(expiry).toLocaleDateString('en-IN')}
          </span>
        )}
      </div>

      <div className="panel-body">
        {error && <div className="notice error">{error}</div>}

        <div style={{ fontSize: 12, marginBottom: 12, color: 'var(--text-secondary)' }}>
          Spot: <strong>{num(spot)}</strong> | Expiry: <strong>{expiry}</strong>
        </div>

        <div style={{ overflowX: 'auto', maxHeight: 600, overflowY: 'auto' }}>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>
                <th className="num" style={{ padding: '6px 4px', borderBottom: '1px solid var(--border)' }}>
                  CALL BID
                </th>
                <th className="num" style={{ padding: '6px 4px', borderBottom: '1px solid var(--border)' }}>
                  CALL LTP
                </th>
                <th className="num" style={{ padding: '6px 4px', borderBottom: '1px solid var(--border)' }}>
                  CALL ASK
                </th>
                <th className="num" style={{ padding: '6px 4px', borderBottom: '1px solid var(--border)', minWidth: 60, background: 'rgba(100,100,100,0.1)', fontWeight: 600 }}>
                  STRIKE
                </th>
                <th className="num" style={{ padding: '6px 4px', borderBottom: '1px solid var(--border)' }}>
                  PUT ASK
                </th>
                <th className="num" style={{ padding: '6px 4px', borderBottom: '1px solid var(--border)' }}>
                  PUT LTP
                </th>
                <th className="num" style={{ padding: '6px 4px', borderBottom: '1px solid var(--border)' }}>
                  PUT BID
                </th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => {
                const isATM = Math.abs(row.strike - spot) < (visibleRows.length > 1 ? Math.abs(visibleRows[1].strike - visibleRows[0].strike) : 100)
                const bgColor = isATM ? 'rgba(100,150,255,0.1)' : row.strike < spot ? 'rgba(18,138,77,0.05)' : 'rgba(211,47,54,0.05)'

                return (
                  <tr key={row.strike} style={{ background: bgColor }}>
                    <td className="num" style={{ padding: '4px', fontSize: 11, color: 'var(--green)' }}>
                      {num(row.call?.bid, 2)}
                    </td>
                    <td className="num" style={{ padding: '4px', fontSize: 11, fontWeight: 600 }}>
                      {num(row.call?.ltp, 2)}
                    </td>
                    <td className="num" style={{ padding: '4px', fontSize: 11, color: 'var(--red)' }}>
                      {num(row.call?.ask, 2)}
                    </td>
                    <td className="num" style={{ padding: '4px', fontSize: 11, fontWeight: 600, background: 'rgba(100,100,100,0.1)' }}>
                      {num(row.strike, 0)}
                    </td>
                    <td className="num" style={{ padding: '4px', fontSize: 11, color: 'var(--red)' }}>
                      {num(row.put?.ask, 2)}
                    </td>
                    <td className="num" style={{ padding: '4px', fontSize: 11, fontWeight: 600 }}>
                      {num(row.put?.ltp, 2)}
                    </td>
                    <td className="num" style={{ padding: '4px', fontSize: 11, color: 'var(--green)' }}>
                      {num(row.put?.bid, 2)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <div style={{ fontSize: 11, marginTop: 12, color: 'var(--text-secondary)' }}>
          Showing {visibleRows.length} strikes around spot. Updates every 2 seconds.
        </div>
      </div>
    </div>
  )
}
