import { useCallback, useEffect, useState } from 'react'
import { api, formatNumber } from '../api'
import type { RuntimeSettings, SettingsUpdate } from '../types'

interface Props {
  onChanged?: () => void
}

interface NumberFieldProps {
  label: string
  hint?: string
  value: number
  step?: number
  min?: number
  max?: number
  suffix?: string
  onCommit: (value: number) => void
}

/**
 * A number input that only saves when the user is done with it.
 *
 * Saving on every keystroke would send a PUT per digit, and half-typed values
 * are real values to the engine — typing "10" through "1" would briefly set the
 * risk per trade to 1%.
 */
function NumberField({
  label,
  hint,
  value,
  step = 1,
  min,
  max,
  suffix,
  onCommit,
}: NumberFieldProps) {
  const [draft, setDraft] = useState(String(value))

  // Follow the server when it changes underneath us, but never while focused.
  useEffect(() => {
    setDraft(String(value))
  }, [value])

  const commit = () => {
    const parsed = Number(draft)
    if (!Number.isFinite(parsed) || parsed === value) {
      setDraft(String(value))
      return
    }
    onCommit(parsed)
  }

  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <span className="field-input">
        <input
          type="number"
          step={step}
          min={min}
          max={max}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        />
        {suffix && <span className="field-suffix">{suffix}</span>}
      </span>
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string
  hint?: string
  checked: boolean
  onChange: (value: boolean) => void
}) {
  return (
    <label className="field field-toggle">
      <span className="switch">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="switch-track" />
      </span>
      <span>
        <span className="field-label">{label}</span>
        {hint && <span className="field-hint">{hint}</span>}
      </span>
    </label>
  )
}

/**
 * Runtime settings.
 *
 * These are applied to the running engine rather than written to .env, because a
 * trading parameter you can only change by editing a file and restarting is one
 * you will not change mid-session — and restarting drops the Breeze session.
 *
 * The exit policy is first and largest deliberately: it is the only setting here
 * that changes whether a losing trade has a floor at all.
 */
