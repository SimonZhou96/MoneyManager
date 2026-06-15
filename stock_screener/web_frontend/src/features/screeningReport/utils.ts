import type {
  BiasInfo, ContributionType, DataCompleteness, DimensionBreakdown,
  FactorItem, HistoryTrendPoint, ObserveCondition, TradeBias,
} from './types'
import { CONTRIBUTION_LABELS, DIMENSION_WEIGHTS } from './types'

// ── 评分 → 偏置映射 ──

interface ThresholdEntry {
  threshold: number
  bias: TradeBias
  label: string
  signal: BiasInfo['signal']
  signalLabel: string
  color: string
  description: string
}

const THRESHOLDS: ThresholdEntry[] = [
  {
    threshold: 80, bias: 'strong_buy', label: '强买入',
    signal: 'very_strong', signalLabel: '信号极强', color: '#16a34a',
    description: '买入信号很强，多数核心规则通过，形成强烈共振。',
  },
  {
    threshold: 60, bias: 'buy', label: '偏买入',
    signal: 'strong', signalLabel: '信号偏强', color: '#22c55e',
    description: '买入信号较强，多个维度出现正向共振。',
  },
  {
    threshold: 40, bias: 'neutral_watch', label: '中性观望',
    signal: 'neutral', signalLabel: '中性信号', color: '#6b7280',
    description: '多空信号不明确，适合继续观察。',
  },
  {
    threshold: 20, bias: 'weak_watch', label: '观望偏弱',
    signal: 'weak', signalLabel: '信号偏弱', color: '#f59e0b',
    description: '买入信号偏弱，暂未形成足够规则共振。',
  },
  {
    threshold: 0, bias: 'strong_sell', label: '强规避',
    signal: 'very_weak', signalLabel: '信号极弱', color: '#ef4444',
    description: '买入信号很弱，规则模型显示当前风险高于机会。',
  },
]

/** 根据 0-100 的买入评分返回偏置信息 */
export function scoreToBias(score: number | null | undefined): BiasInfo {
  if (score == null || !Number.isFinite(score)) {
    return {
      bias: 'neutral_watch', label: '无法判断', signal: 'neutral',
      signalLabel: '无信号', color: '#6b7280',
      description: '评分数据不可用，无法判断当前交易倾向。',
    }
  }
  const clamped = Math.max(0, Math.min(100, score))
  for (const entry of THRESHOLDS) {
    if (clamped >= entry.threshold) {
      return {
        bias: entry.bias,
        label: entry.label,
        signal: entry.signal,
        signalLabel: entry.signalLabel,
        color: entry.color,
        description: entry.description,
      }
    }
  }
  // fallback — never reached
  return THRESHOLDS[THRESHOLDS.length - 1]
}

// ── 规则贡献计算 ──

/** 从一条规则明细中提取贡献分值与类型 */
export function computeContribution(result: string, details: Record<string, unknown> | undefined): {
  score: number
  type: ContributionType
} {
  if (result === 'skip' || result === 'error' || result === 'not_applicable') {
    return { score: 0, type: 'not_included' }
  }

  const rawScore = (details as any)?.score ?? (details as any)?.total_score ?? 0
  const score = Number(rawScore) || 0

  if (result === 'fail') {
    // 失败规则的扣分权重：分数越大影响越大
    const impact = Math.abs(score) > 0 ? Math.abs(score) : 5
    return { score: impact, type: impact >= 5 ? 'negative' : 'neutral' }
  }

  // result === 'pass'
  if (score >= 8) return { score, type: 'strong_positive' }
  if (score >= 3) return { score, type: 'weak_positive' }
  return { score, type: 'neutral' }
}

// ── 维度分解 ──

interface RuleDetailLike {
  strategy_category?: string
  result?: string
  details?: Record<string, unknown>
}

