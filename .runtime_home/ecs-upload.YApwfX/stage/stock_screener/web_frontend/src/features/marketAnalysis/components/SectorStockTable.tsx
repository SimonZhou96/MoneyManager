import React from 'react'
import type { SectorStock } from '../types'

interface Props {
  stocks: SectorStock[]
  loading: boolean
  selectedCode: string
  onStockClick: (stock: SectorStock) => void
}

function formatVal(v: unknown, decimals = 2): string {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n.toFixed(decimals)
}

function formatVolume(v: unknown): string {
  if (v === null || v === undefined) return '-'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(0) + 'K'
  return String(Math.round(n))
}

export function SectorStockTable({ stocks, loading, selectedCode, onStockClick }: Props) {
  if (loading) {
    return (
      <div className="sector-stock-table">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>成交量</th><th>日期</th></tr>
            </thead>
          </table>
        </div>
        <div className="skeleton-rows">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton-row">
              <span className="skeleton-cell w-20" />
              <span className="skeleton-cell w-32" />
              <span className="skeleton-cell w-16" />
              <span className="skeleton-cell w-16" />
              <span className="skeleton-cell w-20" />
              <span className="skeleton-cell w-24" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (stocks.length === 0) {
    return (
      <div className="sector-stock-table">
        <div className="empty-state">该板块暂无成分股数据</div>
      </div>
    )
  }

  return (
    <div className="sector-stock-table">
      <div className="table-wrap" style={{ maxHeight: 300, overflowY: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>最新价</th>
              <th>涨跌幅</th>
              <th>成交量</th>
              <th>日期</th>
            </tr>
          </thead>
          <tbody>
            {stocks.map(stock => {
              const change = stock.change_pct ?? 0
              const isSelected = stock.code === selectedCode
              return (
                <tr
                  key={stock.code}
                  className={`clickable-row${isSelected ? ' selected' : ''}`}
                  onClick={() => onStockClick(stock)}
                >
                  <td className="mono">{stock.code.replace(/^(HK\.|US\.|SH\.|SZ\.)/, '')}</td>
                  <td className="name-cell">{stock.name || stock.code}</td>
                  <td className="mono">{formatVal(stock.price)}</td>
                  <td className={`mono ${change >= 0 ? 'up' : 'down'}`}>
                    {change >= 0 ? '+' : ''}{formatVal(change)}%
                  </td>
                  <td className="mono muted">{formatVolume(stock.volume)}</td>
                  <td className="mono muted" style={{ fontSize: 12 }}>{stock.date ? String(stock.date).slice(0, 10) : '-'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
