import {
  CandlestickSeriesPartialOptions,
  createChart,
  IChartApi,
  ISeriesApi,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { CandleResponse } from '../types'

const CANDLE_STYLE: CandlestickSeriesPartialOptions = {
  upColor: '#26a96c',
  downColor: '#e5484d',
  borderUpColor: '#26a96c',
  borderDownColor: '#e5484d',
  wickUpColor: '#26a96c',
  wickDownColor: '#e5484d',
}

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

interface Props {
  symbol: string
  symbols: string[]
  onSymbolChange: (symbol: string) => void
}

export default function PriceChart({ symbol, symbols, onSymbolChange }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const emaFastRef = useRef<ISeriesApi<'Line'> | null>(null)
  const emaSlowRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapRef = useRef<ISeriesApi<'Line'> | null>(null)

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [barCount, setBarCount] = useState(0)

  // Create the chart once; series are reused across symbol changes.
  useEffect(() => {
    if (!hostRef.current) return

    const chart = createChart(hostRef.current, {
      layout: {
        background: { color: '#141922' },
        textColor: '#8b95a7',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(35, 43, 56, 0.5)' },
        horzLines: { color: 'rgba(35, 43, 56, 0.5)' },
      },
      rightPriceScale: { borderColor: '#232b38' },
      timeScale: { borderColor: '#232b38', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 1 },
      autoSize: true,
    })

    candleRef.current = chart.addCandlestickSeries(CANDLE_STYLE)
    emaFastRef.current = chart.addLineSeries({
      color: '#4c8dff',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    emaSlowRef.current = chart.addLineSeries({
      color: '#e8a33d',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    vwapRef.current = chart.addLineSeries({
      color: '#8b95a7',
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
          <span style={{ color: '#4c8dff' }}>—</span> EMA20{' '}
          <span style={{ color: '#e8a33d' }}>—</span> EMA50{' '}
          <span style={{ color: '#8b95a7' }}>--</span> VWAP
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