/** 按策略维度分组统计 */
export function computeDimensionBreakdown(
  ruleDetails: RuleDetailLike[],
  resultJson: Record<string, unknown>,
): DimensionBreakdown[] {
  const categories = Object.entries(DIMENSION_WEIGHTS)

  // 从 resultJson 中提取各维度得分
  const scores: Record<string, number | null> = {
    technical: _safeNum(resultJson.technical_score),
    macro: _safeNum(resultJson.macro_score),
    event_hot: _safeNum((resultJson as any).event_hot_score) ?? _safeNum((resultJson as any).enterprise_score),
    capital_risk: _safeNum((resultJson as any).fund_risk_score),
    llm: _safeNum((resultJson as any).llm_score),
  }

  return categories.map(([key, config]) => {
    const rules = ruleDetails.filter(d => {
      const cat = d.strategy_category || ''
      // 空分类归入 technical（兜底）
      if (!cat) return key === 'technical'
      return cat === key
    })

    const passed = rules.filter(d => d.result === 'pass').length
    const failed = rules.filter(d => d.result === 'fail').length
    const notCalculated = rules.filter(d => d.result !== 'pass' && d.result !== 'fail').length
    const score = scores[key] ?? null

    // 判断贡献类型
    let contributionType: ContributionType = 'neutral'
    if (score == null || notCalculated > 0 && passed === 0 && failed === 0) {
      contributionType = 'not_included'
    } else if (config.weight === 0) {
      contributionType = 'not_included'
    } else if (score != null) {
      // 基于得分相对权重判断贡献方向
      const weightedContribution = score * (config.weight / 100)
      if (weightedContribution >= 25) contributionType = 'strong_positive'
      else if (weightedContribution >= 10) contributionType = 'weak_positive'
      else if (weightedContribution < 3 && passed < failed) contributionType = 'negative'
    }

    return {
      key,
      label: config.label,
      weight: config.weight,
      score,
      displayScore: score != null ? score.toFixed(1) : 'N/A',
      contributionType,
      contributionLabel: CONTRIBUTION_LABELS[contributionType],
      passed,
      failed,
      notCalculated,
    }
  })
}

// ── 因子提取 ──

interface RuleFactorLike {
  rule_key?: string
  rule_name?: string
  result?: string
  reason?: string
  details?: Record<string, unknown>
}

