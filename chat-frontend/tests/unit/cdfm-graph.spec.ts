import { describe, expect, it } from 'vitest'
import { causalGraphCaption, describeCausalGraphEdge, projectCausalGraphForVis } from '../../src/renderers/graph-renderer'
import { parseReportDocument, projectCausalGraphModel } from '../../src/renderers/report-document'
import { cdfmGraph } from '../helpers/cdfm-graph'

describe('CDFM graph contract parsing', () => {
  it('preserves both probabilities, weight and diagnostics at full precision through report serialization', () => {
    const input = cdfmGraph()
    const serialized = JSON.stringify({
      schema_version: 1, report_id: 'cdfm_report', title: '契约输入',
      blocks: [{ id: 'main', type: 'causal_graph', asset_key: input.graph_id }],
      assets: { [input.graph_id]: input },
    })
    const raw: unknown = JSON.parse(serialized)
    const report = parseReportDocument(raw)
    const block = report?.blocks[0]
    expect(block?.kind).toBe('causal_graph')
    if (block?.kind !== 'causal_graph' || !block.graph) throw new Error('图资源未解析')
    expect(block.graph).toEqual(input)
    expect(projectCausalGraphForVis(block.graph).edges[0]?.label).toBe('p=0.8765')
    expect(block.graph.edges[0]?.weight).toBeNull()
  })

  it.each(['forward_probability', 'reverse_probability'] as const)('rejects invalid %s without accepting a partial prediction', (field) => {
    for (const value of [-0.001, 1.001, NaN, Infinity, -Infinity, '0.5', undefined, null]) {
      const input = cdfmGraph()
      const edge = input.edges[0]!
      const raw = { ...input, edges: [{ ...edge, prediction: { ...edge.prediction, [field]: value } }] }
      expect(projectCausalGraphModel(raw), `${field}=${String(value)}`).toBeNull()
    }
  })

  it('accepts inclusive probability boundaries and never copies them into weight', () => {
    const input = cdfmGraph()
    input.edges[0]!.prediction = { forward_probability: 0, reverse_probability: 1 }
    expect(projectCausalGraphModel(input)?.edges[0]).toMatchObject({
      prediction: { forward_probability: 0, reverse_probability: 1 }, weight: null,
    })
    expect(projectCausalGraphForVis(input).edges[0]?.label).toBe('p=0')
  })

  it('keeps an independently supplied weight separate from prediction', () => {
    const input = cdfmGraph()
    input.edges[0]!.weight = -0.42
    const graph = projectCausalGraphModel(input)
    expect(graph?.edges[0]).toMatchObject({ weight: -0.42, prediction: input.edges[0]!.prediction })
    expect(graph && projectCausalGraphForVis(graph).edges[0]?.label).toBe('p=0.8765')
  })

  it.each(['causal.cdfm', 'causal.lingam', 'causal_pc'])('accepts serialized prediction:null for %s without inventing probabilities', (algorithm) => {
    const input = cdfmGraph()
    input.metadata = { algorithm }
    input.edges.forEach((edge) => { edge.prediction = null })
    input.edges[0]!.weight = algorithm === 'causal.cdfm' ? null : -0.42
    const raw: unknown = JSON.parse(JSON.stringify(input))
    const graph = projectCausalGraphModel(raw)
    expect(graph).not.toBeNull()
    expect(graph?.edges[0]).not.toHaveProperty('prediction')
    if (!graph) throw new Error('prediction:null 不应导致图资源失效')
    const projected = projectCausalGraphForVis(graph)
    if (algorithm === 'causal.cdfm') expect(projected.edges[0]).not.toHaveProperty('label')
    else expect(projected.edges[0]?.label).toBe('-0.42')
    expect(causalGraphCaption(graph)).toBeNull()
  })

  it('does not invent probabilities for legacy edges or copy unknown edge logits into the model', () => {
    const input = cdfmGraph()
    const edge = input.edges[0]!
    delete edge.prediction
    const graph = projectCausalGraphModel({ ...input, edges: [{ ...edge, logits: [1, 2] }] })
    expect(graph?.edges[0]).not.toHaveProperty('prediction')
    expect(graph?.edges[0]).not.toHaveProperty('logits')
    expect(graph && projectCausalGraphForVis(graph).edges[0]).not.toHaveProperty('label')
  })

  it('contains invalid predictions within the graph error boundary', () => {
    const input = cdfmGraph()
    input.edges[0]!.prediction!.forward_probability = Infinity
    const report = parseReportDocument({
      schema_version: 1, report_id: 'invalid_prediction', title: '契约输入',
      blocks: [
        { id: 'main', type: 'causal_graph', asset_key: input.graph_id },
        { id: 'body', type: 'markdown', content: '其他内容保留' },
      ],
      assets: { [input.graph_id]: input },
    })
    expect(report?.blocks[0]).toMatchObject({ graph: null, error: '因果图数据不可用。' })
    expect(report?.blocks[1]).toMatchObject({ kind: 'markdown', content: '其他内容保留' })
  })
})

