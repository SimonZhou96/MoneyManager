import React, { useMemo } from 'react'
import { computeDataCompleteness } from './utils'

type DataStatus = 'computed' | 'not_computed' | 'missing' | 'error'

interface DiagItem {
  key: string
  label: string
  status: DataStatus
  detail: string
  weightImpact?: string  // 对评分权重的影响
}

interface Props {
  diagItems: DiagItem[]
  defaultOpen?: boolean
}

export function EnhancedDataDiagnostics({ diagItems, defaultOpen }: Props) {
  const completeness = useMemo(() => computeDataCompleteness(diagItems), [diagItems])

  const hasError = diagItems.some(d => d.status === 'error')

  return (
    <details className="diagnostic-panel" open={defaultOpen ?? hasError}>
      <summary className="diagnostic-summary">
        <span>数据诊断</span>
        <span className="diagnostic-counts">
          <span className="diag-count-normal">{completeness.normalCount} 正常</span>
          {completeness.missingCount > 0 && (
            <span className="diag-count-missing">{completeness.missingCount} 缺失</span>
          )}
          {completeness.errorCount > 0 && (
            <span className="diag-count-error">{completeness.errorCount} 异常</span>
          )}
        </span>
        <span className="diagnostic-confidence">
          · 数据完整度 {completeness.completenessPct}% · {completeness.confidenceImpact}
        </span>
      </summary>

      <div className="diagnostic-items">
        {diagItems.map(item => (
          <div key={item.key} className="diagnostic-item">
            <span className={`data-status data-status-${item.status}`}>
              {item.status === 'computed' ? '已计算' :
               item.status === 'not_computed' ? '未计算' :
               item.status === 'missing' ? '数据缺失' : '接口失败'}
            </span>
            <span className="diag-label">{item.label}</span>
            <span className="diag-detail">{item.detail}</span>
          </div>
        ))}
      </div>
    </details>
  )
}
