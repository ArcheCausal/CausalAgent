import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyTextToClipboard, reportToMarkdown } from '../../src/renderers/report-clipboard'
import { parseReportDocument } from '../../src/renderers/report-document'
import type { ReportDocumentView } from '../../src/renderers/report-document'

function minimalReport(overrides: Partial<ReportDocumentView> = {}): ReportDocumentView {
  return {
    reportId: 'report_test',
    title: '因果分析报告',
    blocks: [
      {
        kind: 'section',
        id: 'section_summary',
        title: '结论摘要',
        children: [{ kind: 'markdown', id: 'markdown_summary', content: '变量 X 与 Y 呈正相关。', evidenceRefs: [] }],
      },
      { kind: 'chart', id: 'chart_age', title: '年龄分布', asset: null, error: '图表数据不可用。' },
      { kind: 'causal_graph', id: 'graph_dag', title: '因果图', graph: null, error: '因果图数据不可用。' },
      { kind: 'unknown', id: 'metric_cards', blockType: 'metric_cards' },
    ],
    sources: [
      { source_id: 'src_file', kind: 'file', title: 'data.csv', file_id: 12, url: null },
      { source_id: 'src_web', kind: 'web', title: 'Causality', file_id: null, url: 'https://example.com/causality' },
    ],
    evidenceRefs: [],
    ...overrides,
  }
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'clipboard')
  Reflect.deleteProperty(document, 'execCommand')
})

describe('reportToMarkdown', () => {
  it('projects title, sections, body, graphic placeholders and sources', () => {
    expect(reportToMarkdown(minimalReport())).toBe(
      '# 因果分析报告\n'
      + '\n## 结论摘要\n'
      + '\n变量 X 与 Y 呈正相关。\n'
      + '\n[图表：年龄分布]\n'
      + '\n[因果图：因果图]\n'
      + '\n## 来源\n'
      + '- data.csv\n'
      + '- [Causality](https://example.com/causality)\n',
    )
  })

  it('skips unknown blocks and untitled sections but keeps their children', () => {
    const markdown = reportToMarkdown(minimalReport({
      blocks: [
        { kind: 'unknown', id: 'metric_cards', blockType: 'metric_cards' },
        { kind: 'section', id: 'section_plain', title: null, children: [{ kind: 'markdown', id: 'body', content: '正文', evidenceRefs: [] }] },
      ],
      sources: [],
    }))

    expect(markdown).toBe('# 因果分析报告\n\n正文\n')
  })

  it('nests section headings and stops at the sixth level', () => {
    let deep: ReportDocumentView['blocks'] = [{ kind: 'markdown', id: 'leaf', content: '末级正文', evidenceRefs: [] }]
    for (let level = 6; level >= 1; level -= 1) {
      deep = [{ kind: 'section', id: `section_${level}`, title: `第 ${level} 层`, children: deep }]
    }

    const markdown = reportToMarkdown(minimalReport({ blocks: deep, sources: [] }))

    expect(markdown.split('\n').filter((line) => line.startsWith('#'))).toEqual([
      '# 因果分析报告',
      '## 第 1 层',
      '### 第 2 层',
      '#### 第 3 层',
      '##### 第 4 层',
      '###### 第 5 层',
      '###### 第 6 层',
    ])
  })

  it('keeps the parsed report document and its markdown body consistent', () => {
    const parsed = parseReportDocument({
      schema_version: 1,
      report_id: 'report_parsed',
      title: '解析后的报告',
      blocks: [{ id: 'markdown_body', type: 'markdown', content: '| 变量 | 结论 |\n| --- | --- |\n| age | 正相关 |', evidence_refs: ['ev_1'] }],
      assets: {},
      sources: [{ source_id: 'src_kb', kind: 'knowledge_base', title: 'Pearl_2009_Causality.pdf' }],
      evidence_refs: [{ evidence_id: 'ev_1', source_ids: ['src_kb'], description: '倾向得分方法可用于调整估计量。' }],
    })

    expect(parsed).not.toBeNull()
    const markdown = reportToMarkdown(parsed as ReportDocumentView)

    expect(markdown).toContain('| age | 正相关 |')
    expect(markdown).toContain('- Pearl_2009_Causality.pdf')
    expect(markdown).not.toContain('倾向得分方法可用于调整估计量。')
  })
})

describe('copyTextToClipboard', () => {
  it('writes through the clipboard API when it is available', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    await copyTextToClipboard('# 报告')

    expect(writeText).toHaveBeenCalledWith('# 报告')
  })

  it('falls back to selecting a temporary textarea outside a secure context', async () => {
    const execCommand = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })

    await copyTextToClipboard('# 报告')

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('rejects when neither clipboard path is usable', async () => {
    await expect(copyTextToClipboard('# 报告')).rejects.toThrow('clipboard_unavailable')
  })

  it('falls back to selecting a temporary textarea when the clipboard API refuses the write', async () => {
    const writeText = vi.fn(() => Promise.reject(new Error('denied')))
    const execCommand = vi.fn(() => true)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })

    await copyTextToClipboard('# 报告')

    expect(writeText).toHaveBeenCalledWith('# 报告')
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('rejects when both clipboard paths refuse the write', async () => {
    const writeText = vi.fn(() => Promise.reject(new Error('denied')))
    const execCommand = vi.fn(() => false)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    Object.defineProperty(document, 'execCommand', { value: execCommand, configurable: true })

    await expect(copyTextToClipboard('# 报告')).rejects.toThrow('clipboard_unavailable')
  })
})
