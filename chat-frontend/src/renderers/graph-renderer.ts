import { markRaw } from 'vue'
import type { CausalGraphData, CausalGraphEdge, CausalGraphModel } from '../types/domain'

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

// 只在显示时舍入；领域模型中的概率与阈值保留原始精度。
function formatProbability(probability: number): string {
  return Number(probability.toPrecision(4)).toString()
}

function predictionLabel(model: CausalGraphModel, edge: CausalGraphEdge): string | null {
  if (model.metadata?.algorithm !== 'causal.cdfm' || !edge.prediction) return null
  const forward = formatProbability(edge.prediction.forward_probability)
  if (edge.edge_type === 'directed') return `p=${forward}`
  if (edge.edge_type === 'bidirected') {
    return `p₁=${forward}\np₂=${formatProbability(edge.prediction.reverse_probability)}`
  }
  return null
}

function graphThreshold(model: CausalGraphModel): number | null {
  const diagnostics = model.metadata?.diagnostics
  if (typeof diagnostics !== 'object' || diagnostics === null || !('metrics' in diagnostics)) return null
  const metrics = diagnostics.metrics
  if (typeof metrics !== 'object' || metrics === null || !('threshold' in metrics)) return null
  const threshold = metrics.threshold
  return typeof threshold === 'number' && Number.isFinite(threshold) ? threshold : null
}

export function causalGraphCaption(model: CausalGraphModel): string | null {
  if (model.metadata?.algorithm !== 'causal.cdfm') return null
  const notes: string[] = []
  if (model.edges.some((edge) => predictionLabel(model, edge) !== null)) {
    notes.push('CDFM 提供候选结构及模型预测概率。p 为模型预测概率，与结构系数和因果效应不同。')
    if (model.edges.some((edge) => edge.edge_type === 'bidirected' && edge.prediction)) {
      notes.push('双箭头的 p₁ 和 p₂ 分别是 source→target 和 target→source 的编码概率，共同编码潜在共同原因候选，不代表两条因果方向。')
    }
  }
  const threshold = graphThreshold(model)
  if (threshold !== null) notes.push(`本次实际阈值：${formatProbability(threshold)}。`)
  if (!notes.length) return null
  notes.push('数值仅作显示舍入。')
  return notes.join(' ')
}

export function describeCausalGraphEdge(model: CausalGraphModel, edgeId: string): string | null {
  const edge = model.edges.find((candidate) => candidate.id === edgeId)
  if (!edge) return null
  const label = predictionLabel(model, edge)
  if (label !== null && edge.prediction) {
    const nodeLabel = (id: string): string => {
      const node = model.nodes.find((candidate) => candidate.id === id)
      return node?.label || node?.variable || id
    }
    const source = nodeLabel(edge.source)
    const target = nodeLabel(edge.target)
    if (edge.edge_type === 'bidirected') {
      return `边：${source} ↔ ${target}。编码概率：source→target（${source} → ${target}）p₁=${formatProbability(edge.prediction.forward_probability)}；target→source（${target} → ${source}）p₂=${formatProbability(edge.prediction.reverse_probability)}。两个编码概率共同编码潜在共同原因候选，不代表两条因果方向。`
    }
    return `边：${source} → ${target}（${label}，模型预测概率）`
  }
  const weight = typeof edge.weight === 'number' ? formatWeight(edge.weight) : ''
  const arrow = model.metadata?.algorithm === 'causal.cdfm' && edge.edge_type === 'bidirected' ? '↔' : '→'
  return `边：${edge.source} ${arrow} ${edge.target}${weight ? `（${weight}）` : ''}`
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
    const label = predictionLabel(model, edge)
    if (label !== null) {
      projected.label = label
    } else if (typeof edge.weight === 'number' && Number.isFinite(edge.weight)) {
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
