export type MarketCode = 'HK' | 'US' | 'A'

export type Timeframe = '1m' | '5m' | '15m' | '30m' | '60m' | '1d' | '1wk'

export interface HotSector {
  name: string
  source: string
  heat_score: number
  score: number
  reason: string
  stock_count: number
  change_pct: number
  avg_price?: number
}

export interface HotSectorsResponse {
  market: MarketCode
  sectors: HotSector[]
  total: number
}

export interface SectorStock {
  code: string
  name: string
  price: number | null
  change_pct: number | null
  volume: number | null
  market_cap: number | null
  date?: string | null
}

export interface SectorStocksResponse {
  market: MarketCode
  sector: string
  stocks: SectorStock[]
  total: number
}

export interface KlineBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export interface SingleStockResult {
  run_id: string
  market: string
  code: string
  name: string
  sector: string
  industry: string
  market_cap: number | null
  pe_ratio: number | null
  timeframe: string
  passed: boolean
  data_source: string
  rule_chain: {
    chain_key: string
    chain_name: string
    passed: boolean
    details: RuleDetail[]
  }
  conditions_met: string[]
  ai_analysis: AiAnalysis | null
  warnings: string[]
  status?: string
}

export interface RuleDetail {
  rule_key: string
  rule_name: string
  rule_type: string
  strategy_category: string
  result: string
  reason: string
  details: Record<string, unknown>
  display_order: number
}

export interface AiAnalysis {
  code: string
  name: string
  analysis_status: string
  reliability_score: number
  confidence_score: number
  signal_bias: string
  summary: string
  positive_factors: string[]
  risk_factors: string[]
  macro_factors: string[]
  company_events: unknown[]
  market_hot_news: unknown[]
  company_hot_news: unknown[]
  news_impact: string
  hot_sectors: string[]
  hot_sector_mark: string
  matched_hot_sectors: string[]
  hot_sector_relevance: number
  hot_sector_reason: string
  model: string
  error_message: string
}

export interface BacktestConfig {
  market: string
  symbols: string[]
  strategy_source: string
  entry_chain_key: string
  exit_policy: { type: string; days?: number; pct?: number }
  start: string
  end: string
  initial_cash: number
  quantity: number
  commission_rate: number
  slippage_rate: number
  max_position_weight: number
}

export interface BacktestMetrics {
  total_return: number
  annual_return?: number
  sharpe_ratio?: number
  max_drawdown: number
  win_rate: number
  win_loss_ratio?: number
  total_trades?: number
}

export interface BacktestResult {
  run_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress_pct: number
  current_stage: string
  metrics: BacktestMetrics | null
  equity_curve: { date: string; value: number }[]
  trades: { date: string; side: string; price: number; quantity: number; pnl?: number }[]
  error_message: string | null
  warnings: string[]
}
