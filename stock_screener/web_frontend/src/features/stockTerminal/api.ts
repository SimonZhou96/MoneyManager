import { api } from '../../api'
import type { StockTerminalSeriesResponse, StockTerminalSummary } from './types'

function query(params: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) search.set(key, String(value))
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

function stockTerminalEndpoint(market: string, code: string, path: string) {
  return `/api/stock-terminal/${encodeURIComponent(market)}/${encodeURIComponent(code)}${path}`
}

export function getStockTerminalSummary(market: string, code: string) {
  return api<StockTerminalSummary>(stockTerminalEndpoint(market, code, '/summary'))
}

export function getStockTerminalKlines(
  market: string,
  code: string,
  timeframe = '1d',
  limit = 120
) {
  return api<StockTerminalSeriesResponse>(
    `${stockTerminalEndpoint(market, code, '/klines')}${query({ timeframe, limit })}`
  )
}

export function getStockTerminalMinute(market: string, code: string) {
  return api<StockTerminalSeriesResponse>(stockTerminalEndpoint(market, code, '/minute'))
}

export function getStockTerminalFundFlow(market: string, code: string) {
  return api<StockTerminalSeriesResponse>(stockTerminalEndpoint(market, code, '/fund-flow'))
}
