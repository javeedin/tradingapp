import type {
  Analysis,
  FundsResponse,
  OrderHistoryResponse,
  OrderPreview,
  BacktestResponse,
  BrokerPositionsResponse,
  CandleResponse,
  EngineEvent,
  ExpiryCandidate,
  OptionChainResponse,
  OptionOrderRequest,
  OptionPlan,
  OptionPositionsResponse,
  OrderProductsResponse,
  OrderRecord,
  PlaceOrderResponse,
  Position,
  Quote,
  RuntimeSettings,
  SettingsUpdate,
  Signal,
  Status,
  TickerStatus,
  Trade,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    // FastAPI puts the human-readable message in `detail`.
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body?.detail) message = typeof body.detail === 'string' ? body.detail : message
    } catch {
      // Non-JSON error body — keep the status line.
    }
    throw new Error(message)
  }

  return response.json() as Promise<T>
}

export const api = {
  status: () => request<Status>('/status'),

  positions: () =>
    request<{ positions: Position[]; account: Status['account'] }>('/positions'),

  signals: () =>
    request<{ latest: Signal[]; history: unknown[] }>('/signals'),

  trades: () =>
    request<{ trades: Trade[]; stats: Record<string, number>; session_trades: Trade[] }>(
      '/trades',
    ),

  events: () => request<{ events: EngineEvent[] }>('/events'),

  candles: (symbol: string, limit = 300) =>
    request<CandleResponse>(`/candles/${encodeURIComponent(symbol)}?limit=${limit}`),

  submitSession: (sessionToken: string) =>
    request<{ connected: boolean; message: string; backfill_error?: string }>('/session', {
      method: 'POST',
      body: JSON.stringify({ session_token: sessionToken }),
    }),

  loginUrl: () => request<{ url: string; instructions: string }>('/session/login-url'),

  killSwitch: () =>
    request<{ closed: number; halted: boolean }>('/kill-switch', { method: 'POST' }),

  resume: () => request<{ halted: boolean }>('/resume', { method: 'POST' }),

  closePosition: (symbol: string) =>
    request<{ closed: Trade }>('/positions/close', {
      method: 'POST',
      body: JSON.stringify({ symbol }),
    }),

  runCycle: () => request<Record<string, unknown>>('/cycle', { method: 'POST' }),

  ticker: () => request<{ quotes: Quote[]; status: TickerStatus }>('/ticker'),

  funds: () => request<FundsResponse>('/funds'),

  orderHistory: (limit = 200) =>
    request<OrderHistoryResponse>(`/orders?limit=${limit}`),

  previewOrder: (symbol: string, side: string, product: string, quantity = 0) => {
    const params = new URLSearchParams({
      symbol: symbol.toUpperCase(),
      side,
      product,
      quantity: String(quantity),
    })
    return request<OrderPreview>(`/orders/preview?${params.toString()}`)
  },

  brokerPositions: () => request<BrokerPositionsResponse>('/broker/positions'),

  orderProducts: () => request<OrderProductsResponse>('/order-products'),

  placeOrder: (payload: {
    symbol: string
    side: string
    product: string
    quantity?: number
    price?: number
    stoploss?: number
    target?: number
  }) =>
    request<PlaceOrderResponse>('/orders', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  startTicker: () => request<TickerStatus>('/ticker/start', { method: 'POST' }),

  analyse: (symbol: string, opts?: { interval?: string; intraday?: boolean }) => {
    const params = new URLSearchParams()
    if (opts?.interval) params.set('interval', opts.interval)
    if (opts?.intraday !== undefined) params.set('intraday', String(opts.intraday))
    const query = params.toString()
    return request<Analysis>(
      `/analyse/${encodeURIComponent(symbol.toUpperCase())}${query ? `?${query}` : ''}`,
    )
  },

  expiries: (symbol?: string) =>
    request<{ expiries: ExpiryCandidate[]; note: string; has_weeklies: boolean | null }>(
      `/expiries${symbol ? `?symbol=${encodeURIComponent(symbol.toUpperCase())}` : ''}`,
    ),

  optionChain: (symbol: string, expiry?: string) => {
    const params = new URLSearchParams({ symbol: symbol.toUpperCase() })
    if (expiry) params.set('expiry', expiry)
    return request<OptionChainResponse>(`/option-chain?${params.toString()}`)
  },

  settings: () => request<RuntimeSettings>('/settings'),

  updateSettings: (payload: SettingsUpdate) =>
    request<RuntimeSettings>('/settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  previewOptionOrder: (payload: OptionOrderRequest) =>
    request<OptionPlan>('/options/preview', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  placeOptionOrder: (payload: OptionOrderRequest) =>
    request<{ order: Record<string, unknown>; plan: OptionPlan; mode: string }>(
      '/options/orders',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  optionPositions: () => request<OptionPositionsResponse>('/options/positions'),

  optionOrders: (limit = 200) =>
    request<{ orders: OrderRecord[]; session_orders: unknown[]; mode: string }>(
      `/options/orders?limit=${limit}`,
    ),

  backtest: (payload: {
    days: number
    symbols?: string[]
    intraday?: boolean
    entry_threshold?: number
  }) =>
    request<BacktestResponse>('/backtest', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatNumber(value: number, digits = 2): string {
  return new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

export function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-IN', { hour12: false })
}
