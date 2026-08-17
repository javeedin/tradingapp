import {
  createChart,
  IChartApi,
  ISeriesApi,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { cssVar, type Theme } from '../theme'
import type { CandleResponse } from '../types'

/** Convert an ISO timestamp to the epoch-seconds Time the chart expects. */
function toTime(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp
}

/**
 * Lightweight Charts rejects duplicate or out-of-order timestamps outright, and
 * intraday bars re-fetched across backfill windows can collide. Dedupe on the
 * way in rather than letting the chart throw.
 */
function dedupe<T extends { time: Time }>(points: T[]): T[] {
  const byTime = new Map<number, T>()
  for (const point of points) {
    byTime.set(point.time as number, point)
  }
  return [...byTime.values()].sort((a, b) => (a.time as number) - (b.time as number))
}

/**
 * Chart colours pulled from the active theme's CSS custom properties.
 *
 * The chart draws to a canvas and cannot inherit CSS, so it has to be told its
 * colours explicitly — reading them back from the stylesheet avoids keeping a
 * duplicate palette in sync by hand.
 */
function chartPalette() {
  return {
    background: cssVar('--panel', '#ffffff'),
    text: cssVar('--text-dim', '#5a6675'),
    border: cssVar('--border', '#dde3ea'),
    grid: cssVar('--border-soft', '#e8edf3'),
    up: cssVar('--green', '#128a4d'),
    down: cssVar('--red', '#d32f36'),
    accent: cssVar('--accent', '#2563eb'),
    amber: cssVar('--amber', '#a86612'),
    faint: cssVar('--text-faint', '#8d98a7'),
  }
}

interface Props {
  symbol: string
  symbols: string[]
  onSymbolChange: (symbol: string) => void
  theme: Theme
}

export default function PriceChart({ symbol, symbols, onSymbolChange, theme }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const emaFastRef = useRef<ISeriesApi<'Line'> | null>(null)
  const emaSlowRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapRef = useRef<ISeriesApi<'Line'> | null>(null)

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [barCount, setBarCount] = useState(0)

  // Create the chart once; series are reused across symbol and theme changes.
  useEffect(() => {
    if (!hostRef.current) return

    const colors = chartPalette()
    const chart = createChart(hostRef.current, {
      layout: {
        background: { color: colors.background },
        textColor: colors.text,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      rightPriceScale: { borderColor: colors.border },
      timeScale: { borderColor: colors.border, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      autoSize: true,
    })

    candleRef.current = chart.addCandlestickSeries({
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    })
    emaFastRef.current = chart.addLineSeries({
      color: colors.accent,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    emaSlowRef.current = chart.addLineSeries({
      color: colors.amber,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    vwapRef.current = chart.addLineSeries({
      color: colors.faint,
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    })

    chartRef.current = chart
    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  // Re-colour in place when the theme flips. Recreating the chart would lose
  // the loaded data and the user's zoom/pan position.
  useEffect(() => {
    if (!chartRef.current) return
    const colors = chartPalette()

    chartRef.current.applyOptions({
      layout: { background: { color: colors.background }, textColor: colors.text },
      grid: {
        vertLines: { color: colors.grid },
        horzLines: { color: colors.grid },
      },
      rightPriceScale: { borderColor: colors.border },
      timeScale: { borderColor: colors.border },
    })
    candleRef.current?.applyOptions({
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
    })
    emaFastRef.current?.applyOptions({ color: colors.accent })
    emaSlowRef.current?.applyOptions({ color: colors.amber })
    vwapRef.current?.applyOptions({ color: colors.faint })
  }, [theme])

  useEffect(() => {
    if (!symbol) return
    let cancelled = false

    setLoading(true)
    setError(null)

    api
      .candles(symbol, 400)
      .then((data: CandleResponse) => {
        if (cancelled || !candleRef.current) return

        const bars = dedupe(
          data.candles.map((c) => ({
            time: toTime(c.time),
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          })),
        )
        candleRef.current.setData(bars)
        setBarCount(bars.length)

        const line = (key: string) =>
          dedupe((data.indicators[key] ?? []).map((p) => ({ time: toTime(p.time), value: p.value })))

        emaFastRef.current?.setData(line('ema_20'))
        emaSlowRef.current?.setData(line('ema_50'))
        vwapRef.current?.setData(line('vwap'))

        chartRef.current?.timeScale().fitContent()
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [symbol])

  return (
    <div className="panel">
      <div className="panel-head">
        <span>Price</span>
        <select value={symbol} onChange={(e) => onSymbolChange(e.target.value)}>
          {symbols.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
          <span style={{ color: 'var(--accent)' }}>—</span> EMA20{' '}
          <span style={{ color: 'var(--amber)' }}>—</span> EMA50{' '}
          <span style={{ color: 'var(--text-faint)' }}>--</span> VWAP
        </span>
        <div className="spacer" />
        <span className="dim" style={{ fontSize: 11, fontWeight: 400 }}>
          {loading ? 'loading…' : `${barCount} bars`}
        </span>
      </div>
      <div className="panel-body flush">
        {error && (
          <div className="notice error" style={{ margin: 14 }}>
            {error}
          </div>
        )}
        <div ref={hostRef} className="chart-host" />
      </div>
    </div>
  )
}