export default function SettingsPanel({ onChanged }: Props) {
  const [settings, setSettings] = useState<RuntimeSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [lastChange, setLastChange] = useState<string[]>([])

  const load = useCallback(async () => {
    try {
      setSettings(await api.settings())
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const save = async (update: SettingsUpdate) => {
    setSaving(true)
    try {
      const next = await api.updateSettings(update)
      setSettings(next)
      setLastChange(next.changes ?? [])
      setError(null)
      onChanged?.()
    } catch (err) {
      setError((err as Error).message)
      // The engine rejected it, so the inputs must not keep showing the new
      // value as if it had stuck.
      await load()
    } finally {
      setSaving(false)
    }
  }

  if (!settings) {
    return (
      <div className="panel">
        <div className="panel-body">
          {error ? <div className="notice error">{error}</div> : <div className="empty">Loading settings…</div>}
        </div>
      </div>
    )
  }

  const { risk, averaging, signals, robotic, session } = settings
  const policy = settings.exit_policy.value
  const chosen = settings.exit_policy.options.find((o) => o.value === policy)

  return (
    <div className="grid" style={{ gap: 16 }}>
      {error && <div className="notice error">{error}</div>}

      {lastChange.length > 0 && (
        <div className="notice success">
          Applied: {lastChange.join('; ')}.{' '}
          <span className="dim">Not written to .env — a restart returns to the file.</span>
        </div>
      )}

      {/* ---------------- Exit policy ---------------- */}
      <div className="panel">
        <div className="panel-head">
          <span>When a position goes against you</span>
          <div className="spacer" />
          {chosen && !chosen.has_stoploss && (
            <span className="badge live">no stoploss</span>
          )}
        </div>
        <div className="panel-body">
          <p className="panel-intro">
            This is your call, not the strategy's. All three are supported, and they
            differ enormously in how a losing trade ends.
          </p>

          <div className="policy-choices">
            {settings.exit_policy.options.map((option) => {
              const active = option.value === policy
              return (
                <button
                  key={option.value}
                  className={`policy ${active ? 'active' : ''} ${
                    option.has_stoploss ? '' : 'policy-danger'
                  }`}
                  disabled={saving}
                  onClick={() => save({ exit_policy: option.value })}
                >
                  <span className="policy-head">
                    <span className={`radio ${active ? 'on' : ''}`} />
                    <strong>{option.label}</strong>
                    {!option.has_stoploss && (
                      <span className="badge live">unbounded loss</span>
                    )}
                  </span>
                  <span className="policy-note">{option.note}</span>
                </button>
              )
            })}
          </div>

          {chosen?.averages_down && (
            <div className="field-grid" style={{ marginTop: 14 }}>
              <NumberField
                label="Max adds"
                hint="extra entries per position, beyond the first"
                value={averaging.max_adds}
                min={0}
                max={20}
                onCommit={(v) => save({ max_adds: v })}
              />
              <NumberField
                label="Add trigger"
                suffix="× ATR"
                hint="adverse move from the first entry before adding"
                value={averaging.add_trigger_atr}
                step={0.1}
                onCommit={(v) => save({ add_trigger_atr: v })}
              />
              <NumberField
                label="Max per symbol"
                suffix="%"
                hint="exposure ceiling — caps size, not loss"
                value={averaging.max_symbol_exposure_pct}
                step={1}
                onCommit={(v) => save({ max_symbol_exposure_pct: v })}
              />
            </div>
          )}
        </div>
      </div>

      {/* ---------------- Robotic trading ---------------- */}
      <div className="panel">
        <div className="panel-head">
          <span>Robotic trading</span>
          <div className="spacer" />
          <span className={`badge ${robotic.enabled ? 'live' : 'off'}`}>
            {robotic.enabled ? 'ARMED' : 'off'}
          </span>
        </div>
        <div className="panel-body">
          <p className="panel-intro">
            Each cycle scans the top {robotic.universe_size} liquid NIFTY names, ranks
            them, and opens up to {robotic.max_positions} positions in the strongest —
            capped at two per sector, because the top six by score are frequently six
            banks. Nothing below the entry threshold is ever promoted just for being
            top-ranked.
          </p>

          <Toggle
            label="Enable robotic trading"
            hint={
              robotic.enabled
                ? `Orders will be placed automatically in ${session.mode} mode`
                : 'The engine will only manage positions you open yourself'
            }
            checked={robotic.enabled}
            onChange={(v) => save({ robotic_trading: v })}
          />

          <div className="field-grid" style={{ marginTop: 14 }}>
            <NumberField
              label="Positions to hold"
              hint={`also capped by max open positions (${risk.max_open_positions})`}
              value={robotic.max_positions}
              min={1}
              max={50}
              onCommit={(v) => save({ robotic_max_positions: v })}
            />
            <NumberField
              label="Universe to scan"
              hint="symbols ranked per cycle"
              value={robotic.universe_size}
              min={1}
              max={200}
              onCommit={(v) => save({ robotic_universe_size: v })}
            />
          </div>
        </div>
      </div>

      {/* ---------------- Risk ---------------- */}
      <div className="panel">
        <div className="panel-head">
          <span>Risk</span>
          <div className="spacer" />
          <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
            R:R 1:{formatNumber(risk.atr_target_multiplier / risk.atr_stop_multiplier, 1)}
          </span>
        </div>
        <div className="panel-body">
          <div className="field-grid">
            <NumberField
              label="Risk per trade"
              suffix="%"
              hint="of equity, at the stop — this sizes every position"
              value={risk.risk_per_trade_pct}
              step={0.1}
              onCommit={(v) => save({ risk_per_trade_pct: v })}
            />
            <NumberField
              label="Daily loss limit"
              suffix="%"
              hint="halts new entries for the day when breached"
              value={risk.max_daily_loss_pct}
              step={0.5}
              onCommit={(v) => save({ max_daily_loss_pct: v })}
            />
            <NumberField
              label="Max open positions"
              value={risk.max_open_positions}
              min={1}
              max={50}
              onCommit={(v) => save({ max_open_positions: v })}
            />
            <NumberField
              label="Max position size"
              suffix="%"
              hint="of equity per position — can bind before the risk rule does"
              value={risk.max_position_pct}
              step={1}
              onCommit={(v) => save({ max_position_pct: v })}
            />
            <NumberField
              label="Stop distance"
              suffix="× ATR"
              value={risk.atr_stop_multiplier}
              step={0.1}
              onCommit={(v) => save({ atr_stop_multiplier: v })}
            />
            <NumberField
              label="Target distance"
              suffix="× ATR"
              value={risk.atr_target_multiplier}
              step={0.1}
              onCommit={(v) => save({ atr_target_multiplier: v })}
            />
          </div>

          <div style={{ marginTop: 14 }}>
            <Toggle
              label="Trailing stop"
              hint={`follows price at ${formatNumber(risk.trail_atr_multiplier, 1)}× ATR once in profit`}
              checked={risk.use_trailing_stop}
              onChange={(v) => save({ use_trailing_stop: v })}
            />
          </div>
        </div>
      </div>

      {/* ---------------- Signals ---------------- */}
      <div className="panel">
        <div className="panel-head">
          <span>Signals</span>
        </div>
        <div className="panel-body">
          <div className="field-grid">
            <NumberField
              label="Entry threshold"
              hint="minimum |score| to open — raise it to trade less, and better"
              value={signals.entry_threshold}
              step={0.05}
              min={0}
              max={1}
              onCommit={(v) => save({ entry_threshold: v })}
            />
            <NumberField
              label="Exit threshold"
              hint="score at which an open position is closed on signal"
              value={signals.exit_threshold}
              step={0.05}
              min={-1}
              max={1}
              onCommit={(v) => save({ exit_threshold: v })}
            />
          </div>

          <div style={{ marginTop: 14 }} className="grid" >
            <Toggle
              label="Allow short positions"
              hint="shorts need the MARGIN product, which Breeze cannot place via API — paper only"
              checked={signals.allow_shorts}
              onChange={(v) => save({ allow_shorts: v })}
            />
            <Toggle
              label="Square off intraday"
              hint="closes everything before the cutoff instead of holding overnight"
              checked={session.intraday}
              onChange={(v) => save({ intraday: v })}
            />
          </div>
        </div>
      </div>

      <div className="notice info" style={{ marginBottom: 0 }}>
        {settings.note}
      </div>
    </div>
  )
}
