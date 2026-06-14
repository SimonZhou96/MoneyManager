import React, { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import type { HotSector } from '../types'

interface Props {
  sectors: HotSector[]
  loading: boolean
  selectedName: string | null
  onSectorClick: (sector: HotSector) => void
}

function sectorColor(changePct: number): string {
  const abs = Math.abs(changePct)
  if (changePct >= 0) {
    if (abs < 0.5) return '#164e3b'
    if (abs < 1.5) return '#047857'
    if (abs < 3)   return '#059669'
    if (abs < 5)   return '#10b981'
    return '#00c781'
  }
  if (abs < 0.5) return '#4c1d25'
  if (abs < 1.5) return '#7f1d1d'
  if (abs < 3)   return '#b91c1c'
  if (abs < 5)   return '#dc2626'
  return '#ef4444'
}

export function SectorTreemap({ sectors, loading, selectedName, onSectorClick }: Props) {
  const chartRef = useRef<HTMLDivElement>(null)
  const instanceRef = useRef<echarts.ECharts | null>(null)

  // Init chart ONCE — never re-create on re-render
  useEffect(() => {
    if (!chartRef.current) return
    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current, undefined, { renderer: 'canvas' })
    }
    const chart = instanceRef.current
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
      instanceRef.current = null
    }
  }, [])

  // Update chart data
  useEffect(() => {
    const chart = instanceRef.current
    if (!chart) return

    if (loading) {
      chart.showLoading('default', {
        text: '加载中...',
        color: '#c9a84c',
        textColor: '#8a8070',
        maskColor: 'rgba(17, 24, 39, 0.85)',
      })
      return
    }
    chart.hideLoading()

    if (sectors.length === 0) {
      chart.clear()
      return
    }

    const data = sectors.map(s => ({
      name: s.name,
      value: Math.max(s.heat_score, 0.5),
      change_pct: s.change_pct || 0,
      stock_count: s.stock_count || 0,
      reason: s.reason || '',
      itemStyle: { color: sectorColor(s.change_pct || 0) },
    }))

    chart.setOption({
      backgroundColor: '#111827',
      tooltip: {
        show: true,
        trigger: 'item',
        backgroundColor: 'rgba(20, 25, 32, 0.96)',
        borderColor: '#1f2937',
        borderWidth: 1,
        borderRadius: 8,
        padding: [14, 18],
        textStyle: { color: '#e8e0d0', fontSize: 13 },
        formatter: (params: any) => {
          const d = params.data || {}
          const chg = d.change_pct || 0
          const c = chg >= 0 ? '#10b981' : '#ef4444'
          const sign = chg >= 0 ? '+' : ''
          return `<div style="font-weight:700;font-size:16px;margin-bottom:8px;color:#ffffff">${d.name}</div>
            <div style="display:grid;gap:4px;font-size:13px">
              <div>涨跌幅: <b style="color:${c}">${sign}${chg.toFixed(2)}%</b></div>
              <div>成分股: <b style="color:#ffffff">${d.stock_count || 0}</b> 只</div>
              <div style="color:#9ca3af;font-size:11px;margin-top:2px">${d.reason || ''}</div>
            </div>`
        },
      },
      series: [{
        type: 'treemap',
        width: '100%',
        height: '100%',
        top: 6,
        bottom: 6,
        left: 6,
        right: 6,
        roam: false,
        nodeClick: 'link',
        breadcrumb: { show: false },
        label: {
          show: true,
          position: 'inside',
          formatter: (p: any) => {
            const d = p.data || {}
            const chg = d.change_pct || 0
            const sign = chg >= 0 ? '+' : ''
            return `{name|${d.name}}\n{chg|${sign}${chg.toFixed(2)}%}`
          },
          rich: {
            name: { fontSize: 14, fontWeight: 'bold', color: '#ffffff', lineHeight: 22 },
            chg:  { fontSize: 13, fontWeight: 'bold', color: '#ffffff', lineHeight: 20 },
          },
          hideOverlap: true,
        },
        itemStyle: { borderColor: '#0b1220', borderWidth: 2, borderRadius: 3 },
        levels: [{
          color: [
            '#4c1d25', '#7f1d1d', '#b91c1c', '#dc2626', '#1f3a2e',
            '#164e3b', '#047857', '#059669', '#10b981', '#00c781',
          ],
          colorMappingBy: 'value',
          visualMin: -5,
          visualMax: 5,
          itemStyle: { borderColor: '#1f2937', borderWidth: 2, gapWidth: 3 },
        }],
        data,
      }],
    }, true)

    chart.off('click')
    chart.on('click', (params: any) => {
      const name = params.data?.name || params.name
      if (!name) return
      const s = sectors.find(x => x.name === name)
      if (s) onSectorClick(s)
    })
  }, [sectors, loading, selectedName, onSectorClick])

  return (
    <div className="sector-treemap-chart">
      {sectors.length === 0 && !loading && (
        <div className="empty-state" style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', zIndex: 10 }}>
          <div className="empty-icon">📊</div>
          <div>暂无热点板块数据</div>
          <span className="muted">切换市场或稍后重试</span>
        </div>
      )}
      <div ref={chartRef} style={{ width: '100%', aspectRatio: '16/9', background: '#111827', borderRadius: 8 }} />
    </div>
  )
}
