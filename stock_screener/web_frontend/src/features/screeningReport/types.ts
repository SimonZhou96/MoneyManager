// ── 交易倾向 & 评分相关类型 ──

export type TradeBias = 'strong_sell' | 'sell' | 'weak_watch' | 'neutral_watch' | 'buy' | 'strong_buy'

export type SignalStrength = 'very_weak' | 'weak' | 'neutral' | 'strong' | 'very_strong'

export type ConfidenceLevel = 'high' | 'medium' | 'low'

export type ContributionType =
  | 'strong_positive'  // 正向加分
  | 'weak_positive'    // 轻微加分
  | 'neutral'          // 中性
  | 'negative'         // 明显拖累
  | 'not_included'      // 未纳入

// ── 评分偏置映射结果 ──

export interface BiasInfo {
  bias: TradeBias
  label: string            // e.g., "强规避", "观望偏弱", "中性观望", "偏买入", "强买入"
  signal: SignalStrength
  signalLabel: string      // e.g., "信号极弱", "信号偏弱", "中性信号", "信号偏强", "信号极强"
  color: string            // CSS color
  description: string      // 一句话区间解释
}

// ── 维度分解 ──

export interface DimensionBreakdown {
  key: string
  label: string             // e.g., "技术面", "宏观面", "事件热度", "资金风险"
  weight: number            // 0-100（百分比）
  score: number | null      // 维度得分
  displayScore: string      // 展示用字符串（如 "4.8", "N/A"）
  contributionType: ContributionType
  contributionLabel: string // 中文标签
  passed: number
  failed: number
  notCalculated: number
}

// ── 因子条目 ──

export interface FactorItem {
  ruleName: string
  score: number
  reason: string
  ruleKey: string
}

// ── 观察条件 ──

export interface ObserveCondition {
  condition: string
  ruleName: string
}

// ── 历史趋势数据 ──

export interface HistoryTrendPoint {
  runId: string
  date: string
  score: number | null
  bias: TradeBias | null
}

// ── 数据完整度 ──

export interface DataCompleteness {
  normalCount: number
  missingCount: number
  errorCount: number
  completenessPct: number
  confidenceImpact: string
}

// ── 维度权重映射常量 ──

export const DIMENSION_WEIGHTS: Record<string, { weight: number; label: string }> = {
  technical:   { weight: 0,  label: '技术面' },
  macro:       { weight: 40, label: '宏观面' },
  event_hot:   { weight: 30, label: '事件热度' },
  capital_risk:{ weight: 20, label: '资金风险' },
  llm:         { weight: 10, label: 'AI分析' },
}

export const CONTRIBUTION_LABELS: Record<ContributionType, string> = {
  strong_positive: '正向加分',
  weak_positive:   '轻微加分',
  neutral:         '中性',
  negative:        '明显拖累',
  not_included:    '未纳入',
}
