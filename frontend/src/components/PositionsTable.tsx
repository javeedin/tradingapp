import { formatCurrency, formatNumber } from '../api'
import type { Position } from '../types'

interface Props {
  positions: Position[]
  onClose: (symbol: string) => void
  busy?: string | null
}

export default function PositionsTable({ positions, onClose, busy }: Props) {
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Open Positions</span>
        <span className="badge off">{positions.length}</span>
      </div>
      <div className="panel-body flush">
        {positions.length === 0 ? (
          <div className="empty">No open positions</div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Last</th>
                  <th className="num">Stop</th>
                  <th className="num">Target</th>
                  <th className="num">P&L</th>
                  <th className="num">%</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const up = p.unrealized_pnl >= 0
                  // Distance to stop, as a share of the entry-to-stop span —
                  // how much of the planned risk has already been used up.
                  const risk = Math.abs(p.entry_price - p.stoploss)
                  const toStop = risk ? Math.abs(p.last_price - p.stoploss) / risk : 0
                  return (
                    <tr key={p.symbol}>
                      <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                      <td>
                        <span className={`badge ${p.side === 'buy' ? 'ok' : 'warn'}`}>
                          {p.side === 'buy' ? 'LONG' : 'SHORT'}
                        </span>
                      </td>
                      <td className="num">{p.quantity}</td>
                      <td className="num">{formatNumber(p.entry_price)}</td>
                      <td className="num">{formatNumber(p.last_price)}</td>
                      <td className="num neg" title={`${(toStop * 100).toFixed(0)}% of risk buffer left`}>
                        {formatNumber(p.stoploss)}
                      </td>
                      <td className="num pos">{formatNumber(p.target)}</td>
                      <td className={`num ${up ? 'pos' : 'neg'}`}>
                        {formatCurrency(p.unrealized_pnl)}
                      </td>
                      <td className={`num ${up ? 'pos' : 'neg'}`}>
                        {p.unrealized_pnl_pct >= 0 ? '+' : ''}
                        {formatNumber(p.unrealized_pnl_pct)}%
                      </td>
                      <td>
                        <button
                          onClick={() => onClose(p.symbol)}
                          disabled={busy === p.symbol}
                          style={{ padding: '4px 10px', fontSize: 12 }}
                        >
                          {busy === p.symbol ? '…' : 'Close'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
