<script setup lang="ts">
import type { ReportBlockView as ReportBlockViewModel } from '../renderers/report-document'
import { reportBlockAnchor } from '../renderers/report-document'
import CausalGraphBlock from './CausalGraphBlock.vue'
import ChartBlock from './ChartBlock.vue'
import MarkdownBlock from './MarkdownBlock.vue'
import ReportSection from './ReportSection.vue'

defineProps<{ block: ReportBlockViewModel }>()
</script>

<template>
  <ReportSection v-if="block.kind === 'section'" :title="block.title">
    <ReportBlockView
      v-for="child in block.children"
      :key="child.id"
      :block="child"
    />
  </ReportSection>
  <div v-else-if="block.kind === 'markdown'" :id="reportBlockAnchor(block.id)" class="report-block">
    <MarkdownBlock :content="block.content" />
  </div>
  <ChartBlock
    v-else-if="block.kind === 'chart'"
    :title="block.title"
    :asset="block.asset"
    :error="block.error"
  />
  <CausalGraphBlock
    v-else-if="block.kind === 'causal_graph'"
    :title="block.title"
    :graph="block.graph"
    :error="block.error"
  />
  <div v-else class="report-unknown-block" role="note">
    这一部分报告内容暂时无法显示。
  </div>
</template>

<style scoped>
.report-block {
  width: 100%;
  min-width: 0;
}

.report-unknown-block {
  padding: 10px 12px;
  font-size: 13px;
  color: var(--color-text-muted, #6b7280);
  background: var(--report-muted-background, #eef1f4);
  border-radius: 6px;
}
</style>
