export interface AccountSummary {
  mode: string
  equity: number
  cash: number
  open_positions: number
  realised_pnl: number
  unrealised_pnl: number
  total_pnl: number
  total_trades: number
  wins: number
  losses: number
  win_rate: number
  starting_capital?: number
  return_pct?: number
}

export interface RiskStatus {
  halted: boolean
  halted_reason: string
  daily_pnl: number
  daily_pnl_pct: number
  max_daily_loss_pct: number
  risk_per_trade_pct: number
  max_open_positions: number
  risk_reward_ratio: number
  trailing_stop: boolean
}

export interface Status {
  mode: string
  connected: boolean
  market_open: boolean
  session_age_hours: number | null
  symbols: string[]
  interval: string
  product: string
  cycles: number
  last_cycle: string | null
  last_error: string
  account: AccountSummary
  risk: RiskStatus
  login_url?: string
  live_mode_warning?: string | null
}

export interface Position {
  symbol: string
  side: string
  quantity: number
  entry_price: number
  entry_time: string
  product: string
  stoploss: number
  target: number
  last_price: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
  value: number
  order_id: string
}

export interface FactorBreakdown {
  regime: number
  trend: number
  momentum: number
  volatility: number
  volume: number
}

export interface Signal {
  symbol: string
  timestamp: string
  action: string
  score: number
  price: number
  atr: number
  factors: FactorBreakdown
  reasons: string[]
}

export interface Trade {
  symbol: string
  side: string
  quantity: number
  entry_price: number
  exit_price: number
  entry_time: string
  exit_time: string
  pnl: number
  costs: number
  net_pnl: number
  return_pct: number
  exit_reason: string
  product: string
  holding_minutes: number
}

export interface Candle {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface LinePoint {
  time: string
  value: number
}

export interface CandleResponse {
  symbol: string
  candles: Candle[]
  indicators: Record<string, LinePoint[]>
}

export interface EngineEvent {
  timestamp: string
  kind: string
  message: string
}

export interface BacktestStats {
  total_trades: number
  wins?: number
  losses?: number
  win_rate: number
  net_pnl: number
  total_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  profit_factor: number
  expectancy?: number
  avg_win?: number
  avg_loss?: number
  total_costs?: number
  best_trade?: number
  worst_trade?: number
  avg_holding_minutes?: number
  exits_by_reason?: Record<string, number>
  note?: string
}

export interface Quote {
  symbol: string
  price: number
  previous_close: number
  change: number
  change_pct: number
  open: number
  high: number
  low: number
  volume: number
  updated_at: string
  source: string
}

export interface TickerStatus {
  streaming: boolean
  polling: boolean
  poll_interval_seconds?: number
  ticks_received?: number
  symbols?: string[]
  tracked?: number
  last_error?: string
}

export interface TradePlan {
  entry: number
  stoploss: number
  target: number
  stop_distance: number
  stop_distance_pct: number
  target_distance: number
  target_distance_pct: number
  risk_reward: number
  quantity: number
  notional: number
  risk_amount: number
  risk_pct_of_equity: number
  approved: boolean
  sizing_note: string
  binding_constraint: string
  was_capped: boolean
  product: string
}

export interface MarketContext {
  price: number
  atr: number
  atr_pct: number
  rsi: number
  adx: number
  ema_20: number
  ema_50: number
  ema_200: number
  vwap: number
  volume_ratio: number
  above_vwap: boolean
  trend: string
}

export interface Analysis {
  symbol: string
  interval: string
  as_of: string
  candles_used: number
  action: string
  side: string
  score: number
  entry_threshold: number
  conviction: string
  factors: FactorBreakdown
  reasons: string[]
  plan: TradePlan
  market: MarketContext
}

export interface ExpiryCandidate {
  date: string
  breeze_format: string
  kind: string
  label: string
  days_away: number
}

export interface OptionLeg {
  ltp: number | null
  open_interest: number | null
  volume: number | null
  change: number | null
  bid: number | null
  ask: number | null
}

export interface OptionRow {
  strike: number
  call: OptionLeg | null
  put: OptionLeg | null
}

export interface OptionChainResponse {
  symbol: string
  expiry: string
  exchange: string
  spot: number | null
  rows: OptionRow[]
  count: number
}

export interface BrokerPosition {
  symbol: string
  side: string
  quantity: number
  entry_price: number
  last_price: number
  product: string
  product_label: string
  leveraged: boolean
  exitable_via_api: boolean
  unrealised_pnl: number
  unrealised_pct: number
  value: number
  atr: number
  levels_available: boolean
  stoploss?: number
  target?: number
  stop_distance_pct?: number
  target_distance_pct?: number
  risk_reward?: number
  risk_amount?: number
  status?: string
  progress_pct?: number
  note?: string
}

export interface PositionAlert {
  symbol: string
  status: string
  last_price: number
  stoploss: number | null
  target: number | null
  exitable_via_api: boolean
  timestamp: string
}

export interface BrokerPositionsResponse {
  positions: BrokerPosition[]
  alerts: PositionAlert[]
  count: number
  raw_rows: number
}

export interface OrderProduct {
  value: string
  label: string
  leveraged?: boolean
}

export interface OrderProductsResponse {
  placeable: OrderProduct[]
  not_placeable: OrderProduct[]
  note: string
}

export interface PlaceOrderResponse {
  order: {
    order_id: string
    symbol: string
    side: string
    quantity: number
    product: string
    status: string
    filled_price: number | null
    message: string
  }
  mode: string
  plan: { entry: number; stoploss: number; target: number; quantity: number }
  analysis: { action: string; score: number; conviction: string; reasons: string[] }
}

export interface Funds {
  available: number
  bank_balance: number
  allocated_equity: number
  allocated_fno: number
  total_allocated: number
  blocked: number
  source: string
  raw: Record<string, unknown>
}

export interface FundsResponse {
  funds: Funds
  mode: string
  broker_funds?: Funds
  broker_funds_error?: string
}

export interface Affordability {
  quantity: number
  price: number
  notional: number
  estimated_costs: number
  buffer: number
  required: number
  available: number
  affordable: boolean
  shortfall: number
  max_affordable_quantity: number
}

export interface OrderPreview {
  symbol: string
  side: string
  product: string
  placeable: boolean
  plan: TradePlan
  action: string
  conviction: string
  score: number
  reasons: string[]
  funds: Affordability
  account: Funds
}

export interface OrderRecord {
  order_id: string | null
  symbol: string
  side: string
  quantity: number
  price: number
  product: string
  status: string
  stoploss: number | null
  target: number | null
  filled_price: number | null
  filled_quantity: number
  message: string | null
  origin: string
  mode: string
  placed_at: string
}

export interface OrderHistoryResponse {
  orders: OrderRecord[]
  stats: {
    total: number
    filled: number
    rejected: number
    cancelled: number
    pending: number
  }
  session_orders: unknown[]
  mode: string
  broker_orders?: Record<string, unknown>[]
  broker_orders_error?: string
}

export interface BacktestResponse {
  stats: BacktestStats
  trades: Trade[]
  equity_curve: { timestamp: string; equity: number }[]
  summary: string
  symbols: string[]
  bars: Record<string, number>
}
