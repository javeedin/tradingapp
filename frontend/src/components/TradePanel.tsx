import { useCallback, useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import OrderDialog, { type OrderDraft } from './OrderDialog'
import type {
  BrokerPosition,
  BrokerPositionsResponse,
  OrderProductsResponse,
} from '../types'

const STATUS_LABEL: Record<string, { text: string; tone: string }> = {
  stop_hit: { text: 'STOP HIT', tone: 'live' },
  target_hit: { text: 'TARGET HIT', tone: 'ok' },
  approaching_stop: { text: 'near stop', tone: 'warn' },
  approaching_target: { text: 'near target', tone: 'ok' },
  open: { text: 'open', tone: 'off' },
}

/**
 * Where price sits between its stop (left) and target (right).
 *
 * One bar communicates "how is this trade doing against its own plan" faster
 * than three numbers do.
 */
function ProgressBar({ pct }: { pct: number }) {
  const colour = pct >= 70 ? 'var(--green)' : pct <= 30 ? 'var(--red)' : 'var(--amber)'
  return (
    <div className="score-bar" style={{ marginBottom: 0, minWidth: 70 }}>
      <div
        className="fill"
        style={{ left: 0, width: `${Math.max(2, pct)}%`, background: colour }}
      />
    </div>
  )
}

function OrderForm({
  onPlaced,
  universe,
  mode,
}: {
  onPlaced: () => void
  universe: string[]
  mode: string
}) {
  const [products, setProducts] = useState<OrderProductsResponse | null>(null)
  const [symbol, setSymbol] = useState('')
  const [side, setSide] = useState('buy')
  const [product, setProduct] = useState('cash')
  const [quantity, setQuantity] = useState('')
  const [draft, setDraft] = useState<OrderDraft | null>(null)
  const [placed, setPlaced] = useState<string | null>(null)

  useEffect(() => {
    api.orderProducts().then(setProducts).catch(() => {})
  }, [])

  const review = () => {
    const code = symbol.trim().toUpperCase()
    if (!code) return

    const label =
      products?.placeable.find((p) => p.value === product)?.label ?? product
    setDraft({
      symbol: code,
      side,
      product,
      productLabel: label,
      quantity: quantity.trim() ? Number(quantity) : 0,
    })
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <span>Place an Order</span>
        <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
          stoploss and target attached automatically
        </span>
      </div>
      <div className="panel-body">
        <div className="row">
          <input
            style={{ flex: 1, minWidth: 160 }}
            placeholder="Stock code, e.g. RELIND"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === 'Enter' && review()}
          />
          <select value={side} onChange={(e) => setSide(e.target.value)}>
            <option value="buy">BUY</option>
            <option value="sell">SELL</option>
          </select>
          <select value={product} onChange={(e) => setProduct(e.target.value)}>
            {(products?.placeable ?? [{ value: 'cash', label: 'Delivery' }]).map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          <input
            style={{ width: 130 }}
            placeholder="Qty (auto)"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value.replace(/\D/g, ''))}
          />
          <button className="primary" onClick={review} disabled={!symbol.trim()}>
            Place order
          </button>
        </div>

        {universe.length > 0 && (
          <div className="row" style={{ marginTop: 10, gap: 6 }}>
            <span className="dim" style={{ fontSize: 11 }}>
              Quick pick:
            </span>
            {universe.map((s) => (
              <button
                key={s}
                onClick={() => setSymbol(s)}
                style={{ padding: '3px 9px', fontSize: 12 }}
              >
                {s}
              </button>
            ))}
          </div>
        )}

        <div className="notice info" style={{ marginTop: 12, marginBottom: 0 }}>
          Leave quantity blank to let the risk rules size it. Products ICICI blocks via
          API —{' '}
          {(products?.not_placeable ?? []).map((p) => p.label).join(', ') ||
            'Margin, MTF'}{' '}
          — are not offered: those must be traded in ICICI Direct, though they are
          monitored below.
        </div>

        {placed && (
          <div className="notice success" style={{ marginTop: 12, marginBottom: 0 }}>
            {placed}
          </div>
        )}
      </div>

      {draft && (
        <OrderDialog
          draft={draft}
          mode={mode}
          onCancel={() => setDraft(null)}
          onConfirmed={() => {
            setPlaced(
              `${draft.side.toUpperCase()} order placed for ${draft.symbol}. ` +
                'Stoploss and target are attached.',
            )
            setDraft(null)
            setSymbol('')
            setQuantity('')
            onPlaced()
          }}
        />
      )}
    </div>
  )
}

