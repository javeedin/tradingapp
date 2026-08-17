import { useCallback, useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber, formatTime } from '../api'
import type { Funds, OrderHistoryResponse, OrderRecord } from '../types'

const STATUS_TONE: Record<string, string> = {
  filled: 'ok',
  rejected: 'live',
  cancelled: 'warn',
  pending: 'off',
  partial: 'warn',
}

function FundsCard({ funds, brokerFunds, mode, error }: {
  funds: Funds
  brokerFunds?: Funds
  mode: string
  error?: string
}) {
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Funds</span>
        <span className={`badge ${mode === 'live' ? 'live' : 'paper'}`}>{mode}</span>
        {funds.source === 'unavailable' && (
          <span className="badge warn">could not read</span>
        )}
      </div>

      <div className="stats">
        <div className="stat">
          <div className="stat-label">Available to trade</div>
          <div className="stat-value">{formatCurrency(funds.available)}</div>
          <div className="stat-sub">
            {mode === 'paper' ? 'simulated balance' : 'broker margin'}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Bank balance</div>
          <div className="stat-value">{formatCurrency(funds.bank_balance)}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Allocated</div>
          <div className="stat-value">{formatCurrency(funds.total_allocated)}</div>
          <div className="stat-sub">
            equity {formatNumber(funds.allocated_equity, 0)} · F&O{' '}
            {formatNumber(funds.allocated_fno, 0)}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">Blocked by trades</div>
          <div className="stat-value">{formatCurrency(funds.blocked)}</div>
        </div>
      </div>

      {/* In paper mode the real balance is context, never the constraint. */}
      {mode === 'paper' && brokerFunds && (
        <div className="panel-body">
          <div className="notice info" style={{ marginBottom: 0 }}>
            Your real ICICI Direct balance is{' '}
            <strong>{formatCurrency(brokerFunds.available)}</strong> available of{' '}
            {formatCurrency(brokerFunds.bank_balance)}. Paper orders are constrained by
            the simulated balance above, not this.
          </div>
        </div>
      )}

      {mode === 'paper' && error && (
        <div className="panel-body">
          <div className="notice warn" style={{ marginBottom: 0 }}>
            Could not read the real balance: {error}
          </div>
        </div>
      )}
    </div>
  )
}

function OrderRow({ order }: { order: OrderRecord }) {
  const tone = STATUS_TONE[order.status] ?? 'off'
  const filled = order.status === 'filled'

  return (
    <tr>
      <td className="dim" style={{ fontSize: 12 }}>
        {formatTime(order.placed_at)}
      </td>
      <td style={{ fontWeight: 600 }}>{order.symbol}</td>
      <td>
        <span className={`badge ${order.side === 'buy' ? 'ok' : 'warn'}`}>
          {order.side.toUpperCase()}
        </span>
      </td>
      <td className="dim">{order.product}</td>
      <td className="num">{order.quantity}</td>
      <td className="num">{formatNumber(order.price)}</td>
      <td className="num">
        {filled && order.filled_price ? formatNumber(order.filled_price) : '—'}
      </td>
      <td className="num neg">{order.stoploss ? formatNumber(order.stoploss) : '—'}</td>
      <td className="num pos">{order.target ? formatNumber(order.target) : '—'}</td>
      <td>
        <span className={`badge ${tone}`}>{order.status}</span>
      </td>
      <td className="dim">{order.origin}</td>
      {/* The message is where a rejection explains itself, so it is not truncated
          away — it wraps instead. */}
      <td style={{ whiteSpace: 'normal', minWidth: 240, fontSize: 12 }} className="dim">
        {order.message || '—'}
      </td>
    </tr>
  )
}

