import { evidencePage, evidenceSnippet, parseReportDocument, type ReportBlockView } from './report-document'

export function markdownJson(value: unknown): string {
  return ['```json', JSON.stringify(value, null, 2), '```'].join('\n')
}

function inlineText(value: string): string {
  return value.replace(/[\r\n]+/g, ' ').replace(/[\\`*_{}[\]<>()~#!]/g, '\\$&')
}

function heading(title: string, level: number): string {
  return `${'#'.repeat(Math.min(level, 6))} ${inlineText(title)}`
}

function blockMarkdown(block: ReportBlockView, level: number): string {
  switch (block.kind) {
    case 'section':
      return [
        block.title && heading(block.title, level),
        ...block.children.map((child) => blockMarkdown(child, block.title ? level + 1 : level)),
      ].filter(Boolean).join('\n\n')
    case 'markdown':
      return block.content
    case 'chart':
      return [
        heading(block.title ?? '数据图表', level),
        block.asset ? markdownJson({ chart_type: block.asset.chart_type, data: block.asset.data }) : block.error,
      ].join('\n\n')
    case 'causal_graph':
      return [
        heading(block.title ?? '因果图', level),
        block.graph ? markdownJson({ nodes: block.graph.nodes, edges: block.graph.edges }) : block.error,
      ].join('\n\n')
    case 'unknown':
      return '这一部分报告内容暂时无法显示。'
  }
}

export function reportMarkdown(document: unknown): string | null {
  const report = parseReportDocument(document)
  if (!report) return null

  const sources = report.sources.map((source) => {
    const title = inlineText(source.title)
    const url = source.url?.replace(/[<>\s]/g, (character) => encodeURIComponent(character))
    const link = url && /^https?:\/\//i.test(url) ? `[${title}](<${url}>)` : title
    const evidence = report.evidenceRefs
      .filter((item) => item.source_ids?.includes(source.source_id))
      .map((item) => {
        const page = evidencePage(item.locator)
        const description = inlineText(evidenceSnippet(item.description, source.title, Infinity))
        return `  - ${inlineText(item.evidence_id)}${page ? `（第 ${page} 页）` : ''} ${description}`
      })
    return [`- ${link}`, ...evidence].join('\n')
  })

  return [
    report.title && heading(report.title, 1),
    ...report.blocks.map((block) => blockMarkdown(block, 2)),
    sources.length ? ['## 来源', sources.join('\n')].join('\n\n') : '',
  ].filter((part) => part.trim()).join('\n\n')
}