function PositionRow({ position }: { position: BrokerPosition }) {
  const up = position.unrealised_pnl >= 0
  const status = STATUS_LABEL[position.status ?? 'open'] ?? STATUS_LABEL.open

  return (
    <tr>
      <td style={{ fontWeight: 600 }}>{position.symbol}</td>
      <td>
        <span className={`badge ${position.side === 'buy' ? 'ok' : 'warn'}`}>
          {position.side === 'buy' ? 'LONG' : 'SHORT'}
        </span>
      </td>
      <td>
        <span
          className={`badge ${position.exitable_via_api ? 'off' : 'warn'}`}
          title={position.note}
        >
          {position.product_label}
        </span>
      </td>
      <td className="num">{position.quantity}</td>
      <td className="num">{formatNumber(position.entry_price)}</td>
      <td className="num">{formatNumber(position.last_price)}</td>
      <td className="num neg">
        {position.levels_available ? formatNumber(position.stoploss!) : '—'}
      </td>
      <td className="num pos">
        {position.levels_available ? formatNumber(position.target!) : '—'}
      </td>
      <td style={{ minWidth: 80 }}>
        {position.levels_available ? <ProgressBar pct={position.progress_pct ?? 0} /> : '—'}
      </td>
      <td>
        <span className={`badge ${status.tone}`}>{status.text}</span>
      </td>
      <td className={`num ${up ? 'pos' : 'neg'}`}>{formatCurrency(position.unrealised_pnl)}</td>
      <td className={`num ${up ? 'pos' : 'neg'}`}>
        {position.unrealised_pct >= 0 ? '+' : ''}
        {formatNumber(position.unrealised_pct)}%
      </td>
    </tr>
  )
}

export default function TradePanel({
  universe,
  mode,
}: {
  universe: string[]
  mode: string
}) {
  const [data, setData] = useState<BrokerPositionsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.brokerPositions())
      setError(null)
    } catch (err) {
      setError((err as Error).message)
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    // Broker positions change slowly; the ticker already drives the live prices.
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [load])

  const alerts = data?.alerts ?? []
  const needsAttention = alerts.filter(
    (a) => a.status === 'stop_hit' || a.status === 'target_hit',
  )

  return (
    <div className="grid" style={{ gap: 16 }}>
      <OrderForm onPlaced={load} universe={universe} mode={mode} />

      {needsAttention.length > 0 && (
        <div className="notice error" style={{ marginBottom: 0 }}>
          <strong>Level reached.</strong>{' '}
          {needsAttention
            .map(
              (a) =>
                `${a.symbol} ${a.status === 'stop_hit' ? 'hit its stop' : 'hit its target'} at ${formatNumber(a.last_price)}` +
                (a.exitable_via_api ? '' : ' — exit in ICICI Direct'),
            )
            .join(' · ')}
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <span>Broker Positions</span>
          <span className="badge off">{data?.count ?? 0}</span>
          <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
            everything held at ICICI Direct, including positions bought by hand
          </span>
          <div className="spacer" />
          <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
            {loading ? 'refreshing…' : 'every 15s'}
          </span>
          <button onClick={load} disabled={loading} style={{ padding: '4px 10px' }}>
            Refresh
          </button>
        </div>
        <div className="panel-body flush">
          {error ? (
            <div className="notice error" style={{ margin: 14 }}>
              {error}
            </div>
          ) : !data || data.positions.length === 0 ? (
            <div className="empty">
              No positions at the broker. Anything you buy in ICICI Direct — MTF
              included — appears here with its stop and target computed.
            </div>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Product</th>
                    <th className="num">Qty</th>
                    <th className="num">Entry</th>
                    <th className="num">Last</th>
                    <th className="num">Stop</th>
                    <th className="num">Target</th>
                    <th>Stop → Target</th>
                    <th>Status</th>
                    <th className="num">P&L</th>
                    <th className="num">%</th>
                  </tr>
                </thead>
                <tbody>
                  {data.positions.map((p) => (
                    <PositionRow key={`${p.symbol}-${p.product}`} position={p} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
