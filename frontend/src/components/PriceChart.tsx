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

type Timeframe = '1minute' | '5minute' | '30minute' | '1day'
type Indicator = 'ema_20' | 'ema_50' | 'vwap' | 'rsi' | 'adx' | 'atr'

const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: '1minute', label: '1m' },
  { value: '5minute', label: '5m' },
  { value: '30minute', label: '30m' },
  { value: '1day', label: '1D' },
]

const INDICATORS: { id: Indicator; label: string; defaultOn: boolean }[] = [
  { id: 'ema_20', label: 'EMA20', defaultOn: true },
  { id: 'ema_50', label: 'EMA50', defaultOn: true },
  { id: 'vwap', label: 'VWAP', defaultOn: true },
  { id: 'rsi', label: 'RSI', defaultOn: false },
  { id: 'adx', label: 'ADX', defaultOn: false },
  { id: 'atr', label: 'ATR', defaultOn: false },
]

export default function PriceChart({ symbol, symbols, onSymbolChange, theme }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const indicatorSeriesRef = useRef<Map<Indicator, ISeriesApi<'Line'>>>(new Map())

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [barCount, setBarCount] = useState(0)
  const [timeframe, setTimeframe] = useState<Timeframe>('5minute')
  const [enabledIndicators, setEnabledIndicators] = useState<Set<Indicator>>(
    new Set(INDICATORS.filter((i) => i.defaultOn).map((i) => i.id)),
  )
  const [showIndicatorMenu, setShowIndicatorMenu] = useState(false)

  const getIndicatorColor = (indicator: Indicator, colors: ReturnType<typeof chartPalette>) => {
    const colorMap: Record<Indicator, string> = {
      ema_20: colors.accent,
      ema_50: colors.amber,
      vwap: colors.faint,
      rsi: colors.up,
      adx: colors.down,
      atr: colors.border,
    }
    return colorMap[indicator] || colors.accent
  }

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

    // Create series for each indicator
    indicatorSeriesRef.current.clear()
    for (const indicator of INDICATORS) {
      const series = chart.addLineSeries({
        color: getIndicatorColor(indicator.id, colors),
        lineWidth: indicator.id === 'vwap' ? 1 : 1,
        lineStyle: indicator.id === 'vwap' ? 2 : undefined,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      indicatorSeriesRef.current.set(indicator.id, series)
    }

    chartRef.current = chart
    return () => {
      chart.remove()
      chartRef.current = null
      indicatorSeriesRef.current.clear()
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

    // Update all indicator colors
    for (const [indicator, series] of indicatorSeriesRef.current) {
      series.applyOptions({ color: getIndicatorColor(indicator, colors) })
    }
  }, [theme])

  useEffect(() => {
    if (!symbol) return
    let cancelled = false

    setLoading(true)
    setError(null)

    api
      .candles(symbol, 400, timeframe)
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

        // Update all indicator series
        for (const indicator of INDICATORS) {
          const series = indicatorSeriesRef.current.get(indicator.id)
          if (series) {
            if (enabledIndicators.has(indicator.id)) {
              series.setData(line(indicator.id))
            } else {
              series.setData([])
            }
          }
        }

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
  }, [symbol, timeframe, enabledIndicators])

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

        {/* Timeframe selector */}
        <select value={timeframe} onChange={(e) => setTimeframe(e.target.value as Timeframe)}>
          {TIMEFRAMES.map((tf) => (
            <option key={tf.value} value={tf.value}>
              {tf.label}
            </option>
          ))}
        </select>

        {/* Indicator toggle menu */}
        <div style={{ position: 'relative', display: 'inline-block' }}>
          <button
            onClick={() => setShowIndicatorMenu(!showIndicatorMenu)}
            style={{
              fontSize: 12,
              padding: '6px 10px',
              background: enabledIndicators.size > 3 ? 'var(--accent)' : undefined,
              color: enabledIndicators.size > 3 ? '#fff' : undefined,
              border: enabledIndicators.size > 3 ? 'none' : undefined,
            }}
            title="Toggle indicators"
          >
            📊 {enabledIndicators.size}/{INDICATORS.length}
          </button>
          {showIndicatorMenu && (
            <div
              style={{
                position: 'absolute',
                top: 32,
                left: 0,
                background: 'var(--panel)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                minWidth: 150,
                zIndex: 100,
                boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
              }}
            >
              {INDICATORS.map((indicator) => (
                <label
                  key={indicator.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 12px',
                    fontSize: 12,
                    cursor: 'pointer',
                    borderBottom: '1px solid var(--border-soft)',
                  }}
                  onMouseDown={(e) => e.preventDefault()}
                >
                  <input
                    type="checkbox"
                    checked={enabledIndicators.has(indicator.id)}
                    onChange={(e) => {
                      const newEnabled = new Set(enabledIndicators)
                      if (e.target.checked) {
                        newEnabled.add(indicator.id)
                      } else {
                        newEnabled.delete(indicator.id)
                      }
                      setEnabledIndicators(newEnabled)
                    }}
                    style={{ cursor: 'pointer' }}
                  />
                  {indicator.label}
                </label>
              ))}
            </div>
          )}
        </div>

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
