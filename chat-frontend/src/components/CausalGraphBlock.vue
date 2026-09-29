<script setup lang="ts">
import { computed, ref } from 'vue'
import { projectCausalGraphForVis } from '../renderers/graph-renderer'
import type { CausalGraphModel } from '../types/domain'
import CausalGraph from './CausalGraph.vue'

const props = defineProps<{
  title: string | null
  graph: CausalGraphModel | null
  error: string | null
}>()

const emit = defineEmits<{
  selectNode: [payload: { id: string; label: string }]
  selectEdge: [payload: { id: string; from: string; to: string; label: string }]
}>()

const selection = ref<string | null>(null)
// 组件只处理因果图业务模型；vis-network 字段在投影函数里生成。
const graphData = computed(() => (props.graph ? projectCausalGraphForVis(props.graph) : null))

function onSelectNode(payload: { id: string; label: string }): void {
  selection.value = `节点：${payload.label}`
  emit('selectNode', payload)
}

function onSelectEdge(payload: { id: string; from: string; to: string; label: string }): void {
  selection.value = `边：${payload.from} → ${payload.to}${payload.label ? `（${payload.label}）` : ''}`
  emit('selectEdge', payload)
}
</script>

<template>
  <section class="report-causal-graph">
    <h2 v-if="title" class="report-causal-graph-title">{{ title }}</h2>
    <p v-if="error" class="report-chart-state" role="note">{{ error }}</p>
    <template v-else-if="graphData">
      <CausalGraph
        :graph="graphData"
        mode="select"
        @select-node="onSelectNode"
        @select-edge="onSelectEdge"
      />
      <p v-if="selection" class="report-graph-selection">{{ selection }}</p>
    </template>
    <p v-else class="report-chart-state" role="note">暂无可展示的因果图。</p>
  </section>
</template>

<style scoped>
.report-causal-graph {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  background: var(--report-surface, #ffffff);
  border: 1px solid var(--report-border, #e2e5e9);
  border-radius: var(--report-card-radius, 8px);
}

.report-causal-graph-title {
  margin: 0;
  font-size: var(--report-chart-title-size, 15px);
  font-weight: 400;
}

.report-chart-state,
.report-graph-selection {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-muted, #6b7280);
}

@media (max-width: 640px) {
  .report-causal-graph {
    padding: 12px;
  }
}
</style>
