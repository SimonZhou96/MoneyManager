export type Market = 'HK' | 'US' | 'A' | string
export type Direction = 'upstream' | 'downstream' | 'peer'
export type TopologyZone = Direction | 'center'
export type QuoteStatus = 'pending' | 'queued' | 'cached' | 'fresh' | 'stale' | 'failed' | 'error' | 'skipped'

export interface TopologyNodeData {
  id: string
  code: string
  name: string
  market: Market
  sector: string
  pct_chg: number | null
  market_cap?: number | null
  market_cap_str: string
  size_level: 1 | 2 | 3 | 4 | 5 | 6
  quote_status?: QuoteStatus
  quote_updated_at?: string | null
  quote_error?: string
  expanded: boolean
  stale: boolean
  is_center: boolean
  depth: number
  zone: TopologyZone
}

export interface TopologyEdgeData {
  source: string
  target: string
  direction: Direction
  relation: string
  label: string
  evidence: string
}

export interface TopologyStats {
  llm_calls: number
  cached_nodes?: number
  stale_nodes?: number
  stale_sources?: Array<{ code: string; market: Market }>
  depth?: number
  relation_status?: 'cached' | 'generating' | 'fresh' | 'failed' | 'pending'
  background_started?: boolean
  error?: string
}

export interface TopologyGraph {
  center: TopologyNodeData
  nodes: TopologyNodeData[]
  edges: TopologyEdgeData[]
  stats: TopologyStats
}

export interface ExpandResult {
  nodes: TopologyNodeData[]
  edges: TopologyEdgeData[]
  stats: TopologyStats
}

export interface SearchResult {
  code: string
  name: string
  market: Market
  sector: string
}

export interface TopologyQuoteItem {
  symbol: string
  market: Market
  code: string
  status: QuoteStatus
  price: number | null
  pct_chg: number | null
  name: string
  market_cap: number | null
  market_cap_str: string
  source: string
  updated_at: string | null
  error: string
}

export interface TopologyQuoteBatch {
  items: TopologyQuoteItem[]
  ready: number
  pending: number
  failed: number
}
