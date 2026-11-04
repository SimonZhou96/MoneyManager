import { useState, useMemo } from 'react'
import type { AtomicRuleMeta } from './types'
import { NODE_COLORS, ruleNodeCategory } from './types'

interface Props {
  rules: AtomicRuleMeta[]
  onDragStart: (event: React.DragEvent, rule: AtomicRuleMeta) => void
}

export function RuleSidebar({ rules, onDragStart }: Props) {
  const [search, setSearch] = useState('')
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const filtered = useMemo(() => {
    if (!search.trim()) return rules
    const kw = search.toLowerCase()
    return rules.filter(r =>
      r.rule_key.toLowerCase().includes(kw) ||
      r.rule_name.toLowerCase().includes(kw) ||
      r.implementation.toLowerCase().includes(kw)
    )
  }, [rules, search])

  const groups = useMemo(() => {
    const map: Record<string, AtomicRuleMeta[]> = { filter: [], strategy: [], macro: [] }
    for (const r of filtered) {
      const cat = ruleNodeCategory(r)
      map[cat].push(r)
    }
    return [
      { key: 'filter', label: '🏷 硬筛选', color: NODE_COLORS.filter, rules: map.filter },
      { key: 'strategy', label: '📊 技术信号', color: NODE_COLORS.strategy, rules: map.strategy },
      { key: 'macro', label: '🌐 宏观评估', color: NODE_COLORS.macro, rules: map.macro },
    ]
  }, [filtered])

  function toggle(key: string) {
    setCollapsed(prev => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <div className="rule-sidebar">
      <div className="rule-sidebar-search">
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="搜索规则..."
        />
      </div>
      <div className="rule-sidebar-hint">拖拽规则到画布</div>
      {groups.map(g => (
        <div key={g.key} className="rule-sidebar-group">
          <div
            className="rule-sidebar-group-header"
            onClick={() => toggle(g.key)}
            style={{ borderLeftColor: g.color }}
          >
            <span>{g.label}</span>
            <small>{g.rules.length} 条</small>
            <span className="collapse-arrow">{collapsed[g.key] ? '▸' : '▾'}</span>
          </div>
          {!collapsed[g.key] && (
            <div className="rule-sidebar-items">
              {g.rules.map(r => (
                <div
                  key={r.rule_key}
                  className="rule-sidebar-item"
                  draggable
                  onDragStart={e => onDragStart(e, r)}
                  style={{ borderLeftColor: g.color }}
                  title={r.description || r.rule_name}
                >
                  <span className="rule-item-name">{r.rule_name}</span>
                  <span className="rule-item-key">{r.rule_key}</span>
                  {!r.enabled && <span className="rule-item-disabled">停用</span>}
                </div>
              ))}
              {g.rules.length === 0 && (
                <div className="rule-sidebar-empty">无匹配规则</div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
