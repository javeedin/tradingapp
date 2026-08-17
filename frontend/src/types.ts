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

export interface BacktestResponse {
  stats: BacktestStats
  trades: Trade[]
  equity_curve: { timestamp: string; equity: number }[]
  summary: string
  symbols: string[]
  bars: Record<string, number>
}
