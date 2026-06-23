import { useEffect, useRef, useState } from 'react'
import { topologyApi } from './topologyApi'
import type { SearchResult } from './types'

interface Props {
  onSelect: (s: SearchResult) => void
  selected?: SearchResult | null
}

export function StockSearchInput({ onSelect, selected }: Props) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!q.trim()) { setItems([]); return }
    setLoading(true)
    const t = setTimeout(async () => {
      try {
        const res = await topologyApi.search(q.trim())
        setItems(res.data)
        setOpen(true)
      } catch { setItems([]) } finally { setLoading(false) }
    }, 300)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  return (
    <div className="topo-search" ref={boxRef}>
      <input
        className="topo-search-input"
        placeholder="输入股票代码或名称"
        value={selected ? `${selected.name} (${selected.code})` : q}
        onChange={(e) => { setQ(e.target.value); if (selected) { /* 允许重新搜索 */ } }}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false) }}
      />
      {open && (
        <ul className="topo-search-dropdown">
          {loading && <li>搜索中…</li>}
          {!loading && items.length === 0 && <li>无匹配</li>}
          {!loading && items.map((s) => (
            <li key={`${s.market}:${s.code}`} onClick={() => { onSelect(s); setOpen(false); setQ('') }}>
              <span className="topo-search-name">{s.name}</span>
              <span className="topo-search-code">{s.code}</span>
              <span className="topo-search-sector">{s.sector}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