export default function OrdersPanel() {
  const [history, setHistory] = useState<OrderHistoryResponse | null>(null)
  const [funds, setFunds] = useState<Awaited<ReturnType<typeof api.funds>> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('all')
  // Equity and options orders share one table, but they are rarely reviewed
  // together — an option row's price is a premium and its quantity is units.
  const [product, setProduct] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [orders, money] = await Promise.all([api.orderHistory(), api.funds()])
      setHistory(orders)
      setFunds(money)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [load])

  const stats = history?.stats
  const all = history?.orders ?? []
  const rows = all.filter(
    (o) =>
      (filter === 'all' || o.status === filter) &&
      (product === 'all' ||
        (product === 'options' ? o.product === 'options' : o.product !== 'options')),
  )
  const optionCount = all.filter((o) => o.product === 'options').length

  return (
    <div className="grid" style={{ gap: 16 }}>
      {funds && (
        <FundsCard
          funds={funds.funds}
          brokerFunds={funds.broker_funds}
          mode={funds.mode}
          error={funds.broker_funds_error}
        />
      )}

      {error && <div className="notice error">{error}</div>}

      <div className="panel">
        <div className="panel-head">
          <span>Order History</span>
          {stats && (
            <>
              <span className="badge off">{stats.total} total</span>
              {stats.filled > 0 && <span className="badge ok">{stats.filled} filled</span>}
              {stats.rejected > 0 && (
                <span className="badge live">{stats.rejected} rejected</span>
              )}
              {stats.pending > 0 && (
                <span className="badge warn">{stats.pending} pending</span>
              )}
            </>
          )}
          {optionCount > 0 && (
            <span className="badge off">{optionCount} options</span>
          )}
          <div className="spacer" />
          <select value={product} onChange={(e) => setProduct(e.target.value)}>
            <option value="all">Equity &amp; options</option>
            <option value="equity">Equity only</option>
            <option value="options">Options only</option>
          </select>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="filled">Filled</option>
            <option value="rejected">Rejected</option>
            <option value="pending">Pending</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
            {loading ? 'refreshing…' : 'every 10s'}
          </span>
          <button onClick={load} disabled={loading} style={{ padding: '4px 10px' }}>
            Refresh
          </button>
        </div>
        <div className="panel-body flush">
          {rows.length === 0 ? (
            <div className="empty">
              {all.length
                ? `No matching orders.`
                : 'No orders yet. Anything placed from the Trade or Options tab appears here — including rejections and why they failed.'}
            </div>
          ) : (
            <div className="table-scroll" style={{ maxHeight: 560 }}>
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Product</th>
                    <th className="num">Qty</th>
                    <th className="num">Price</th>
                    <th className="num">Filled</th>
                    <th className="num">Stop</th>
                    <th className="num">Target</th>
                    <th>Status</th>
                    <th>Origin</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((order, i) => (
                    <OrderRow key={`${order.order_id ?? 'na'}-${i}`} order={order} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Orders placed in the ICICI app or website, which this app never saw. */}
      {history?.broker_orders && history.broker_orders.length > 0 && (
        <div className="panel">
          <div className="panel-head">
            <span>Broker Order Book</span>
            <span className="badge off">{history.broker_orders.length}</span>
            <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
              last 7 days at ICICI Direct, including orders placed elsewhere
            </span>
          </div>
          <div className="panel-body flush">
            <div className="table-scroll" style={{ maxHeight: 320 }}>
              <table>
                <thead>
                  <tr>
                    {Object.keys(history.broker_orders[0]).slice(0, 9).map((key) => (
                      <th key={key}>{key.replace(/_/g, ' ')}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {history.broker_orders.map((row, i) => (
                    <tr key={i}>
                      {Object.keys(history.broker_orders![0])
                        .slice(0, 9)
                        .map((key) => (
                          <td key={key} className="dim" style={{ fontSize: 12 }}>
                            {String(row[key] ?? '—')}
                          </td>
                        ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {history?.broker_orders_error && (
        <div className="notice warn">
          Could not read the broker order book: {history.broker_orders_error}
        </div>
      )}
    </div>
  )
}
