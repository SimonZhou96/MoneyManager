export type Market = 'HK' | 'US' | 'A'
export type Direction = 'upstream' | 'downstream' | 'peer'

export interface TopologyNodeData {
  code: string
  name: string
  market: Market
  sector: string
  pct_chg: number | null
  market_cap_str: string
  size_level: 1 | 2 | 3 | 4 | 5 | 6
  expanded: boolean
  stale: boolean
  is_center: boolean
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
  depth?: number
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
