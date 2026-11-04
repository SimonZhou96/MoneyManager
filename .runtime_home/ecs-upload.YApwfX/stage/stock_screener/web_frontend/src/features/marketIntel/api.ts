import { api } from '../../api'
import type { EvidencePackPreview, IntelBundle, MarketCode, MarketIntelSourcesResponse, ProviderRunsResponse } from './types'

function query(params: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function getStockIntel(market: MarketCode, code: string, refresh = false) {
  return api<IntelBundle>(
    `/api/market-intel/stocks/${encodeURIComponent(market)}/${encodeURIComponent(code)}${query({
      refresh,
      include_search: false,
      max_items_per_group: 20
    })}`
  )
}

export function getMarketDigest(market: MarketCode, refresh = false) {
  return api<IntelBundle>(
    `/api/market-intel/markets/${encodeURIComponent(market)}/digest${query({ refresh })}`
  )
}

export function getProviderRuns(market: MarketCode, code: string, limit = 20) {
  return api<ProviderRunsResponse>(
    `/api/market-intel/provider-runs${query({ market, code, limit })}`
  )
}

export function getMarketIntelSources() {
  return api<MarketIntelSourcesResponse>('/api/market-intel/sources')
}

export function previewEvidencePack(market: MarketCode, code: string, forceRefresh = false) {
  return api<EvidencePackPreview>('/api/market-intel/evidence-pack/preview', {
    method: 'POST',
    body: JSON.stringify({
      market,
      code,
      include_search: false,
      force_refresh: forceRefresh
    })
  })
}
