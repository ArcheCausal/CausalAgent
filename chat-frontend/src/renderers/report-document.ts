import { z } from 'zod'
import type {
  CausalGraphModel,
  ChartAsset,
  ChartAssetData,
  ChartType,
  ReportEvidence,
  ReportSource,
} from '../types/domain'

/*
 * 报告文档的前端校验与投影边界。后端已经校验过一次，这里只做防御性解析：
 * 未知块类型降级成占位块，非法资源引用降级成受控提示，绝不让渲染层读到脏数据。
 */

const reportDocumentSchema = z.object({
  schema_version: z.number(),
  report_id: z.string(),
  title: z.string(),
  blocks: z.array(z.unknown()),
  assets: z.record(z.string(), z.unknown()),
  sources: z.array(z.unknown()).optional(),
  evidence_refs: z.array(z.unknown()).optional(),
})

const chartAssetSchema = z.object({
  asset_key: z.string(),
  type: z.literal('chart'),
  chart_type: z.enum(['histogram', 'bar', 'heatmap']),
  data: z.record(z.string(), z.unknown()),
  metadata: z.record(z.string(), z.unknown()).optional(),
  options: z.record(z.string(), z.unknown()).optional(),
})

const causalGraphModelSchema = z.object({
  graph_id: z.string(),
  schema_version: z.number(),
  nodes: z.array(z.object({
    id: z.string(),
    variable: z.string(),
    label: z.string(),
  })),
  edges: z.array(z.object({
    id: z.string(),
    source: z.string(),
    target: z.string(),
    edge_type: z.string(),
    weight: z.number().nullable().optional(),
  })),
  metadata: z.record(z.string(), z.unknown()).optional(),
})

const reportSourceSchema = z.object({
  source_id: z.string(),
  kind: z.string(),
  title: z.string(),
  file_id: z.number().nullable().optional(),
  url: z.string().nullable().optional(),
})

const reportEvidenceSchema = z.object({
  evidence_id: z.string(),
  source_ids: z.array(z.string()).optional(),
  locator: z.record(z.string(), z.unknown()).optional(),
  description: z.string(),
})

export type ReportBlockView =
  | { kind: 'section'; id: string; title: string | null; children: ReportBlockView[] }
  | { kind: 'markdown'; id: string; content: string; evidenceRefs: string[] }
  | { kind: 'chart'; id: string; title: string | null; asset: ChartAsset | null; error: string | null }
  | { kind: 'causal_graph'; id: string; title: string | null; graph: CausalGraphModel | null; error: string | null }
  | { kind: 'unknown'; id: string; blockType: string }

export interface ReportDocumentView {
  reportId: string
  title: string
  blocks: ReportBlockView[]
  sources: ReportSource[]
  evidenceRefs: ReportEvidence[]
}

function textOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}

function numberList(value: unknown): number[] | null {
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'number' && Number.isFinite(item))) {
    return null
  }
  return value
}

function chartDataConsistent(chartType: ChartType, data: Record<string, unknown>): ChartAssetData | null {
  if (chartType === 'histogram') {
    const bins = numberList(data.bins)
    const counts = numberList(data.counts)
    if (!bins || !counts || bins.length !== counts.length + 1 || bins.length < 2) return null
    return { bins, counts }
  }
  if (chartType === 'bar') {
    const counts = numberList(data.counts)
    const categories = Array.isArray(data.categories)
      ? data.categories.filter((item): item is string => typeof item === 'string')
      : null
    if (!counts || !categories || categories.length !== counts.length || !categories.length) return null
    return { categories, counts }
  }
  const variables = Array.isArray(data.variables)
    ? data.variables.filter((item): item is string => typeof item === 'string')
    : null
  const matrix = Array.isArray(data.matrix)
    ? data.matrix.map((row) => numberList(row))
    : null
  if (!variables || !matrix || variables.length < 2) return null
  if (matrix.length !== variables.length) return null
  if (matrix.some((row) => row === null || row.length !== variables.length)) return null
  return { variables, matrix: matrix as number[][] }
}

export function projectChartAsset(raw: unknown): ChartAsset | null {
  const parsed = chartAssetSchema.safeParse(raw)
  if (!parsed.success) return null
  const data = chartDataConsistent(parsed.data.chart_type, parsed.data.data)
  if (!data) return null
  return {
    asset_key: parsed.data.asset_key,
    type: 'chart',
    chart_type: parsed.data.chart_type,
    data,
    metadata: parsed.data.metadata,
    options: parsed.data.options,
  }
}

export function projectCausalGraphModel(raw: unknown): CausalGraphModel | null {
  const parsed = causalGraphModelSchema.safeParse(raw)
  if (!parsed.success) return null
  // 没有节点的图无法渲染，按不可用资源处理，由块组件给受控提示。
  if (!parsed.data.nodes.length) return null
  return {
    graph_id: parsed.data.graph_id,
    schema_version: parsed.data.schema_version,
    nodes: parsed.data.nodes,
    edges: parsed.data.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      edge_type: edge.edge_type,
      weight: typeof edge.weight === 'number' ? edge.weight : null,
    })),
    metadata: parsed.data.metadata,
  }
}

