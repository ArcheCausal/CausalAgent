import { markRaw } from 'vue'
import type { CausalGraphData, CausalGraphModel } from '../types/domain'

interface GraphInstance {
  on: (event: string, callback: (params: Record<string, unknown>) => void) => void
  setOptions: (options: Record<string, unknown>) => void
  setData: (data: { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> }) => void
  destroy: () => void
}

interface VisNetworkModule {
  Network: new (
    container: HTMLElement,
    data: { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> },
    options: Record<string, unknown>,
  ) => GraphInstance
}

export type GraphMode = 'view' | 'select'

export interface GraphNodeSelection {
  id: string
  label: string
}

export interface GraphEdgeSelection {
  id: string
  from: string
  to: string
  label: string
}

export interface CausalGraphHandle {
  update: (graph: CausalGraphData) => void
  destroy: () => void
}

export interface CausalGraphMountOptions {
  mode?: GraphMode
  onSelectNode?: (selection: GraphNodeSelection) => void
  onSelectEdge?: (selection: GraphEdgeSelection) => void
}

// 因果图业务模型到 vis-network 的唯一投影边界：组件和 store 都不直接生产 vis 字段。
const EDGE_ARROWS: Record<string, string> = {
  directed: 'to',
  partially_directed: 'to',
  partially_oriented: 'to',
  bidirected: 'to,from',
}

const DASHED_EDGE_TYPES = new Set(['undirected', 'partially_directed', 'partially_oriented'])

function formatWeight(weight: number): string {
  if (!Number.isFinite(weight)) return ''
  return Number(weight.toPrecision(6)).toString()
}

export function projectCausalGraphForVis(model: CausalGraphModel): CausalGraphData {
  const nodes = (model.nodes ?? []).map((node) => ({
    id: node.id,
    label: node.label || node.variable || node.id,
  }))
  const edges = (model.edges ?? []).map((edge) => {
    const projected: Record<string, unknown> = {
      id: edge.id,
      from: edge.source,
      to: edge.target,
      arrows: EDGE_ARROWS[edge.edge_type] ?? '',
      dashes: DASHED_EDGE_TYPES.has(edge.edge_type),
    }
    if (typeof edge.weight === 'number' && Number.isFinite(edge.weight)) {
      projected.label = formatWeight(edge.weight)
    }
    return projected
  })
  return { nodes, edges }
}

const interactionOptions: Record<string, unknown> = {
  dragNodes: true,
  dragView: true,
  zoomView: true,
}

const graphOptions: Record<string, unknown> = {
  layout: { hierarchical: { enabled: false } },
  edges: {
    arrows: { to: { enabled: true, scaleFactor: 1, type: 'arrow' } },
    color: '#848484',
    font: { size: 12 },
    smooth: { enabled: true, type: 'dynamic' },
  },
  nodes: { shape: 'box', size: 30, font: { size: 14, color: '#333' }, borderWidth: 2 },
  physics: {
    enabled: true,
    barnesHut: {
      gravitationalConstant: -2000,
      centralGravity: 0.3,
      springLength: 95,
      springConstant: 0.04,
      damping: 0.09,
      avoidOverlap: 0.1,
    },
    solver: 'barnesHut',
    stabilization: { iterations: 1000 },
  },
}

export async function mountCausalGraph(
  container: HTMLElement,
  graph: CausalGraphData,
  options: CausalGraphMountOptions = {},
): Promise<CausalGraphHandle> {
  const module = await import('vis-network/standalone') as unknown as VisNetworkModule
  const mode: GraphMode = options.mode ?? 'view'
  let current = graph
  const network = markRaw(new module.Network(
    container,
    { nodes: current.nodes, edges: current.edges },
    {
      ...graphOptions,
      interaction: { ...interactionOptions, selectable: mode === 'select' },
    },
  ))
  network.on('stabilizationIterationsDone', () => network.setOptions({ physics: { enabled: false } }))

  if (mode === 'select') {
    network.on('selectNode', (params) => {
      const selected = Array.isArray(params.nodes) ? params.nodes[0] : undefined
      if (typeof selected !== 'string') return
      const node = current.nodes.find((candidate) => candidate.id === selected)
      const label = node && typeof node.label === 'string' ? node.label : selected
      options.onSelectNode?.({ id: selected, label })
    })
    network.on('selectEdge', (params) => {
      const selected = Array.isArray(params.edges) ? params.edges[0] : undefined
      if (typeof selected !== 'string') return
      const edge = current.edges.find((candidate) => candidate.id === selected)
      const from = edge && typeof edge.from === 'string' ? edge.from : ''
      const to = edge && typeof edge.to === 'string' ? edge.to : ''
      const label = edge && typeof edge.label === 'string' ? edge.label : ''
      options.onSelectEdge?.({ id: selected, from, to, label })
    })
  }

  return {
    update: (next) => {
      current = next
      network.setData({ nodes: current.nodes, edges: current.edges })
    },
    destroy: () => network.destroy(),
  }
}
