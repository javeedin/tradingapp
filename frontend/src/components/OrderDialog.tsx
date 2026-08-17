import { useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { Analysis } from '../types'

export interface OrderDraft {
  symbol: string
  side: string
  product: string
  productLabel: string
  quantity: number
}

interface Props {
  draft: OrderDraft
  mode: string
  onCancel: () => void
  onConfirmed: () => void
}

/**
 * Order confirmation dialog.
 *
 * Deliberately a two-step flow: clicking "Place order" fetches the plan and
 * shows it here, and only the button in this dialog sends the order. A real
 * order deserves to be reviewed against concrete numbers — entry, stop, target,
 * rupees at risk — rather than a browser confirm() that says nothing.
 *
 * It also surfaces the engine's own verdict, so taking a trade the strategy
 * would not take is a visible, deliberate choice rather than an accident.
 */
export default function OrderDialog({ draft, mode, onCancel, onConfirmed }: Props) {
  const [plan, setPlan] = useState<Analysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [placing, setPlacing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .analyse(draft.symbol)
      .then((result) => !cancelled && setPlan(result))
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [draft.symbol])

  // Close on Escape — a dialog that can send a real order should always be
  // dismissible without hunting for the button.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !placing) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, placing])

  const confirm = async () => {
    setPlacing(true)
    setError(null)
    try {
      await api.placeOrder({
        symbol: draft.symbol,
        side: draft.side,
        product: draft.product,
        quantity: draft.quantity,
      })
      onConfirmed()
    } catch (err) {
      setError((err as Error).message)
      setPlacing(false)
    }
  }

  const isBuy = draft.side.toLowerCase() === 'buy'
  const isLive = mode === 'live'

  // Quantity resolves server-side when left blank; show what the plan suggests.
  const quantity = draft.quantity || plan?.plan.quantity || 0
  const entry = plan?.plan.entry ?? 0
  const notional = entry * quantity
  const riskPerShare = plan ? Math.abs(entry - plan.plan.stoploss) : 0
  const totalRisk = riskPerShare * quantity

  // Is this order fighting the engine's own read? A HOLD is not opposition —
  // it just means no signal — so only an opposite verdict counts.
  const against = plan && plan.action !== 'hold' && plan.action !== draft.side.toLowerCase()

  return (
    <div className="modal-backdrop" onClick={() => !placing && onCancel()}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className={`badge ${isBuy ? 'ok' : 'live'}`}>{draft.side.toUpperCase()}</span>
          <span>{draft.symbol}</span>
          <span className="dim" style={{ fontWeight: 400, fontSize: 12 }}>
            {draft.productLabel}
          </span>
          <div className="spacer" />
          <span className={`badge ${isLive ? 'live' : 'paper'}`}>
            {isLive ? 'LIVE — REAL MONEY' : 'PAPER'}
          </span>
        </div>

        <div className="modal-body">
          {loading && <div className="empty">Building the plan…</div>}

          {error && <div className="notice error">{error}</div>}

          {plan && (
            <>
              {isLive && (
                <div className="notice error">
                  <strong>This will place a real order</strong> against your ICICI Direct
                  account with real money.
                </div>
              )}

              {against && (
                <div className="notice warn">
                  <strong>Against the signal.</strong> The engine currently reads{' '}
                  {plan.action.toUpperCase()} on {draft.symbol}, but this order is a{' '}
                  {draft.side.toUpperCase()}.
                </div>
              )}

              {plan.action === 'hold' && (
                <div className="notice info">
                  The engine would <strong>not</strong> open a position here — score{' '}
                  {plan.score >= 0 ? '+' : ''}
                  {formatNumber(plan.score, 3)} against a ±
                  {formatNumber(plan.entry_threshold, 2)} threshold ({plan.conviction}).
                </div>
              )}

              <div className="order-levels">
                <div className="order-level">
                  <div className="order-level-label">{isBuy ? 'Buy at' : 'Sell at'}</div>
                  <div className="order-level-value">{formatNumber(entry)}</div>
                  <div className="order-level-sub">market</div>
                </div>
                <div className="order-level">
                  <div className="order-level-label">Stoploss</div>
                  <div className="order-level-value neg">
                    {formatNumber(plan.plan.stoploss)}
                  </div>
                  <div className="order-level-sub">
                    −{formatNumber(plan.plan.stop_distance_pct)}%
                  </div>
                </div>
                <div className="order-level">
                  <div className="order-level-label">Target</div>
                  <div className="order-level-value pos">{formatNumber(plan.plan.target)}</div>
                  <div className="order-level-sub">
                    +{formatNumber(plan.plan.target_distance_pct)}%
                  </div>
                </div>
              </div>

              <dl className="kv">
                <dt>Quantity</dt>
                <dd>
                  {quantity}
                  {draft.quantity === 0 && (
                    <span className="dim" style={{ fontWeight: 400 }}>
                      {' '}
                      (risk-sized)
                    </span>
                  )}
                </dd>

                <dt>Order value</dt>
                <dd>{formatCurrency(notional)}</dd>

                <dt>Risk if stopped out</dt>
                <dd className="neg">
                  {formatCurrency(totalRisk)}
                  <span className="dim" style={{ fontWeight: 400 }}>
                    {' '}
                    ({formatNumber(riskPerShare)}/share)
                  </span>
                </dd>

                <dt>Reward at target</dt>
                <dd className="pos">
                  {formatCurrency(Math.abs(plan.plan.target - entry) * quantity)}
                </dd>

                <dt>Risk : reward</dt>
                <dd>1 : {formatNumber(plan.plan.risk_reward, 2)}</dd>

                <dt>Engine verdict</dt>
                <dd>
                  {plan.action.toUpperCase()}{' '}
                  <span className="dim" style={{ fontWeight: 400 }}>
                    ({plan.conviction})
                  </span>
                </dd>
              </dl>

              {plan.plan.was_capped && (
                <div className="notice warn" style={{ marginBottom: 0, marginTop: 12 }}>
                  {plan.plan.sizing_note}
                </div>
              )}

              {plan.reasons.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div className="stat-label" style={{ marginBottom: 6 }}>
                    Engine reasoning
                  </div>
                  <div className="reasons">{plan.reasons.slice(0, 4).join(' · ')}</div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="modal-foot">
          <button onClick={onCancel} disabled={placing}>
            Cancel
          </button>
          <button
            className={isLive ? 'danger' : 'primary'}
            onClick={confirm}
            disabled={placing || loading || !plan || quantity < 1}
          >
            {placing
              ? 'Placing…'
              : isLive
                ? `Place REAL ${draft.side.toUpperCase()} order`
                : `Confirm ${draft.side.toUpperCase()} (paper)`}
          </button>
        </div>
      </div>
    </div>
  )
}
