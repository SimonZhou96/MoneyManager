import { useCallback, useState } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { StockSearchInput } from './StockSearchInput'
import { TopologyCanvas } from './TopologyCanvas'
import { topologyApi } from './topologyApi'
import type { SearchResult } from './types'

export function IndustryTopologyPanel() {
  const [selected, setSelected] = useState<SearchResult | null>(null)
  const [depth, setDepth] = useState(3)
  const [nodes, setNodes] = useState<Node[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [stats, setStats] = useState<string>('')

  const startTopology = useCallback(async () => {
    if (!selected) return
    try {
      const res = await topologyApi.graph(selected.code, selected.market, depth)
      const g = res.data
      setNodes(g.nodes.map((n) => ({ id: n.code, type: 'topology' as const, position: { x: 0, y: 0 }, data: n as unknown as Record<string, unknown> })))
      setEdges(g.edges.map((e) => ({ id: `${e.source}->${e.target}`, source: e.source, target: e.target, data: e as unknown as Record<string, unknown> })))
      setStats(`LLM调用 ${g.stats.llm_calls} | 缓存 ${g.stats.cached_nodes ?? 0} | stale ${g.stats.stale_nodes ?? 0}`)
    } catch (e) {
      setStats(`拓扑失败: ${(e as Error).message}`)
    }
  }, [selected, depth])

  const onExpand = useCallback(async (code: string, market: string) => {
    if (!selected) return
    const existing = nodes.map((n) => n.id)
    try {
      const res = await topologyApi.expand(code, market, depth, existing)
      const d = res.data
      setNodes((prev) => {
        const map = new Map(prev.map((n) => [n.id, n]))
        d.nodes.forEach((n) => map.set(n.code, { id: n.code, type: 'topology' as const, position: { x: 0, y: 0 }, data: n as unknown as Record<string, unknown> }))
        // 标记被展开节点 expanded
        return Array.from(map.values()).map((n) => n.id === code ? { ...n, data: { ...n.data, expanded: true } } : n)
      })
      setEdges((prev) => {
        const set = new Set(prev.map((e) => e.id))
        const merged = [...prev]
        d.edges.forEach((e) => { const id = `${e.source}->${e.target}`; if (!set.has(id)) merged.push({ id, source: e.source, target: e.target, data: e as unknown as Record<string, unknown> }) })
        return merged
      })
    } catch { /* toast 可后续加 */ }
  }, [selected, nodes, depth])

  const onRefresh = useCallback(async () => {
    if (!selected) return
    try {
      await topologyApi.refresh(selected.code, selected.market)
      await startTopology()
    } catch { /* */ }
  }, [selected, startTopology])

  return (
    <div className="industry-topology">
      <div className="topo-toolbar">
        <StockSearchInput onSelect={setSelected} selected={selected} onClearSelected={() => setSelected(null)} />
        <label>深度
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>{d}度</option>)}
          </select>
        </label>
        <button onClick={startTopology} disabled={!selected}>开始拓扑</button>
        <button onClick={onRefresh} disabled={!selected}>刷新关系</button>
        <span className="topo-stats">{stats}</span>
      </div>
      {nodes.length > 0 ? (
        <TopologyCanvas rawNodes={nodes} rawEdges={edges} onExpand={onExpand} />
      ) : <div className="topo-empty">选择股票后点击"开始拓扑"</div>}
    </div>
  )
}
