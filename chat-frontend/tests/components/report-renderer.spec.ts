import { mount } from '@vue/test-utils'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import ReportRenderer from '../../src/components/ReportRenderer.vue'
import markedSource from '../../public/vendor/marked.min.js?raw'

/*
 * 用仓库里 vendored 的真实 marked 预置 window.marked，验证标记文本块的渲染链路，
 * 而不是替换成一个只满足断言的假解析器。
 */
beforeAll(() => {
  new Function(markedSource).call(window)
})

afterEach(() => {
  vi.useRealTimers()
  Reflect.deleteProperty(navigator, 'clipboard')
})

function histogramAsset() {
  return {
    asset_key: 'chart_histogram_age',
    type: 'chart',
    chart_type: 'histogram',
    data: { bins: [20, 30, 40, 50], counts: [3, 5, 2] },
    metadata: { variable: 'age' },
    options: { show_tooltip: true },
  }
}

function documentPayload(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    report_id: 'report_test',
    title: '因果分析报告',
    blocks: [
      {
        id: 'section_summary',
        type: 'section',
        title: '结论摘要',
        children: [
          {
            id: 'markdown_summary',
            type: 'markdown',
            content: '## 结论\n\n- 变量 X 与变量 Y 呈正相关\n- 该结论基于 1200 条样本\n\n1. 先做预处理\n2. 再做因果检验\n\n| 变量 | 结论 |\n| --- | --- |\n| age | 正相关 |',
            evidence_refs: ['ev_1'],
          },
        ],
      },
      { id: 'chart_age', type: 'chart', title: '年龄分布', asset_key: 'chart_histogram_age' },
    ],
    assets: { chart_histogram_age: histogramAsset() },
    sources: [{ source_id: 'src_1', kind: 'file', title: 'data.csv', file_id: 12 }],
    evidence_refs: [{ evidence_id: 'ev_1', source_ids: ['src_1'], description: '年龄字段的相关性统计结果' }],
    ...overrides,
  }
}

