import { useEffect, useState } from 'react'
import { api, formatCurrency, formatNumber } from '../api'
import type { OptionOrderRequest, OptionPlan } from '../types'

export interface OptionDraft {
  underlying: string
  expiry: string
  strike: number
  right: 'call' | 'put'
  side: 'buy' | 'sell'
  premium: number
  lotSize: number
}

interface Props {
  draft: OptionDraft
  mode: string
  onCancel: () => void
  onPlaced: () => void
}

// Premium-based exit levels. Wide by equity standards because option premiums
// routinely swing 20% intraday on a move the underlying would call ordinary.
const STOP_CHOICES = [20, 30, 40, 50]
const TARGET_CHOICES = [40, 60, 80, 120]

/**
 * Option order dialog.
 *
 * Same two-step shape as the equity dialog — review concrete numbers, then send
 * — but the numbers are different in kind. Lots rather than shares, premium
 * rather than share price, and margin rather than notional when writing. The
 * plan's warnings are shown before the levels because for options they are
 * usually the most important thing on screen.
 */
export default function OptionOrderDialog({ draft, mode, onCancel, onPlaced }: Props) {
  const [lots, setLots] = useState(1)
  const [stopPct, setStopPct] = useState(40)
  const [targetPct, setTargetPct] = useState(80)
  const [plan, setPlan] = useState<OptionPlan | null>(null)
  const [loading, setLoading] = useState(true)
  const [placing, setPlacing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const payload: OptionOrderRequest = {
    underlying: draft.underlying,
    expiry: draft.expiry,
    strike: draft.strike,
    right: draft.right,
    side: draft.side,
    lots,
    lot_size: draft.lotSize,
    premium: draft.premium,
    stop_pct: stopPct,
    target_pct: targetPct,
  }

  // Repriced on every change, so what is on screen always matches what would be
  // sent — the dialog is the only place these numbers are reviewed.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .previewOptionOrder(payload)
      .then((result) => {
        if (cancelled) return
        setPlan(result)
        setError(null)
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lots, stopPct, targetPct, draft.strike, draft.right, draft.side, draft.premium])

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
      await api.placeOptionOrder(payload)
      onPlaced()
    } catch (err) {
      setError((err as Error).message)
      setPlacing(false)
    }
  }

  const isBuy = draft.side === 'buy'
  const isLive = mode === 'live'
  const writing = !isBuy
  const blocked = plan ? !plan.affordable : false

  return (
    <div className="modal-backdrop" onClick={() => !placing && onCancel()}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className={`badge ${isBuy ? 'ok' : 'live'}`}>
            {isBuy ? 'BUY' : 'WRITE'}
          </span>
          <span>
            {draft.underlying} {formatNumber(draft.strike, 0)}{' '}
            {draft.right === 'call' ? 'CE' : 'PE'}
          </span>
          <span className="dim" style={{ fontWeight: 400, fontSize: 12 }}>
            {new Date(draft.expiry).toLocaleDateString('en-IN', {
              day: '2-digit',
              month: 'short',
            })}
          </span>
          <div className="spacer" />
          <span className={`badge ${isLive ? 'live' : 'paper'}`}>
            {isLive ? 'LIVE — REAL MONEY' : 'PAPER'}
          </span>
        </div>

        <div className="modal-body">
          {error && <div className="notice error">{error}</div>}

          {isLive && (
            <div className="notice error">
              <strong>This will place a real F&amp;O order</strong> against your ICICI
              Direct account.
            </div>
          )}

          {plan?.warnings.map((warning) => (
            <div className="notice warn" key={warning}>
              {warning}
            </div>
          ))}

          <div className="field-grid">
            <label className="field">
              <span className="field-label">Lots</span>
              <input
                type="number"
                min={1}
                value={lots}
                onChange={(e) => setLots(Math.max(1, Number(e.target.value) || 1))}
              />
              <span className="field-hint">
                {draft.lotSize} per lot ·{' '}
                <strong>{plan?.quantity ?? lots * draft.lotSize}</strong> units
              </span>
            </label>

            <label className="field">
              <span className="field-label">Stop</span>
              <select value={stopPct} onChange={(e) => setStopPct(Number(e.target.value))}>
                {STOP_CHOICES.map((pct) => (
                  <option key={pct} value={pct}>
                    {pct}% of premium
                  </option>
                ))}
              </select>
              <span className="field-hint">
                {writing ? 'premium rising' : 'premium falling'} against you
              </span>
            </label>

            <label className="field">
              <span className="field-label">Target</span>
              <select
                value={targetPct}
                onChange={(e) => setTargetPct(Number(e.target.value))}
              >
                {TARGET_CHOICES.map((pct) => (
                  <option key={pct} value={pct}>
                    {pct}% of premium
                  </option>
                ))}
              </select>
              <span className="field-hint">
                R:R 1:{formatNumber(plan?.risk_reward ?? 0, 2)}
              </span>
            </label>
          </div>

          {loading && !plan && <div className="empty">Pricing the order…</div>}

          {plan && (
            <>
              <div className="order-levels">
                <div className="order-level">
                  <div className="order-level-label">
                    {isBuy ? 'Pay premium' : 'Collect premium'}
                  </div>
                  <div className="order-level-value">{formatNumber(plan.premium)}</div>
                  <div className="order-level-sub">per unit</div>
                </div>
                <div className="order-level">
                  <div className="order-level-label">Stoploss</div>
                  <div className="order-level-value neg">
                    {formatNumber(plan.stoploss)}
                  </div>
                  <div className="order-level-sub">{plan.stop_pct}% move</div>
                </div>
                <div className="order-level">
                  <div className="order-level-label">Target</div>
                  <div className="order-level-value pos">{formatNumber(plan.target)}</div>
                  <div className="order-level-sub">{plan.target_pct}% move</div>
                </div>
              </div>

              {blocked && (
                <div className="notice error">
                  <strong>Not enough funds.</strong> This needs{' '}
                  {formatCurrency(plan.required)} but only{' '}
                  {formatCurrency(plan.available)} is available — short by{' '}
                  {formatCurrency(plan.shortfall)}. The largest size that fits is{' '}
                  <strong>{plan.max_affordable_lots}</strong> lot(s).
                </div>
              )}

              <dl className="kv">
                <dt>Premium value</dt>
                <dd>{formatCurrency(plan.premium_value)}</dd>

                <dt>{writing ? 'Margin blocked (estimate)' : 'Cost'}</dt>
                <dd>{formatCurrency(plan.margin_blocked)}</dd>

                <dt>Estimated charges</dt>
                <dd className="dim">{formatCurrency(plan.estimated_costs)}</dd>

                <dt>Funds needed</dt>
                <dd>{formatCurrency(plan.required)}</dd>

                <dt>Funds available</dt>
                <dd className={blocked ? 'neg' : 'pos'}>
                  {formatCurrency(plan.available)}
                </dd>

                <dt>Risk if stopped out</dt>
                <dd className="neg">{formatCurrency(plan.risk_amount)}</dd>

                <dt>Reward at target</dt>
                <dd className="pos">{formatCurrency(plan.reward_amount)}</dd>

                <dt>Days to expiry</dt>
                <dd>{plan.days_to_expiry}</dd>
              </dl>

              {writing && (
                <div className="notice info" style={{ marginBottom: 0, marginTop: 12 }}>
                  Margin is an estimate at 15% of the strike value. The exchange sets
                  the real figure from its SPAN file and raises it when volatility
                  rises, so a write that fits today may be short tomorrow.
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
            className={isLive || writing ? 'danger' : 'primary'}
            onClick={confirm}
            disabled={placing || loading || !plan || blocked}
          >
            {placing
              ? 'Placing…'
              : `${isBuy ? 'Buy' : 'Write'} ${lots} lot${lots === 1 ? '' : 's'}${
                  isLive ? ' (REAL)' : ' (paper)'
                }`}
          </button>
        </div>
      </div>
    </div>
  )
}
