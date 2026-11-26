import type { ReportBlockView, ReportDocumentView } from './report-document'
import type { ReportSource } from '../types/domain'

/*
 * 报告复制边界：把已经过校验的报告投影成 Markdown 文本。
 * 复制的是读者能看到的结构——标题、章节、正文和来源清单；
 * 图表和因果图是图形资源，文本副本里降级成占位行，
 * 页脚里默认收起的检索证据不进入副本，避免把检索片段当成报告正文。
 */

const MAX_HEADING_LEVEL = 6

function heading(level: number, title: string): string {
  return `${'#'.repeat(Math.min(level, MAX_HEADING_LEVEL))} ${title}`
}

function collectSegments(blocks: ReportBlockView[], level: number, segments: string[]): void {
  for (const block of blocks) {
    if (block.kind === 'section') {
      if (block.title) segments.push(heading(level, block.title))
      collectSegments(block.children, level + 1, segments)
      continue
    }
    if (block.kind === 'markdown') {
      const content = block.content.trim()
      if (content) segments.push(content)
      continue
    }
    if (block.kind === 'chart') {
      segments.push(block.title ? `[图表：${block.title}]` : '[图表]')
      continue
    }
    if (block.kind === 'causal_graph') {
      segments.push(block.title ? `[因果图：${block.title}]` : '[因果图]')
    }
    // 未知块在页面里本来就只显示占位提示，副本里不保留。
  }
}

function sourceLine(source: ReportSource): string {
  return source.url ? `- [${source.title}](${source.url})` : `- ${source.title}`
}

/** 把报告投影成可直接粘贴的 Markdown 文本。 */
export function reportToMarkdown(report: ReportDocumentView): string {
  const segments: string[] = [heading(1, report.title)]
  collectSegments(report.blocks, 2, segments)
  if (report.sources.length) {
    segments.push([heading(2, '来源'), ...report.sources.map(sourceLine)].join('\n'))
  }
  return `${segments.join('\n\n')}\n`
}

function copyWithSelection(text: string): boolean {
  if (typeof document.execCommand !== 'function') return false
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.setAttribute('aria-hidden', 'true')
  area.style.position = 'fixed'
  area.style.top = '0'
  area.style.left = '0'
  area.style.width = '1px'
  area.style.height = '1px'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.focus()
  area.select()
  area.setSelectionRange(0, text.length)
  try {
    return document.execCommand('copy')
  } finally {
    document.body.removeChild(area)
  }
}

/**
 * 写入剪贴板。
 *
 * 优先使用异步剪贴板 API；被权限策略拒绝或文档失焦时继续尝试选中复制，
 * 两条路径都失败才抛错，由调用方展示可重试的失败状态。
 */
export async function copyTextToClipboard(text: string): Promise<void> {
  const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text)
      return
    } catch {
      // 继续走选中复制，不让一次权限拒绝直接变成失败提示。
    }
  }
  if (!copyWithSelection(text)) throw new Error('clipboard_unavailable')
}
