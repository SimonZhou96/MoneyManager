export type Market = 'HK' | 'US' | 'A' | string
export type Direction = 'upstream' | 'downstream' | 'peer'
export type TopologyZone = Direction | 'center'
export type QuoteStatus = 'pending' | 'queued' | 'cached' | 'fresh' | 'stale' | 'failed' | 'error' | 'skipped'
export type TopologyDataStage = 'llm_initial' | 'source_partial' | 'source_verified' | string
export type TopologyTaskStage = 'idle' | 'graph_loading' | 'graph_ready_search_enriching' | 'graph_ready_source_polling' | 'done' | 'partial' | 'failed'
export type TopologyGraphTaskStage =
  | 'queued'
  | 'initial_graph_running'
  | 'initial_graph_ready'
  | 'enriching'
  | 'source_polling'
  | 'done'
  | 'partial'
  | 'failed'
  | 'cancelled'
  | 'expired'

export interface TopologyNodeData {
  id: string
  code: string
  name: string
  market: Market
  sector: string
  industry?: string
  pct_chg: number | null
  market_cap?: number | null
  market_cap_str: string
  size_level: 1 | 2 | 3 | 4 | 5 | 6
  quote_status?: QuoteStatus
  quote_updated_at?: string | null
  quote_error?: string
  field_sources?: Record<string, string>
  field_confidence?: Record<string, number>
  data_stage?: TopologyDataStage
  data_gaps?: string[]
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
  data_stage?: TopologyDataStage
  background_started?: boolean
  error?: string
}

export interface TopologyGraph {
  center: TopologyNodeData
  nodes: TopologyNodeData[]
  edges: TopologyEdgeData[]
  stats: TopologyStats
  warnings?: string[]
}

export interface TopologyGraphTaskError {
  code: string
  message: string
}

export interface TopologyGraphTask {
  task_id: string
  stage: TopologyGraphTaskStage
  progress_pct: number
  graph: TopologyGraph
  message: string
  warnings: string[]
  error?: TopologyGraphTaskError | null
  updated_at: string
}

export interface TopologyNodePatch {
  id: string
  symbol?: string
  code: string
  market: Market
  name?: string
  sector?: string
  industry?: string
  pct_chg?: number | null
  market_cap?: number | null
  market_cap_str?: string
  quote_status?: QuoteStatus
  quote_updated_at?: string | null
  quote_error?: string
  field_sources?: Record<string, string>
  field_confidence?: Record<string, number>
  data_stage?: TopologyDataStage
  data_gaps?: string[]
}

export interface TopologySearchEnrichResult {
  items: TopologyNodePatch[]
  warnings?: string[]
  data_stage?: TopologyDataStage
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
  sector?: string
  industry?: string
  market_cap: number | null
  market_cap_str: string
  field_sources?: Record<string, string>
  field_confidence?: Record<string, number>
  data_stage?: TopologyDataStage
  data_gaps?: string[]
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
