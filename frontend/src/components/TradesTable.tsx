import { formatCurrency, formatNumber, formatTime } from '../api'
import type { Trade } from '../types'

const REASON_TONE: Record<string, string> = {
  target: 'ok',
  stoploss: 'live',
  signal: 'off',
  intraday_squareoff: 'warn',
  kill_switch: 'live',
  daily_loss_limit: 'live',
}

export default function TradesTable({
  trades,
  title = 'Trade History',
  onRefresh,
  loading,
}: {
  trades: Trade[]
  title?: string
  onRefresh?: () => void
  loading?: boolean
}) {
  const netPnl = trades.reduce((sum, t) => sum + t.net_pnl, 0)
  const wins = trades.filter((t) => t.net_pnl > 0).length

  return (
    <div className="panel">
      <div className="panel-head">
        <span>{title}</span>
        <span className="badge off">{trades.length}</span>
        {trades.length > 0 && (
          <>
            <span className={`badge ${netPnl >= 0 ? 'ok' : 'live'}`}>
              {formatCurrency(netPnl)}
            </span>
            <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
              {wins}W / {trades.length - wins}L
            </span>
          </>
        )}
        {onRefresh && (
          <>
            <div className="spacer" />
            <button onClick={onRefresh} disabled={loading} style={{ padding: '4px 10px' }}>
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
          </>
        )}
      </div>
      <div className="panel-body flush">
        {trades.length === 0 ? (
          <div className="empty">No completed trades</div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Exit</th>
                  <th className="num">Net P&L</th>
                  <th className="num">Return</th>
                  <th>Exit</th>
                  <th className="num">Held</th>
                  <th>Closed</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => {
                  const win = t.net_pnl >= 0
                  return (
                    <tr key={`${t.symbol}-${t.exit_time}-${i}`}>
                      <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                      <td>
                        <span className={`badge ${t.side === 'buy' ? 'ok' : 'warn'}`}>
                          {t.side === 'buy' ? 'LONG' : 'SHORT'}
                        </span>
                      </td>
                      <td className="num">{t.quantity}</td>
                      <td className="num">{formatNumber(t.entry_price)}</td>
                      <td className="num">{formatNumber(t.exit_price)}</td>
                      <td className={`num ${win ? 'pos' : 'neg'}`}>
                        {formatCurrency(t.net_pnl)}
                      </td>
                      <td className={`num ${win ? 'pos' : 'neg'}`}>
                        {t.return_pct >= 0 ? '+' : ''}
                        {formatNumber(t.return_pct)}%
                      </td>
                      <td>
                        <span className={`badge ${REASON_TONE[t.exit_reason] ?? 'off'}`}>
                          {t.exit_reason.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="num dim">{Math.round(t.holding_minutes)}m</td>
                      <td className="dim" style={{ fontSize: 12 }}>
                        {formatTime(t.exit_time)}
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