describe('ReportRenderer', () => {
  it('renders the report title, markdown lists and tables without inline evidence text', () => {
    const wrapper = mount(ReportRenderer, { props: { document: documentPayload() } })

    expect(wrapper.get('.report-title').text()).toBe('因果分析报告')
    expect(wrapper.get('.report-section-title').text()).toBe('结论摘要')
    expect(wrapper.findAll('.report-markdown .markdown-content li').map((item) => item.text())).toEqual([
      '变量 X 与变量 Y 呈正相关',
      '该结论基于 1200 条样本',
      '先做预处理',
      '再做因果检验',
    ])
    expect(wrapper.findAll('.report-markdown table td').map((cell) => cell.text())).toContain('正相关')
    expect(wrapper.get('.report-markdown h2').text()).toBe('结论')
    expect(wrapper.get('.report-markdown').text()).not.toContain('年龄字段的相关性统计结果')
    expect(wrapper.get('.report-sources').text()).toContain('data.csv')
    expect(wrapper.get('.report-sources').text()).toContain('年龄字段的相关性统计结果')
    expect(wrapper.get('.report-evidence-jump').text()).toBe('定位正文')
  })

  it('keeps knowledge base citations collapsed until the user expands them', async () => {
    const wrapper = mount(ReportRenderer, {
      props: {
        document: documentPayload({
          sources: [{ source_id: 'src_kb', kind: 'knowledge_base', title: 'Pearl_2009_Causality.pdf' }],
          evidence_refs: [
            {
              evidence_id: 'ev_1',
              source_ids: ['src_kb'],
              locator: { locator: 'Pearl_2009_Causality.pdf#page=372#chunk=unit_x', modality: 'text' },
              description: '类型：text 标题：Pearl_2009_Causality.pdf 倾向得分方法可用于调整估计量。',
            },
          ],
        }),
      },
    })

    expect(wrapper.get('.report-sources').text()).toContain('Pearl_2009_Causality.pdf')
    expect(wrapper.get('.report-evidence-toggle').text()).toBe('')
    expect(wrapper.find('.report-evidence-toggle-icon').exists()).toBe(true)
    expect(wrapper.get('.report-evidence-toggle').attributes('aria-label')).toBe('展开知识库引用')
    expect(wrapper.get('.report-evidence-toggle').attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('.report-evidence-text').exists()).toBe(false)

    await wrapper.get('.report-evidence-toggle').trigger('click')

    expect(wrapper.get('.report-evidence-page').text()).toBe('第 372 页')
    expect(wrapper.get('.report-evidence-text').text()).toBe('倾向得分方法可用于调整估计量。')
    expect(wrapper.get('.report-evidence-toggle').text()).toBe('')
    expect(wrapper.get('.report-evidence-toggle').attributes('aria-label')).toBe('收起知识库引用')
    expect(wrapper.get('.report-evidence-toggle').attributes('aria-expanded')).toBe('true')
  })

  it('recovers a jump target when the model wrote an evidence id in markdown content', () => {
    const wrapper = mount(ReportRenderer, {
      props: {
        document: documentPayload({
          blocks: [
            {
              id: 'markdown_summary',
              type: 'markdown',
              content: '结论正文（证据 ev_1）',
            },
          ],
        }),
      },
    })

    expect(wrapper.get('.report-evidence-jump').text()).toBe('定位正文')
  })

  it('jumps from a source citation back to the citing block', async () => {
    const wrapper = mount(ReportRenderer, {
      props: { document: documentPayload() },
      attachTo: document.body,
    })

    await wrapper.get('.report-evidence-jump').trigger('click')

    const target = document.getElementById('report-block-markdown_summary')
    expect(target?.classList.contains('is-cited-target')).toBe(true)
    wrapper.unmount()
  })

  it('renders a histogram chart from the validated asset', () => {
    const wrapper = mount(ReportRenderer, { props: { document: documentPayload() } })

    expect(wrapper.get('.report-chart-title').text()).toBe('年龄分布')
    const bars = wrapper.findAll('.report-histogram-svg rect')
    expect(bars).toHaveLength(3)
    expect(bars.map((bar) => bar.attributes('opacity'))).toEqual(['0.696', '1', '0.544'])
  })

  it('keeps rendering the rest of the report when a block type is unknown', () => {
    const payload = documentPayload()
    ;(payload.blocks as unknown[]).push({ id: 'metric_cards', type: 'metric_cards', value: 3 })

    const wrapper = mount(ReportRenderer, { props: { document: payload } })

    expect(wrapper.get('.report-unknown-block').text()).toContain('暂时无法显示')
    expect(wrapper.get('.report-title').text()).toBe('因果分析报告')
  })

  it('re-renders the chart when the chart data changes', async () => {
    const wrapper = mount(ReportRenderer, { props: { document: documentPayload() } })
    expect(wrapper.findAll('.report-histogram-svg rect')).toHaveLength(3)

    await wrapper.setProps({
      document: documentPayload({
        assets: {
          chart_histogram_age: {
            ...histogramAsset(),
            data: { bins: [20, 30, 40], counts: [7, 9] },
          },
        },
      }),
    })

    expect(wrapper.findAll('.report-histogram-svg rect')).toHaveLength(2)
  })

  it('shows a controlled message for illegal chart assets', () => {
    const wrapper = mount(ReportRenderer, {
      props: { document: documentPayload({ blocks: [{ id: 'chart_bad', type: 'chart', asset_key: 'missing' }], assets: {} }) },
    })

    expect(wrapper.get('.report-chart-state').text()).toBe('图表数据不可用。')
  })

  it('shows a controlled error instead of crashing on a non-report payload', () => {
    const wrapper = mount(ReportRenderer, { props: { document: { type: 'text', summary: '正文' } } })

    expect(wrapper.get('.report-document-error').text()).toContain('报告内容不可用')
  })

  it('copies the whole report as markdown from the toolbar button', async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const wrapper = mount(ReportRenderer, { props: { document: documentPayload() } })
    const button = wrapper.get('.report-copy')
    expect(button.text()).toBe('复制报告')

    await button.trigger('click')
    await Promise.resolve()

    expect(writeText).toHaveBeenCalledTimes(1)
    const copied = writeText.mock.calls[0]?.[0]
    expect(copied).toBeDefined()
    expect(copied).toContain('# 因果分析报告')
    expect(copied).toContain('## 结论摘要')
    expect(copied).toContain('变量 X 与变量 Y 呈正相关')
    expect(copied).toContain('[图表：年龄分布]')
    expect(copied).toContain('## 来源\n- data.csv')
    expect(wrapper.get('.report-copy').text()).toBe('已复制')
    expect(wrapper.get('.report-copy').classes()).toContain('report-copy-copied')
  })

  it('offers a retry when the clipboard write fails', async () => {
    const writeText = vi.fn(() => Promise.reject(new Error('denied')))
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const wrapper = mount(ReportRenderer, { props: { document: documentPayload() } })

    await wrapper.get('.report-copy').trigger('click')
    await Promise.resolve()

    expect(wrapper.get('.report-copy').text()).toBe('复制失败，请重试')
    expect(wrapper.get('.report-copy').classes()).toContain('report-copy-failed')
    expect(wrapper.get('.report-copy').attributes('disabled')).toBeUndefined()
  })

  it('drops the copy feedback after the two second window', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const wrapper = mount(ReportRenderer, { props: { document: documentPayload() } })

    await wrapper.get('.report-copy').trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.get('.report-copy').text()).toBe('已复制')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.report-copy').text()).toBe('复制报告')
  })

  it('resets the copy feedback when another report replaces the current one', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const wrapper = mount(ReportRenderer, { props: { document: documentPayload() } })
    await wrapper.get('.report-copy').trigger('click')
    await Promise.resolve()
    expect(wrapper.get('.report-copy').text()).toBe('已复制')

    await wrapper.setProps({ document: documentPayload({ report_id: 'report_next', title: '第二份报告' }) })

    expect(wrapper.get('.report-title').text()).toBe('第二份报告')
    expect(wrapper.get('.report-copy').text()).toBe('复制报告')
  })

  it('keeps the copy button out of the unusable report state', () => {
    const wrapper = mount(ReportRenderer, { props: { document: { type: 'text', summary: '正文' } } })

    expect(wrapper.find('.report-copy').exists()).toBe(false)
  })
})
