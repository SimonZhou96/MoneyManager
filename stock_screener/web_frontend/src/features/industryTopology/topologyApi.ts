import { api } from '../../api'
import type {
  ExpandResult,
  SearchResult,
  TopologyGraph,
  TopologyGraphTask,
  TopologyQuoteBatch,
  TopologySearchEnrichResult,
} from './types'

export const topologyApi = {
  search: (q: string, limit = 10) =>
    api<{ ok: true; data: SearchResult[] }>(`/api/topology/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  graph: (code: string, market: string, depth: number, centerName = '', quoteMode = 'llm_initial') =>
    api<{ ok: true; data: TopologyGraph }>('/api/topology/graph', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth, center_name: centerName, quote_mode: quoteMode }),
    }),
  createGraphTask: (code: string, market: string, depth: number, centerName = '', quoteMode = 'llm_initial') =>
    api<{ ok: true; data: TopologyGraphTask }>('/api/topology/graph/tasks', {
      method: 'POST',
      body: JSON.stringify({ code, market, depth, center_name: centerName, quote_mode: quoteMode }),
    }),
  getGraphTask: (taskId: string) =>
    api<{ ok: true; data: TopologyGraphTask }>(`/api/topology/graph/tasks/${encodeURIComponent(taskId)}`),
  cancelGraphTask: (taskId: string) =>
    api<{ ok: true; data: TopologyGraphTask }>(`/api/topology/graph/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
    }),
  searchEnrich: (center: { market: string; code: string; name?: string; sector?: string; industry?: string }, symbols: string[]) =>
    api<{ ok: true; data: TopologySearchEnrichResult }>('/api/topology/graph/search-enrich', {
      method: 'POST',
      body: JSON.stringify({
        center_market: center.market,
        center_code: center.code,
        center_name: center.name || '',
        center_sector: center.sector || '',
        center_industry: center.industry || '',
        symbols,
      }),
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
