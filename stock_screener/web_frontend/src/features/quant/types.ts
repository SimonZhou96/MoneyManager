// ── 策略元信息（GET /api/quant/strategies） ──
export type StrategyMeta = {
  type: string
  name: string
  params: Record<string, StrategyParamDef>
}

export type StrategyParamDef = {
  type: 'int' | 'float'
  default: number
  min: number
  max: number
  label: string
}

// ── 策略回测请求 ──
export type StrategyBacktestRequest = {
  market: string
  symbols: string[]
  strategy: { type: string; params: Record<string, number>; entry_side: string }
  start: string
  end: string
  initial_cash: number
  quantity: number
  commission_rate: number
  slippage_rate: number
  max_position_weight: number
  risk?: { stop_loss_pct?: number; take_profit_pct?: number; trailing_stop_pct?: number }
}

// ── 参数优化请求 ──
export type OptimizationRequest = {
  market: string
  symbol: string
  strategy_type: string
  param_space: Record<string, number[]>
  objective: string
  start: string
  end: string
  initial_cash: number
  quantity: number
}

// ── 优化结果 ──
export type OptimizationResultItem = {
  params: Record<string, number>
  rank: number
  objective_value: number
  sharpe: number
  total_return: number
  max_drawdown: number
  win_rate: number
}

// ── 保持原有类型（向后兼容） ──
export type QuantBacktestRequest = {
  market: string
  symbols: string[]
  strategy_source: 'rule_chain' | 'meta_rules' | 'option_lab'
  entry_chain_key: string
  exit_policy: Record<string, unknown>
  start: string
  end: string
  initial_cash: number
  quantity: number
  commission_rate: number
  slippage_rate: number
  max_position_weight: number
}

export type QuantBacktestSubmitResponse = { run_id: string; status: string }

export type QuantProgressLog = { time?: string; message: string }

export type QuantBacktestStatus = {
  run_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | string
  progress_pct: number
  current_stage: string
  metrics: Record<string, number>
  chart?: QuantBacktestChart
  warnings: string[]
  progress_logs: QuantProgressLog[]
  error_message?: string | null
  created_at?: string | null
  finished_at?: string | null
}

export type QuantBacktestChart = {
  symbols: QuantSymbolChart[]
  optimization_results?: OptimizationResultItem[]
}

export type QuantSymbolChart = {
  symbol: string
  bars: QuantKlineBar[]
  signals: QuantSignalMarker[]
  trades: QuantTradeMarker[]
  overlays?: QuantStrategyOverlay[]
}

export type QuantKlineBar = { date: string; open: number; high: number; low: number; close: number; volume: number }
export type QuantSignalMarker = { signal_id: string; date: string; direction: 'buy' | 'sell' | string; reason: string; price?: number | null }
export type QuantTradeMarker = { trade_id: string; date?: string | null; side: 'buy' | 'sell' | string; quantity: number; price: number }

export type QuantStrategyOverlay = {
  type: 'zuoyi' | 'ema' | 'rsi' | 'volume' | 'pct_change' | string
  rule_key: string; rule_name: string
  items?: QuantZuoYiOverlayItem[]; lines?: QuantOverlayLine[]
  thresholds?: number[]; bars?: QuantOverlayPoint[]; signals?: QuantOverlaySignal[]
}

export type QuantZuoYiOverlayItem = {
  direction: 'bullish' | 'bearish' | string
  left_one_date: string; median_date: string; breakout_date: string
  left_one_high: number; left_one_low: number
  median_high?: number; median_low?: number; breakout_close?: number
}

export type QuantOverlayLine = { name: string; points: QuantOverlayPoint[] }
export type QuantOverlayPoint = { date: string; value: number }

export type QuantOverlaySignal = {
  date: string; label?: string; rule_key?: string; rule_name?: string
  value?: number | null; today_volume?: number | null
  max_volume_prior3?: number | null; pct_change?: number | null
  band_min?: number | null; band_max?: number | null
}