function normalizeBlock(raw: unknown, assets: Record<string, unknown>, fallbackId: string): ReportBlockView {
  if (typeof raw !== 'object' || raw === null) {
    return { kind: 'unknown', id: fallbackId, blockType: '' }
  }
  const record = raw as Record<string, unknown>
  const id = typeof record.id === 'string' && record.id ? record.id : fallbackId
  const blockType = typeof record.type === 'string' ? record.type : ''
  const title = textOrNull(record.title)

  if (blockType === 'section') {
    const children = Array.isArray(record.children) ? record.children : []
    return {
      kind: 'section',
      id,
      title,
      children: children.map((child, index) => normalizeBlock(child, assets, `${id}_${index}`)),
    }
  }
  if (blockType === 'markdown') {
    return {
      kind: 'markdown',
      id,
      content: typeof record.content === 'string' ? record.content : '',
      evidenceRefs: stringList(record.evidence_refs),
    }
  }
  if (blockType === 'chart') {
    return { kind: 'chart', id, title, ...resolveChartAsset(record.asset_key, assets) }
  }
  if (blockType === 'causal_graph') {
    return { kind: 'causal_graph', id, title, ...resolveGraphAsset(record.asset_key, assets) }
  }
  return { kind: 'unknown', id, blockType }
}

function resolveChartAsset(
  assetKey: unknown,
  assets: Record<string, unknown>,
): { asset: ChartAsset | null; error: string | null } {
  if (typeof assetKey !== 'string' || !assetKey) {
    return { asset: null, error: '图表引用缺失。' }
  }
  const asset = projectChartAsset(assets[assetKey])
  return asset ? { asset, error: null } : { asset: null, error: '图表数据不可用。' }
}

function resolveGraphAsset(
  assetKey: unknown,
  assets: Record<string, unknown>,
): { graph: CausalGraphModel | null; error: string | null } {
  if (typeof assetKey !== 'string' || !assetKey) {
    return { graph: null, error: '因果图引用缺失。' }
  }
  const graph = projectCausalGraphModel(assets[assetKey])
  return graph ? { graph, error: null } : { graph: null, error: '因果图数据不可用。' }
}

function normalizeSources(raw: unknown): ReportSource[] {
  if (!Array.isArray(raw)) return []
  const sources: ReportSource[] = []
  for (const item of raw) {
    const parsed = reportSourceSchema.safeParse(item)
    if (!parsed.success) continue
    sources.push({
      source_id: parsed.data.source_id,
      kind: parsed.data.kind,
      title: parsed.data.title,
      file_id: parsed.data.file_id ?? null,
      url: parsed.data.url ?? null,
    })
  }
  return sources
}

function normalizeEvidence(raw: unknown): ReportEvidence[] {
  if (!Array.isArray(raw)) return []
  const evidence: ReportEvidence[] = []
  for (const item of raw) {
    const parsed = reportEvidenceSchema.safeParse(item)
    if (!parsed.success) continue
    evidence.push({
      evidence_id: parsed.data.evidence_id,
      source_ids: parsed.data.source_ids ?? [],
      locator: parsed.data.locator,
      description: parsed.data.description,
    })
  }
  return evidence
}

export function parseReportDocument(payload: unknown): ReportDocumentView | null {
  const parsed = reportDocumentSchema.safeParse(payload)
  if (!parsed.success) return null
  const document = parsed.data
  return {
    reportId: document.report_id,
    title: document.title,
    blocks: document.blocks.map((block, index) => normalizeBlock(block, document.assets, `block_${index}`)),
    sources: normalizeSources(document.sources),
    evidenceRefs: normalizeEvidence(document.evidence_refs),
  }
}

/**
 * 去掉检索文本的“类型/标题”前缀，得到可读的证据片段。
 *
 * 报告里的描述已被后端折叠成单行空格分隔，标题本身可能含空格，
 * 因此只在标题与来源展示名一致时才按长度裁剪，否则保留标题文本。
 */
export function evidenceSnippet(description: string, sourceTitle = '', limit = 160): string {
  let body = description.replace(/\s+/g, ' ').trim()
  const typePrefix = /^类型：\S+\s*/.exec(body)
  if (typePrefix) {
    body = body.slice(typePrefix[0].length)
    const titlePrefix = /^标题：/.exec(body)
    if (titlePrefix) {
      const afterTitle = body.slice(titlePrefix[0].length)
      const knownTitle = sourceTitle.trim()
      body = knownTitle && afterTitle.startsWith(knownTitle)
        ? afterTitle.slice(knownTitle.length)
        : afterTitle
    }
  }
  const cleaned = body.trim()
  return cleaned.length > limit ? cleaned.slice(0, limit) + '…' : cleaned
}

/** 从证据定位串解析物理页码；没有页码时返回 null。 */
export function evidencePage(locator: Record<string, unknown> | undefined | null): string | null {
  const raw = locator?.locator
  if (typeof raw !== 'string') return null
  const matched = /#page=(\d+)/.exec(raw)
  return matched?.[1] ?? null
}

/** 证据 ID → 引用它的报告块 ID，按报告顺序排列，供来源导航跳转。 */
export function collectEvidenceUsage(
  blocks: ReportBlockView[],
  evidenceIds: ReadonlySet<string>,
): Map<string, string[]> {
  const usage = new Map<string, string[]>()
  const visit = (items: ReportBlockView[]): void => {
    for (const block of items) {
      if (block.kind === 'section') {
        visit(block.children)
        continue
      }
      if (block.kind !== 'markdown') continue
      const textualRefs = block.content.match(/\bev_[A-Za-z0-9_-]+\b/g) ?? []
      const citedRefs = new Set([
        ...block.evidenceRefs,
        ...textualRefs.filter((evidenceId) => evidenceIds.has(evidenceId)),
      ])
      for (const evidenceId of citedRefs) {
        const existing = usage.get(evidenceId)
        if (!existing) usage.set(evidenceId, [block.id])
        else if (!existing.includes(block.id)) existing.push(block.id)
      }
    }
  }
  visit(blocks)
  return usage
}

/** 报告块在 DOM 中的锚点 ID。 */
export function reportBlockAnchor(blockId: string): string {
  return 'report-block-' + blockId
}
