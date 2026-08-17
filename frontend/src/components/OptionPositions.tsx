import { useCallback, useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { OptionPositionsResponse } from '../types'

/**
 * Open option legs, with premiums re-quoted on load.
 *
 * Separate from the equity positions table because the columns mean different
 * things: the price is a premium, the quantity is units-not-lots, and the levels
 * are premium levels. Sharing one table would put four different units in the
 * same column.
 */
export default function OptionPositions({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData] = useState<OptionPositionsResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [closing, setClosing] = useState<string | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      setData(await api.optionPositions())
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  const close = async (key: string, price: number) => {
    if (!confirm(`Close ${key} at ₹${formatNumber(price)}?`)) return
    setClosing(key)
    try {
      await api.closePosition(key)
      await load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setClosing(null)
    }
  }

  const positions = data?.positions ?? []

  // Nothing open and nothing to say — stay out of the way.
  if (!error && positions.length === 0) return null

  return (
    <div className="panel">
      <div className="panel-head">
        <span>Option positions</span>
        {positions.length > 0 && (
          <span className="badge off">{positions.length} open</span>
        )}
        <div className="spacer" />
        {data && data.repriced < positions.length && (
          <span className="badge warn" title={data.note}>
            premiums stale
          </span>
        )}
        <button onClick={load} disabled={busy}>
          {busy ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="panel-body">
          <div className="notice error" style={{ marginBottom: 0 }}>
            {error}
          </div>
        </div>
      )}

      {positions.length > 0 && (
        <div className="panel-body flush">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Contract</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Premium</th>
                  <th className="num">Stop</th>
                  <th className="num">Target</th>
                  <th className="num">P&amp;L</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const tone = p.unrealized_pnl >= 0 ? 'pos' : 'neg'
                  const writing = p.side === 'sell'
                  return (
                    <tr key={p.symbol}>
                      <td>
                        <strong>{p.contract.label}</strong>
                        <span className="cell-sub">
                          {p.contract.lot_size} per lot ·{' '}
                          {Math.round(p.quantity / p.contract.lot_size)} lot(s)
                        </span>
                      </td>
                      <td>
                        <span className={`badge ${writing ? 'live' : 'ok'}`}>
                          {writing ? 'WRITE' : 'BUY'}
                        </span>
                      </td>
                      <td className="num">{p.quantity}</td>
                      <td className="num">{formatNumber(p.entry_price)}</td>
                      <td className="num">{formatNumber(p.last_price)}</td>
                      <td className="num neg">{formatNumber(p.stoploss)}</td>
                      <td className="num pos">{formatNumber(p.target)}</td>
                      <td className={`num ${tone}`}>
                        {formatCurrency(p.unrealized_pnl)}
                        <span className="cell-sub">
                          {p.unrealized_pnl_pct >= 0 ? '+' : ''}
                          {formatNumber(p.unrealized_pnl_pct)}%
                        </span>
                      </td>
                      <td>
                        <button
                          className="mini"
                          disabled={closing === p.symbol}
                          onClick={() => close(p.symbol, p.last_price)}
                        >
                          {closing === p.symbol ? '…' : 'Close'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
