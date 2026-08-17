import { useEffect, useRef, useState } from 'react'
import { api, formatNumber } from '../api'
import type { ExpiryCandidate, OptionChainResponse, OptionLeg } from '../types'

// Index underlyings people actually trade options on, as Breeze codes.
const UNDERLYINGS = ['NIFTY', 'CNXBAN', 'RELIND', 'TCS', 'INFTEC', 'HDFBAN']

// Strikes to show either side of spot. A full chain is hundreds of rows and the
// far wings are untradable noise.
const STRIKE_WINDOW = 12

function num(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '—' : formatNumber(value, digits)
}

function compact(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (Math.abs(value) >= 1e7) return `${(value / 1e7).toFixed(2)}Cr`
  if (Math.abs(value) >= 1e5) return `${(value / 1e5).toFixed(2)}L`
  if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)}K`
  return String(Math.round(value))
}

function LegCells({ leg, side }: { leg: OptionLeg | null; side: 'call' | 'put' }) {
  // Tint in-the-money legs so the ATM boundary is visible without reading strikes.
  const tint = side === 'call' ? 'rgba(18,138,77,0.05)' : 'rgba(211,47,54,0.05)'

  if (!leg) {
    return (
      <>
        <td className="num dim" style={{ background: tint }}>—</td>
        <td className="num dim" style={{ background: tint }}>—</td>
        <td className="num dim" style={{ background: tint }}>—</td>
      </>
    )
  }

  const changeTone = (leg.change ?? 0) >= 0 ? 'pos' : 'neg'

  return (
    <>
      <td className="num" style={{ background: tint, fontWeight: 600 }}>
        {num(leg.ltp)}
      </td>
      <td className={`num ${changeTone}`} style={{ background: tint }}>
        {leg.change === null ? '—' : `${leg.change >= 0 ? '+' : ''}${formatNumber(leg.change)}%`}
      </td>
      <td className="num dim" style={{ background: tint }}>
        {compact(leg.open_interest)}
      </td>
    </>
  )
}

export default function OptionChain() {
  const [symbol, setSymbol] = useState('NIFTY')
  const [expiries, setExpiries] = useState<ExpiryCandidate[]>([])
  const [expiry, setExpiry] = useState('')
  const [chain, setChain] = useState<OptionChainResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

  // The ATM row, so the chain can open centred on it. A chain that opens at the
  // top of the strike range shows only deep in-the-money calls — technically
  // correct and practically useless, since nobody trades the far wings.
  const atmRowRef = useRef<HTMLTableRowElement>(null)

  // Refetched per underlying: only NIFTY still has weekly contracts, so the
  // candidate list differs by symbol.
  useEffect(() => {
    let cancelled = false
    api
      .expiries(symbol)
      .then((data) => {
        if (cancelled) return
        setExpiries(data.expiries)
        if (data.expiries.length) setExpiry(data.expiries[0].date)
      })
      .catch((err: Error) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [symbol])

  const load = async () => {
    setBusy(true)
    setError(null)
    try {
      setChain(await api.optionChain(symbol, expiry || undefined))
    } catch (err) {
      setError((err as Error).message)
      setChain(null)
    } finally {
      setBusy(false)
    }
  }

  // Nearest strike to spot — the ATM row.
  const spot = chain?.spot ?? null
  const atmStrike =
    spot && chain?.rows.length
      ? chain.rows.reduce(
          (best, row) =>
            Math.abs(row.strike - spot) < Math.abs(best - spot) ? row.strike : best,
          chain.rows[0].strike,
        )
      : null

  let rows = chain?.rows ?? []
  if (!showAll && atmStrike !== null && rows.length > STRIKE_WINDOW * 2) {
    const atmIndex = rows.findIndex((r) => r.strike === atmStrike)
    rows = rows.slice(
      Math.max(0, atmIndex - STRIKE_WINDOW),
      atmIndex + STRIKE_WINDOW + 1,
    )
  }

  // Centre the ATM row once the chain renders. Runs on chain/showAll change
  // rather than on every render so it never fights the user's own scrolling.
  useEffect(() => {
    if (!chain || atmStrike === null) return
    // A frame's delay lets the rows commit before scrolling to one of them.
    const id = requestAnimationFrame(() => {
      atmRowRef.current?.scrollIntoView({ block: 'center', behavior: 'auto' })
    })
    return () => cancelAnimationFrame(id)
  }, [chain, atmStrike, showAll])

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="panel">
        <div className="panel-head">
          <span>Option Chain</span>
        </div>
        <div className="panel-body">
          <div className="row">
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {UNDERLYINGS.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
            <select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
              {expiries.map((e) => (
                <option key={e.date} value={e.date}>
                  {e.label} · {e.days_away}d
                </option>
              ))}
            </select>
            <button className="primary" onClick={load} disabled={busy}>
              {busy ? 'Loading…' : 'Load chain'}
            </button>
            {chain && (
              <>
                <div className="spacer" />
                {chain.spot && (
                  <span className="dim" style={{ fontSize: 12 }}>
                    Spot <strong>{formatNumber(chain.spot)}</strong>
                  </span>
                )}
                <span className="badge off">{chain.count} strikes</span>
                <button onClick={() => setShowAll((v) => !v)}>
                  {showAll ? 'Near ATM only' : 'Show all'}
                </button>
              </>
            )}
          </div>

          <div className="notice info" style={{ marginTop: 12, marginBottom: 0 }}>
            Expiries are <strong>Tuesdays</strong> since NSE moved the F&O expiry day on
            28&nbsp;Aug&nbsp;2025, and <strong>weeklies exist for NIFTY only</strong> —
            every other index is monthly. Holidays shift an expiry earlier, and Breeze
            rejects anything that is not a live contract.
          </div>

          {error && (
            <div className="notice error" style={{ marginTop: 12, marginBottom: 0 }}>
              {error}
            </div>
          )}
        </div>
      </div>

      {chain && (
        <div className="panel">
          <div className="panel-head">
            <span>
              {chain.symbol} · {new Date(chain.expiry).toLocaleDateString('en-IN')}
            </span>
            <div className="spacer" />
            {atmStrike !== null && (
              <span className="badge ok">ATM {formatNumber(atmStrike, 0)}</span>
            )}
            <span className="dim" style={{ fontWeight: 400, fontSize: 11 }}>
              <span className="pos">calls</span> left · <span className="neg">puts</span> right
            </span>
          </div>
          <div className="panel-body flush">
            {rows.length === 0 ? (
              <div className="empty">
                No strikes returned. The expiry may not be a live contract.
              </div>
            ) : (
              <div className="table-scroll" style={{ maxHeight: 620 }}>
                <table>
                  <thead>
                    <tr>
                      <th className="num" colSpan={3} style={{ textAlign: 'center' }}>
                        CALLS
                      </th>
                      <th className="num" style={{ textAlign: 'center' }}>
                        STRIKE
                      </th>
                      <th className="num" colSpan={3} style={{ textAlign: 'center' }}>
                        PUTS
                      </th>
                    </tr>
                    <tr>
                      <th className="num">LTP</th>
                      <th className="num">Chg</th>
                      <th className="num">OI</th>
                      <th className="num" />
                      <th className="num">LTP</th>
                      <th className="num">Chg</th>
                      <th className="num">OI</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const isAtm = row.strike === atmStrike
                      // Label which side of the money each strike sits on, so the
                      // chain reads without mentally comparing every row to spot.
                      const moneyness =
                        spot === null
                          ? ''
                          : row.strike > spot
                            ? 'OTM call'
                            : 'ITM call'
                      return (
                        <tr
                          key={row.strike}
                          ref={isAtm ? atmRowRef : undefined}
                          style={
                            isAtm
                              ? {
                                  outline: '2px solid var(--accent)',
                                  background: 'var(--info-bg)',
                                }
                              : undefined
                          }
                        >
                          <LegCells leg={row.call} side="call" />
                          <td className="num strike-cell">
                            {formatNumber(row.strike, 0)}
                            {isAtm && <span className="strike-atm">ATM</span>}
                            {!isAtm && moneyness && (
                              <span className="strike-moneyness">{moneyness}</span>
                            )}
                          </td>
                          <LegCells leg={row.put} side="put" />
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
