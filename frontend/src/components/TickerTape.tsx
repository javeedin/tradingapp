import { useEffect, useRef, useState } from 'react'
import { formatNumber } from '../api'
import type { Quote, TickerStatus } from '../types'

/**
 * Live price strip.
 *
 * Flashes a cell green or red for a moment when its price moves, because a
 * number that changes silently is easy to miss — the flash is what makes
 * "the price is moving" visible at a glance.
 */
function QuoteCell({ quote }: { quote: Quote }) {
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)
  const previous = useRef(quote.price)

  useEffect(() => {
    if (quote.price === previous.current) return
    setFlash(quote.price > previous.current ? 'up' : 'down')
    previous.current = quote.price

    const timer = setTimeout(() => setFlash(null), 550)
    return () => clearTimeout(timer)
  }, [quote.price])

  const up = quote.change >= 0
  const flashBg =
    flash === 'up'
      ? 'var(--green-bg)'
      : flash === 'down'
        ? 'var(--red-bg)'
        : 'transparent'

  return (
    <div
      className="tick"
      style={{ background: flashBg, transition: 'background 0.45s ease-out' }}
      title={`Updated ${new Date(quote.updated_at).toLocaleTimeString()} via ${quote.source}`}
    >
      <span className="tick-symbol">{quote.symbol}</span>
      <span className="tick-price">{formatNumber(quote.price)}</span>
      <span className={`tick-change ${up ? 'pos' : 'neg'}`}>
        {up ? '▲' : '▼'} {formatNumber(Math.abs(quote.change_pct))}%
      </span>
    </div>
  )
}

interface Props {
  quotes: Quote[]
  status: TickerStatus | null
  connected: boolean
}

export default function TickerTape({ quotes, status, connected }: Props) {
  if (!connected) return null

  const feed = status?.streaming ? 'stream' : status?.polling ? 'polling' : 'off'
  const feedTone = status?.streaming ? 'ok' : status?.polling ? 'warn' : 'off'

  return (
    <div className="panel ticker-tape">
      <div className="tick-rail">
        {quotes.length === 0 ? (
          <span className="dim" style={{ fontSize: 12, padding: '4px 2px' }}>
            Waiting for the first prices…
          </span>
        ) : (
          quotes.map((q) => <QuoteCell key={q.symbol} quote={q} />)
        )}
        <div className="spacer" />
        <span className={`badge ${feedTone}`} title={status?.last_error || undefined}>
          {feed}
          {feed === 'polling' && status?.poll_interval_seconds
            ? ` ${status.poll_interval_seconds}s`
            : ''}
        </span>
      </div>
    </div>
  )
}
