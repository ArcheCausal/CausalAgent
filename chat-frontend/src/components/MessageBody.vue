<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../renderers/markdown-adapter'
import CausalGraph from './CausalGraph.vue'
import ReportRenderer from './ReportRenderer.vue'
import type { CausalGraphData, MessageText, StructuredMessage } from '../types/domain'

const props = defineProps<{ text: MessageText }>()
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
</script>

<template>
  <ReportRenderer v-if="isReportDocument" :document="structured?.document" />
  <div v-else-if="isGraph" class="report-content causal-report">
    <div v-if="structured?.summary" class="markdown-content" v-html="summaryHtml"></div>
    <CausalGraph v-if="graph" :graph="graph" />
  </div>
  <div v-else-if="structured?.summary !== undefined || typeof text === 'string'" class="markdown-content" :class="{ 'causal-report': isReport }" v-html="summaryHtml"></div>
  <pre v-else class="unknown-message">{{ JSON.stringify(text, null, 2) }}</pre>
</template>
