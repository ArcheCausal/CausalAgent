import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { describe, expect, it } from 'vitest'
import CausalGraphBlock from '../../src/components/CausalGraphBlock.vue'
import ReportRenderer from '../../src/components/ReportRenderer.vue'
import { cdfmGraph } from '../helpers/cdfm-graph'

// 使用真实 Vue 组件与 SSR，验证模板文字和资源传递；不触发 canvas 挂载或替换 vis-network。
// 选边文本的端点映射由纯函数测试覆盖；这里不声称验证浏览器点击与实际绘图。
describe('CDFM graph components', () => {
  it('renders the probability explanation and threshold beside the existing single graph', async () => {
    const html = await renderToString(createSSRApp({
      render: () => h(CausalGraphBlock, { title: '主要因果关系', graph: cdfmGraph(), error: null }),
    }))
    expect(html).toContain('主要因果关系')
    expect(html).toContain('report-graph-caption')
    expect(html).toContain('CDFM 提供候选结构及模型预测概率')
    expect(html).toContain('本次实际阈值：0.6123')
    expect(html).toContain('共同编码潜在共同原因候选，不代表两条因果方向')
    expect(html.match(/aria-label="因果关系图"/g)).toHaveLength(1)
    expect(html).not.toContain('report-graph-selection')
  })

  it('passes a serialized report asset through the actual report components', async () => {
    const input = cdfmGraph()
    const document: unknown = JSON.parse(JSON.stringify({
      schema_version: 1, report_id: 'cdfm_components', title: '报告组件契约',
      blocks: [{ id: 'main', type: 'causal_graph', asset_key: input.graph_id }],
      assets: { [input.graph_id]: input },
    }))
    const html = await renderToString(createSSRApp({ render: () => h(ReportRenderer, { document }) }))
    expect(html).toContain('报告组件契约')
    expect(html).toContain('本次实际阈值：0.6123')
    expect(html).toContain('p₁ 和 p₂ 分别是 source→target 和 target→source')
    expect(html.match(/aria-label="因果关系图"/g)).toHaveLength(1)
  })

  it('does not describe bidirected encodings for a graph containing only directed predictions', async () => {
    const input = cdfmGraph()
    input.edges = [input.edges[0]!]
    const html = await renderToString(createSSRApp({
      render: () => h(CausalGraphBlock, { title: null, graph: input, error: null }),
    }))
    expect(html).toContain('p 为模型预测概率')
    expect(html).not.toContain('双箭头')
  })

  it.each(['causal.cdfm', 'causal.lingam'])('renders a reserialized %s report with prediction:null', async (algorithm) => {
    const input = cdfmGraph()
    input.metadata = { algorithm }
    input.edges.forEach((edge) => { edge.prediction = null })
    input.edges[0]!.weight = algorithm === 'causal.cdfm' ? null : 0.42
    const document: unknown = JSON.parse(JSON.stringify({
      schema_version: 1, report_id: 'nullable_prediction', title: '兼容报告',
      blocks: [{ id: 'main', type: 'causal_graph', asset_key: input.graph_id }],
      assets: { [input.graph_id]: input },
    }))
    const html = await renderToString(createSSRApp({ render: () => h(ReportRenderer, { document }) }))
    expect(html.match(/aria-label="因果关系图"/g)).toHaveLength(1)
    expect(html).not.toContain('因果图数据不可用')
    expect(html).not.toContain('report-graph-caption')
  })

  it('leaves legacy CDFM graphs and other algorithms without a probability caption', async () => {
    const legacy = cdfmGraph()
    legacy.edges.forEach((edge) => { delete edge.prediction })
    legacy.metadata = { algorithm: 'causal.cdfm' }
    const other = cdfmGraph()
    other.metadata = { ...other.metadata, algorithm: 'causal.lingam' }
    other.edges[0]!.weight = 0.42
    for (const graph of [legacy, other]) {
      const html = await renderToString(createSSRApp({
        render: () => h(CausalGraphBlock, { title: null, graph, error: null }),
      }))
      expect(html).not.toContain('report-graph-caption')
      expect(html).not.toContain('本次实际阈值')
      expect(html.match(/aria-label="因果关系图"/g)).toHaveLength(1)
    }
  })

  it('keeps the controlled error state without rendering a caption or graph', async () => {
    const html = await renderToString(createSSRApp({
      render: () => h(CausalGraphBlock, { title: null, graph: cdfmGraph(), error: '因果图数据不可用。' }),
    }))
    expect(html).toContain('因果图数据不可用。')
    expect(html).not.toContain('report-graph-caption')
    expect(html).not.toContain('aria-label="因果关系图"')
  })
})