describe('CDFM graph presentation', () => {
  it('uses source→target probability for directed edges and preserves the two bidirected encodings', () => {
    const input = cdfmGraph()
    const original = structuredClone(input)
    const projected = projectCausalGraphForVis(input)
    expect(projected.edges[0]).toMatchObject({
      from: 'node_x', to: 'node_y', arrows: 'to', label: 'p=0.8765', dashes: false,
    })
    expect(projected.edges[1]).toMatchObject({
      from: 'node_y', to: 'node_x', arrows: 'to,from', label: 'p₁=0.9877\np₂=0.7654', dashes: false,
    })
    expect(input).toEqual(original)
  })

  it('explains the two encodings using the selected edge endpoints rather than node order', () => {
    const input = cdfmGraph()
    expect(describeCausalGraphEdge(input, 'edge_directed')).toBe('边：变量 X → 结果 Y（p=0.8765，模型预测概率）')
    const selected = describeCausalGraphEdge(input, 'edge_bidirected')
    expect(selected).toContain('边：结果 Y ↔ 变量 X')
    expect(selected).toContain('source→target（结果 Y → 变量 X）p₁=0.9877')
    expect(selected).toContain('target→source（变量 X → 结果 Y）p₂=0.7654')
    expect(selected).toContain('共同编码潜在共同原因候选，不代表两条因果方向')
    expect(describeCausalGraphEdge(input, 'unknown')).toBeNull()
  })

  it('explains prediction semantics and displays the actual nested threshold with rounding', () => {
    const input = cdfmGraph()
    const caption = causalGraphCaption(input)
    expect(caption).toContain('CDFM 提供候选结构及模型预测概率')
    expect(caption).toContain('与结构系数和因果效应不同')
    expect(caption).toContain('p₁ 和 p₂ 分别是 source→target 和 target→source')
    expect(caption).toContain('本次实际阈值：0.6123')
    expect(caption).toContain('数值仅作显示舍入')
    expect(input.metadata?.diagnostics).toMatchObject({ metrics: { threshold: 0.6123456789012345 } })
  })

  it('does not substitute weight or threshold for a missing prediction on an individual edge', () => {
    const input = cdfmGraph()
    delete input.edges[0]!.prediction
    input.edges[0]!.weight = 0.25
    expect(projectCausalGraphForVis(input).edges[0]?.label).toBe('0.25')
    expect(describeCausalGraphEdge(input, 'edge_directed')).toBe('边：node_x → node_y（0.25）')
    expect(projectCausalGraphForVis(input).edges[1]?.label).toContain('p₁=')
  })

  it.each(['causal.lingam', 'causal_pc', undefined])('keeps weight semantics when algorithm is %s', (algorithm) => {
    const input = cdfmGraph()
    input.metadata = { ...input.metadata, algorithm }
    input.edges[0]!.weight = -0.123456789
    expect(projectCausalGraphForVis(input).edges[0]?.label).toBe('-0.123457')
    expect(projectCausalGraphForVis(input).edges[1]).not.toHaveProperty('label')
    expect(describeCausalGraphEdge(input, 'edge_directed')).toBe('边：node_x → node_y（-0.123457）')
    expect(causalGraphCaption(input)).toBeNull()
  })

  it('keeps legacy CDFM reports without new fields free of invented labels and captions', () => {
    const input = cdfmGraph()
    input.edges.forEach((edge) => { delete edge.prediction })
    input.metadata = { algorithm: 'causal.cdfm' }
    expect(projectCausalGraphForVis(input).edges.every((edge) => edge.label === undefined)).toBe(true)
    expect(causalGraphCaption(input)).toBeNull()
    expect(describeCausalGraphEdge(input, 'edge_bidirected')).toBe('边：node_y ↔ node_x')
  })

  it('does not invent an absent or invalid threshold, and accepts a threshold of zero', () => {
    const input = cdfmGraph()
    for (const diagnostics of [undefined, null, [], {}, { metrics: null }, { metrics: {} }, { metrics: { threshold: '0.5' } }, { metrics: { threshold: NaN } }, { metrics: { threshold: Infinity } }]) {
      input.metadata = { algorithm: 'causal.cdfm', diagnostics }
      expect(causalGraphCaption(input)).not.toContain('本次实际阈值')
    }
    input.metadata = { algorithm: 'causal.cdfm', diagnostics: { metrics: { threshold: 0 } } }
    expect(causalGraphCaption(input)).toContain('本次实际阈值：0。')
    input.edges.forEach((edge) => { delete edge.prediction })
    expect(causalGraphCaption(input)).toBe('本次实际阈值：0。 数值仅作显示舍入。')
  })

  it('retains significant digits for tiny probabilities instead of rounding them to zero', () => {
    const input = cdfmGraph()
    input.edges[0]!.prediction!.forward_probability = 1.23456789e-9
    expect(projectCausalGraphForVis(input).edges[0]?.label).toBe('p=1.235e-9')
  })
})
