export type DataBlockStatus = {
  status: string
  source?: string
  fetched_at?: string | null
  expires_at?: string | null
  stale?: boolean
  error_message?: string
}

export type QuoteSnapshot = {
  market: string
  code: string
  name?: string
  price?: number | null
  change?: number | null
  change_percent?: number | null
  open_price?: number | null
  high?: number | null
  low?: number | null
  previous_close?: number | null
  volume?: number | null
  turnover?: number | null
  fetched_at?: string | null
  source?: string
}

export type SeriesPoint = {
  at: string | null
  open?: number | null
  high?: number | null
  low?: number | null
  close?: number | null
  price?: number | null
  average_price?: number | null
  volume?: number | null
  turnover?: number | null
  inflow?: number | null
  outflow?: number | null
  net_inflow?: number | null
  main_net_inflow?: number | null
  retail_net_inflow?: number | null
}

export type StockTerminalSummary = {
  market: string
  code: string
  name?: string
  quote?: QuoteSnapshot | null
  source_status: Record<string, DataBlockStatus>
  data_gaps?: string[]
}

export type StockTerminalSeriesResponse = {
  market: string
  code: string
  timeframe?: string
  rows: SeriesPoint[]
  source_status: Record<string, DataBlockStatus>
  data_gaps?: string[]
}
