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

export interface BacktestResponse {
  stats: BacktestStats
  trades: Trade[]
  equity_curve: { timestamp: string; equity: number }[]
  summary: string
  symbols: string[]
  bars: Record<string, number>
}
