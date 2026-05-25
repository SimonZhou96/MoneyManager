export type MarketCode = 'HK' | 'US' | 'A'

export type SourceStatus = {
  provider: string
  status: string
  item_count: number
  error_message?: string
  fetched_at?: string | null
  stale?: boolean
}

export type IntelItem = {
  scope_type?: string
  market?: string
  code?: string
  source?: string
  provider?: string
  item_type?: string
  title?: string
  summary?: string
  url?: string
  published_at?: string | null
  fetched_at?: string | null
  expires_at?: string | null
  is_stale?: boolean
  dedupe_key?: string
}

export type IntelBundle = {
  scope_type?: string
  market: string
  code?: string
  groups?: Record<string, IntelItem[]>
  freshness_status?: string
  source_status?: Record<string, SourceStatus>
  data_gaps?: string[]
}

export type ProviderRun = {
  provider?: string
  market?: string
  code?: string
  status?: string
  item_count?: number
  error_message?: string
  started_at?: string
  finished_at?: string
  created_at?: string
}

export type ProviderRunsResponse = {
  runs: ProviderRun[]
}

export type EvidencePackPreview = {
  market: string
  code: string
  structured_items?: IntelItem[]
  search_documents?: IntelItem[]
  manual_items?: IntelItem[]
  market_context?: IntelBundle
  stock_context?: IntelBundle
  source_status?: Record<string, SourceStatus>
  data_gaps?: string[]
  citations?: Array<Record<string, unknown>>
}
