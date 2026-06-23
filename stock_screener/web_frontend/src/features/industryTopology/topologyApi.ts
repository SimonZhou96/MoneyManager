import { api } from '../../api'
import type { ExpandResult, SearchResult, TopologyGraph } from './types'

export const topologyApi = {
  search: (q: string, limit = 10) =>
    api<{ ok: true; data: SearchResult[] }>(`/api/topology/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  graph: (code: string, market: string, depth: number) =>
    api<{ ok: true; data: TopologyGraph }>('/api/topology/graph', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth }),
    }),
  expand: (code: string, market: string, depth: number, existingCodes: string[]) =>
    api<{ ok: true; data: ExpandResult }>('/api/topology/expand', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth, existing_codes: existingCodes }),
    }),
  refresh: (code: string, market: string) =>
    api<{ ok: true; data: ExpandResult }>('/api/topology/refresh', {
      method: 'POST',
      body: JSON.stringify({ code, market }),
    }),
}
