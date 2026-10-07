import { describe, expect, it } from 'vitest'
import { parseReportDocument, projectCausalGraphModel, projectChartAsset } from '../../src/renderers/report-document'

function reportDocument(blocks: unknown[], assets: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    report_id: 'report_test',
    title: '因果分析报告',
    blocks,
    assets,
    sources: [],
    evidence_refs: [],
  }
}

const histogramAsset = {
  asset_key: 'chart_histogram_age',
  type: 'chart',
  chart_type: 'histogram',
  data: { bins: [0, 1, 2, 3], counts: [4, 5, 6] },
  metadata: { variable: 'age' },
  options: { show_tooltip: true },
}

const graphAsset = {
  graph_id: 'graph_main',
  schema_version: 1,
  nodes: [
    { id: 'node_age', variable: 'age', label: 'age' },
    { id: 'node_income', variable: 'income', label: 'income' },
  ],
  edges: [
    { id: 'edge_age_income', source: 'node_age', target: 'node_income', edge_type: 'directed', weight: 0.42 },
  ],
  metadata: { algorithm: 'causal_pc', graph_source: 'postprocessed' },
}

describe('parseReportDocument', () => {
  it('dispatches section, markdown, chart and causal_graph blocks', () => {
    const parsed = parseReportDocument(reportDocument(
      [
        {
          id: 'section_summary',
          type: 'section',
          title: '结论摘要',
          children: [{ id: 'markdown_summary', type: 'markdown', content: '- a\n- b', evidence_refs: ['ev_1'] }],
        },
        { id: 'chart_age', type: 'chart', title: '年龄分布', asset_key: 'chart_histogram_age' },
        { id: 'graph_main_block', type: 'causal_graph', title: '主要因果关系', asset_key: 'graph_main' },
      ],
      { chart_histogram_age: histogramAsset, graph_main: graphAsset },
    ))

    expect(parsed?.blocks.map((block) => block.kind)).toEqual(['section', 'chart', 'causal_graph'])
    const section = parsed?.blocks[0]
    expect(section?.kind === 'section' ? section.children[0]?.kind : null).toBe('markdown')
    const chart = parsed?.blocks[1]
    expect(chart?.kind === 'chart' ? chart.asset?.chart_type : null).toBe('histogram')
    const graph = parsed?.blocks[2]
    expect(graph?.kind === 'causal_graph' ? graph.graph?.nodes.length : null).toBe(2)
  })

  it('degrades unknown block types without dropping the rest of the report', () => {
    const parsed = parseReportDocument(reportDocument([
      { id: 'mystery', type: 'metric_cards', value: 1 },
      { id: 'markdown_summary', type: 'markdown', content: '正文' },
    ]))

    expect(parsed?.blocks.map((block) => block.kind)).toEqual(['unknown', 'markdown'])
  })

  it('marks illegal asset references as controlled errors', () => {
    const parsed = parseReportDocument(reportDocument(
      [
        { id: 'chart_missing', type: 'chart', asset_key: 'chart_unknown' },
        { id: 'graph_missing', type: 'causal_graph', asset_key: 'graph_unknown' },
      ],
      { chart_other: { asset_key: 'chart_other', type: 'chart', chart_type: 'bar', data: {} } },
    ))

    const chart = parsed?.blocks[0]
    expect(chart?.kind === 'chart' ? chart.error : null).toBe('图表数据不可用。')
    const graph = parsed?.blocks[1]
    expect(graph?.kind === 'causal_graph' ? graph.error : null).toBe('因果图数据不可用。')
  })

  it('keeps valid sources and evidence and drops malformed ones', () => {
    const parsed = parseReportDocument({
      ...reportDocument([]),
      sources: [
        { source_id: 'src_1', kind: 'file', title: 'data.csv', file_id: 12 },
        { source_id: 5, kind: 'file', title: 'bad' },
      ],
      evidence_refs: [
        { evidence_id: 'ev_1', source_ids: ['src_1'], description: 'age 与 income 的相关性' },
        { evidence_id: 'ev_2' },
      ],
    })

    expect(parsed?.sources).toEqual([
      { source_id: 'src_1', kind: 'file', title: 'data.csv', file_id: 12, url: null },
    ])
    expect(parsed?.evidenceRefs).toEqual([
      { evidence_id: 'ev_1', source_ids: ['src_1'], locator: undefined, description: 'age 与 income 的相关性' },
    ])
  })

  it('returns null for payloads that are not report documents', () => {
    expect(parseReportDocument(null)).toBeNull()
    expect(parseReportDocument({ type: 'text', summary: '正文' })).toBeNull()
  })
})

describe('projectChartAsset', () => {
  it('accepts histogram, bar and heatmap data with consistent shapes', () => {
    expect(projectChartAsset(histogramAsset)?.data.bins).toEqual([0, 1, 2, 3])
    expect(projectChartAsset({
      asset_key: 'chart_bar_city',
      type: 'chart',
      chart_type: 'bar',
      data: { categories: ['北京', '上海'], counts: [3, 4] },
    })?.data.counts).toEqual([3, 4])
    expect(projectChartAsset({
      asset_key: 'chart_heatmap_correlation',
      type: 'chart',
      chart_type: 'heatmap',
      data: { variables: ['age', 'income'], matrix: [[1, 0.4], [0.4, 1]] },
    })?.data.variables).toEqual(['age', 'income'])
  })

  it('rejects unknown chart types and inconsistent data', () => {
    expect(projectChartAsset({ asset_key: 'c', type: 'chart', chart_type: 'pie', data: {} })).toBeNull()
    expect(projectChartAsset({ ...histogramAsset, data: { bins: [0, 1, 2], counts: [1] } })).toBeNull()
    expect(projectChartAsset({
      asset_key: 'c',
      type: 'chart',
      chart_type: 'heatmap',
      data: { variables: ['a', 'b'], matrix: [[1, 2]] },
    })).toBeNull()
  })
})

describe('projectCausalGraphModel', () => {
  it('keeps node and edge identity from the business model', () => {
    const graph = projectCausalGraphModel(graphAsset)
    expect(graph?.graph_id).toBe('graph_main')
    expect(graph?.nodes[0]).toEqual({ id: 'node_age', variable: 'age', label: 'age' })
    expect(graph?.edges[0]?.weight).toBe(0.42)
  })

  it('rejects graphs without node or edge identity', () => {
    expect(projectCausalGraphModel({ graph_id: 'g', schema_version: 1, nodes: [], edges: [] })).toBeNull()
    expect(projectCausalGraphModel({ graph_id: 'g', schema_version: 1 })).toBeNull()
  })
})
