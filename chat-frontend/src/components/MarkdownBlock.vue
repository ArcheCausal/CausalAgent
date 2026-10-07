<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../renderers/markdown-adapter'

const props = defineProps<{ content: string }>()

// Markdown 解析只经过适配器；报告文本块复用普通聊天的同一套 marked 行为。
const html = computed(() => renderMarkdown(props.content))
</script>

<template>
  <div class="report-markdown">
    <div class="markdown-content" v-html="html"></div>
  </div>
</template>

<style scoped>
.report-markdown {
  width: 100%;
  min-width: 0;
  font-size: var(--report-text-size, 15px);
  line-height: 1.65;
}

.report-markdown :deep(.markdown-content) {
  width: 100%;
  min-width: 0;
  overflow-x: auto;
  overflow-wrap: anywhere;
}

.report-markdown :deep(h1),
.report-markdown :deep(h2),
.report-markdown :deep(h3),
.report-markdown :deep(h4),
.report-markdown :deep(th) {
  font-weight: 400;
}

.report-markdown :deep(p) {
  width: 100%;
  max-width: none;
  margin: 0 0 12px;
}

.report-markdown :deep(table) {
  display: table;
  width: 100%;
  min-width: 100%;
  max-width: 100%;
  table-layout: auto;
  border-collapse: collapse;
}

.report-markdown :deep(th),
.report-markdown :deep(td) {
  padding: 12px 8px 12px 0;
  text-align: left;
  border-top: 1px solid var(--report-border, #e2e5e9);
}

.report-markdown :deep(thead th) {
  color: var(--color-text-muted, #6f6f6f);
  font-size: 12px;
  border-top: 0;
}

.report-markdown :deep(tbody th) {
  color: var(--color-text-muted, #6f6f6f);
}

.report-markdown :deep(pre) {
  max-width: 100%;
  padding: 10px 12px;
  overflow-x: auto;
  background: var(--report-code-background, #eef1f4);
  border-radius: 6px;
}

.report-markdown :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  color: var(--color-text-muted, #6b7280);
  border-left: 3px solid var(--report-border, #e2e5e9);
}

</style>
