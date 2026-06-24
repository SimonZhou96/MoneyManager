import { api } from '../../api'
import type { ExpandResult, SearchResult, TopologyGraph, TopologyQuoteBatch } from './types'

export const topologyApi = {
  search: (q: string, limit = 10) =>
    api<{ ok: true; data: SearchResult[] }>(`/api/topology/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  graph: (code: string, market: string, depth: number) =>
    api<{ ok: true; data: TopologyGraph }>('/api/topology/graph', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth }),
    }),
  expand: (code: string, market: string, depth: number, existingSymbols: string[]) =>
    api<{ ok: true; data: ExpandResult }>('/api/topology/expand', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth, existing_codes: existingSymbols }),
    }),
  refresh: (code: string, market: string) =>
    api<{ ok: true; data: ExpandResult }>('/api/topology/refresh', {
      method: 'POST',
      body: JSON.stringify({ code, market }),
    }),
  quotes: (symbols: string[]) =>
    api<{ ok: true; data: TopologyQuoteBatch }>(`/api/topology/quotes?symbols=${encodeURIComponent(symbols.join(','))}`),
  refreshQuotes: (symbols: string[], force = false) =>
    api<{ ok: true; data: { items: unknown[] } }>('/api/topology/quotes/refresh', {
      method: 'POST',
      body: JSON.stringify({ symbols, force }),
    }),
}