/** 提取主要加分项（top 3 passed 规则） */
export function extractTopPositiveFactors(ruleDetails: RuleFactorLike[]): FactorItem[] {
  return ruleDetails
    .filter(d => d.result === 'pass')
    .map(d => {
      const contrib = computeContribution('pass', d.details)
      return {
        ruleName: d.rule_name || d.rule_key || '未知规则',
        score: contrib.score,
        reason: d.reason || '',
        ruleKey: d.rule_key || '',
      }
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)
}

/** 提取主要扣分项（top 3 failed 规则） */
export function extractTopNegativeFactors(ruleDetails: RuleFactorLike[]): FactorItem[] {
  return ruleDetails
    .filter(d => d.result === 'fail')
    .map(d => {
      const contrib = computeContribution('fail', d.details)
      return {
        ruleName: d.rule_name || d.rule_key || '未知规则',
        score: contrib.score,
        reason: d.reason || '',
        ruleKey: d.rule_key || '',
      }
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 3)
}

// ── 观察条件生成 ──

/** 从 failed 规则中生成中性观察条件 */
export function generateObserveConditions(ruleDetails: RuleFactorLike[]): ObserveCondition[] {
  const failed = ruleDetails
    .filter(d => d.result === 'fail')
    .sort((a, b) => {
      const ca = computeContribution('fail', a.details).score
      const cb = computeContribution('fail', b.details).score
      return cb - ca
    })
    .slice(0, 5)

  return failed.map(r => {
    const name = r.rule_name || r.rule_key || '未知规则'
    return {
      condition: name,
      ruleName: name,
    }
  })
}

// ── 可信度计算 ──

/** 根据 AI 分析 + 数据完整度计算可信度 */
export function computeConfidence(
  aiAnalysis: Record<string, unknown> | null | undefined,
  ruleDetails: RuleDetailLike[],
): { level: 'high' | 'medium' | 'low'; label: string; reason: string } {
  // 优先用 AI 分析的 confidence_score
  if (aiAnalysis) {
    const cs = _safeNum((aiAnalysis as any).confidence_score ?? (aiAnalysis as any).reliability_score)
    if (cs != null) {
      if (cs >= 70) return { level: 'high', label: '高', reason: `AI 分析可信度 ${cs}/100` }
      if (cs >= 40) return { level: 'medium', label: '中', reason: `AI 分析可信度 ${cs}/100` }
      return { level: 'low', label: '低', reason: `AI 分析可信度 ${cs}/100` }
    }
  }

  // 兜底：用数据完整度
  const total = ruleDetails.length
  if (total === 0) return { level: 'low', label: '低', reason: '无规则数据' }
  const computed = ruleDetails.filter(d => d.result === 'pass' || d.result === 'fail').length
  const ratio = computed / total
  if (ratio >= 0.9) return { level: 'high', label: '高', reason: '数据完整，规则覆盖充分' }
  if (ratio >= 0.6) return { level: 'medium', label: '中', reason: '部分规则未计算，评分精度受影响' }
  return { level: 'low', label: '低', reason: '大量规则未计算，评分可能偏差较大' }
}

// ── 数据完整度 ──

/** 统计数据诊断结果 */
export function computeDataCompleteness(diagItems: { status: string }[]): DataCompleteness {
  const total = diagItems.length
  const normal = diagItems.filter(d => d.status === 'computed').length
  const error = diagItems.filter(d => d.status === 'error').length
  const missing = total - normal - error
  const completenessPct = total > 0 ? Math.round((normal / total) * 100) : 0

  let confidenceImpact: string
  if (completenessPct === 100) confidenceImpact = '数据完整，评分可靠'
  else if (completenessPct >= 80) confidenceImpact = '大部分数据可用，评分基本可靠'
  else if (completenessPct >= 50) confidenceImpact = '部分数据缺失，评分精度受影响'
  else confidenceImpact = '大量数据缺失，评分可能偏差较大'

  return { normalCount: normal, missingCount: missing, errorCount: error, completenessPct, confidenceImpact }
}

// ── 历史趋势 ──

export interface HistoryRunLike {
  run_id: string
  created_at: string | null
  final_score?: number | null
  passed?: boolean | null
  status?: string
}

/** 计算历史评分趋势 */
export function computeScoreTrend(runs: HistoryRunLike[]): HistoryTrendPoint[] {
  return [...runs]
    .filter(r => r.status === 'completed' || r.passed != null)
    .sort((a, b) => new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime())
    .map(r => ({
      runId: r.run_id,
      date: (r.created_at || '').slice(0, 10),
      score: _safeNum(r.final_score),
      bias: r.final_score != null ? scoreToBias(r.final_score).bias : null,
    }))
}

/** 比较两次评分的变化 */
export function scoreChangeLabel(prev: number | null, curr: number | null): {
  label: string; color: string; icon: string
} {
  if (prev == null || curr == null) return { label: '—', color: '#6b7280', icon: '—' }
  const diff = curr - prev
  if (Math.abs(diff) < 0.5) return { label: '持平', color: '#6b7280', icon: '→' }
  if (diff > 0) return { label: `上升 ${diff.toFixed(1)}`, color: '#22c55e', icon: '↑' }
  return { label: `下降 ${Math.abs(diff).toFixed(1)}`, color: '#ef4444', icon: '↓' }
}

// ── 一句话解释生成 ──

/** 自动生成一句话解释 */
export function generateExplanation(
  score: number | null | undefined,
  bias: BiasInfo,
  passCount: number,
  totalRules: number,
  dimensions: DimensionBreakdown[],
): string {
  if (score == null || !Number.isFinite(score)) {
    return '当前买入评分不可用，无法判断交易倾向。请检查 K 线数据是否正常获取。'
  }

  const pct = totalRules > 0 ? Math.round((passCount / totalRules) * 100) : 0
  const strongDims = dimensions.filter(d => d.contributionType === 'strong_positive').map(d => d.label)
  const weakDims = dimensions.filter(d => d.contributionType === 'negative').map(d => d.label)

  let parts = `当前买入评分为 ${score.toFixed(1)}，处于"${bias.label}"区间。`

  if (passCount > 0) {
    parts += `${totalRules} 条策略规则中 ${passCount} 条通过（${pct}%）。`
  } else {
    parts += `${totalRules} 条策略规则中无通过项。`
  }

  if (strongDims.length > 0) {
    parts += `${strongDims.join('、')}维度表现较好`
    if (weakDims.length > 0) parts += `，但${weakDims.join('、')}维度拖累明显`
    parts += '。'
  } else if (weakDims.length > 0) {
    parts += `${weakDims.join('、')}维度拖累明显，整体信号偏弱。`
  } else {
    parts += '各维度信号均衡，尚不足以形成明确买入信号。'
  }

  return parts
}

// ── 辅助 ──

function _safeNum(v: unknown): number | null {
  if (v == null) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
