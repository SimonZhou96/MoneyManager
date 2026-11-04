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

export type MarketIntelSource = {
  provider: string
  source: string
  reliability_tier: string
  markets: string[]
  item_types: string[]
  freshness_minutes: number
  requires_auth: boolean
  requires_browser: boolean
  default_enabled: boolean
  automated_safe: boolean
  enabled: boolean
  status: string
  disabled_reason?: string
}

export type MarketIntelSourcesResponse = {
  sources: MarketIntelSource[]
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
