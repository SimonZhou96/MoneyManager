import { api } from '../../api'
import type {
  HotSectorsResponse,
  SectorStocksResponse,
  SingleStockResult,
  BacktestResult,
  BacktestConfig,
} from './types'

/** 获取热点板块列表 */
export function getHotSectors(market: string, limit = 15) {
  return api<HotSectorsResponse>(
    `/api/sectors/hot?market=${encodeURIComponent(market)}&limit=${limit}`
  )
}

/** 获取板块下成分股 */
export function getSectorStocks(sectorName: string, market: string, limit = 30) {
  return api<SectorStocksResponse>(
    `/api/sectors/${encodeURIComponent(sectorName)}/stocks?market=${encodeURIComponent(market)}&limit=${limit}`
  )
}

/** 获取K线数据 */
export function getKlines(market: string, code: string, timeframe = '1d', limit = 120) {
  const search = new URLSearchParams({ timeframe, limit: String(limit) })
  return api<{ market: string; code: string; timeframe: string; rows: Array<Record<string, unknown>> }>(
    `/api/stock-terminal/${encodeURIComponent(market)}/${encodeURIComponent(code)}/klines?${search.toString()}`
  )
}

/** 获取股票摘要 */
export function getStockSummary(market: string, code: string) {
  return api<{ market: string; code: string; name?: string; quote?: Record<string, unknown> }>(
    `/api/stock-terminal/${encodeURIComponent(market)}/${encodeURIComponent(code)}/summary`
  )
}

/** 提交单股分析 */
export function submitSingleStock(market: string, code: string, timeframe: string, chainKey?: string) {
  return api<{ run_id: string; status: string }>('/api/screening/single-stock', {
    method: 'POST',
    body: JSON.stringify({ market, code, timeframe, chain_key: chainKey || undefined }),
  })
}

/** 轮询单股分析结果 */
export function getSingleStockResult(runId: string) {
  return api<SingleStockResult & { status?: string }>(`/api/screening/single-stock/${runId}`)
}

/** 提交回测 */
export function submitBacktest(config: BacktestConfig) {
  return api<{ run_id: string; status: string }>('/api/quant/backtests', {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

/** 轮询回测结果 */
export function getBacktestResult(runId: string) {
  return api<BacktestResult>(`/api/quant/backtests/${runId}`)
}
