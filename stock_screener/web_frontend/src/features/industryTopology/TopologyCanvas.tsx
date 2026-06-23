import { useMemo } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap, MarkerType,
  useNodesState, useEdgesState, type Node, type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'
import { TopologyNode } from './TopologyNode'
import type { TopologyEdgeData, TopologyNodeData } from './types'

const EDGE_COLOR: Record<string, string> = {
  upstream: '#3b82f6', downstream: '#f59e0b', peer: '#8b5cf6',
}

function layout(rawNodes: Node[], rawEdges: Edge[]): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 60, ranksep: 80 })
  g.setDefaultEdgeLabel(() => ({}))
  rawNodes.forEach((n) => {
    const sz = (n.data as unknown as TopologyNodeData)?.size_level || 1
    const w = [0, 120, 150, 180, 210, 240, 270][sz] || 120
    const h = [0, 72, 88, 104, 120, 136, 152][sz] || 72
    g.setNode(n.id, { width: w, height: h })
  })
  rawEdges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  const nodes = rawNodes.map((n) => {
    const pos = g.node(n.id)
    return { ...n, position: { x: pos.x - (pos.width / 2), y: pos.y - (pos.height / 2) } }
  })
  return { nodes, edges: rawEdges }
}

interface Props {
  rawNodes: Node[]
  rawEdges: Edge[]
  onExpand: (code: string, market: string) => void
}

export function TopologyCanvas({ rawNodes, rawEdges, onExpand }: Props) {
  const [, , onNodesChange] = useNodesState(rawNodes)
  const [, , onEdgesChange] = useEdgesState(rawEdges)

  const laid = useMemo(() => layout(rawNodes, rawEdges), [rawNodes, rawEdges])

  const styledEdges: Edge[] = laid.edges.map((e) => {
    const d = (e.data as unknown as TopologyEdgeData) || ({} as TopologyEdgeData)
    const color = EDGE_COLOR[d.direction] || '#94a3b8'
    return {
      ...e,
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: { stroke: color, strokeWidth: 2 },
      label: d.label,
      labelStyle: { fill: '#1f2937', fontWeight: 500, fontSize: 11 },
      labelBgStyle: { fill: '#ffffff' },
      labelBgPadding: [4, 2],
      labelBgBorderRadius: 4,
    }
  })

  return (
    <div className="topo-canvas" style={{ height: '70vh' }}
         onClick={(ev) => {
           const t = ev.target as HTMLElement
           const btn = t.closest('.topo-node-expand') as HTMLButtonElement | null
           if (btn && !btn.disabled) {
             const id = btn.getAttribute('data-node-id') || ''
             const node = laid.nodes.find((n) => n.id === id)
             if (node) onExpand(id, (node.data as unknown as TopologyNodeData).market)
           }
         }}>
      <ReactFlow
        nodes={laid.nodes} edges={styledEdges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        nodeTypes={{ topology: TopologyNode }}
        fitView fitViewOptions={{ padding: 0.2 }}
      >
        <Background /> <Controls /> <MiniMap />
      </ReactFlow>
    </div>
  )
}
