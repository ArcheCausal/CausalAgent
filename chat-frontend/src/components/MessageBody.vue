<script setup lang="ts">
import { Copy } from '@lucide/vue'
import { computed } from 'vue'
import { useLocale } from '../i18n/use-locale'
import { renderMarkdown } from '../renderers/markdown-adapter'
import { markdownJson, reportMarkdown } from '../renderers/report-markdown'
import CausalGraph from './CausalGraph.vue'
import ReportRenderer from './ReportRenderer.vue'
import type { CausalGraphData, MessageText, StructuredMessage } from '../types/domain'

const props = defineProps<{ text: MessageText; copyText?: string }>()
const emit = defineEmits<{ 'copy-result': [success: boolean] }>()
const { text: labels } = useLocale()
const structured = computed<StructuredMessage | null>(() => typeof props.text === 'string' ? null : props.text)
// 结构化报告由专用渲染器接管；MessageBody 不再拼接报告 HTML。
const isReportDocument = computed(() => structured.value?.type === 'report'
  && structured.value.render_mode === 'structured'
  && structured.value.document !== undefined)
const isGraph = computed(() => {
  const data = structured.value?.data
  return structured.value?.type === 'causal_graph'
    && typeof data === 'object'
    && data !== null
    && Array.isArray((data as Record<string, unknown>).nodes)
    && Array.isArray((data as Record<string, unknown>).edges)
})
const graph = computed<CausalGraphData | null>(() => {
  if (!isGraph.value || !structured.value?.data) return null
  const data = structured.value.data as { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> }
  return { nodes: data.nodes, edges: data.edges }
})
const isReport = computed(() => structured.value?.layout === 'report' || structured.value?.type === 'causal_graph')
const summaryHtml = computed(() => {
  if (typeof props.text === 'string') return renderMarkdown(props.text)
  return renderMarkdown(props.text.summary ?? '')
})
const clipboardText = computed(() => {
  const message = props.text
  if (props.copyText !== undefined) return props.copyText
  if (typeof message === 'string') return message
  if (isReportDocument.value) return reportMarkdown(message.document) ?? message.summary ?? ''
  if (graph.value) {
    return [message.summary, markdownJson(graph.value)].filter(Boolean).join('\n\n')
  }
  return message.summary ?? markdownJson(message)
})

function copyWithSelection(value: string): boolean {
  const input = document.createElement('textarea')
  input.value = value
  input.readOnly = true
  input.style.position = 'fixed'
  input.style.opacity = '0'
  const focused = document.activeElement
  document.body.append(input)
  input.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    input.remove()
    if (focused instanceof HTMLElement) focused.focus({ preventScroll: true })
  }
}

async function copyMessage(): Promise<void> {
  const value = clipboardText.value
  if (!value.trim()) {
    emit('copy-result', false)
    return
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      emit('copy-result', true)
      return
    }
  } catch {
    // 部分浏览器禁用剪贴板 API，此时尝试从当前页面选区复制。
  }
  emit('copy-result', copyWithSelection(value))
}
</script>

<template>
  <div class="message-content">
    <ReportRenderer v-if="isReportDocument" :document="structured?.document" />
    <div v-else-if="isGraph" class="report-content causal-report">
      <div v-if="structured?.summary" class="markdown-content" v-html="summaryHtml"></div>
      <CausalGraph v-if="graph" :graph="graph" />
    </div>
    <div v-else-if="structured?.summary !== undefined || typeof text === 'string'" class="markdown-content" :class="{ 'causal-report': isReport }" v-html="summaryHtml"></div>
    <pre v-else class="unknown-message">{{ JSON.stringify(text, null, 2) }}</pre>
  </div>
  <div v-if="clipboardText.trim()" class="message-actions">
    <button type="button" class="message-copy-button" :aria-label="labels.copyMessage" :title="labels.copyMessage" @click="copyMessage">
      <Copy :size="16" :stroke-width="1.75" aria-hidden="true" focusable="false" />
    </button>
  </div>
</template>
