<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { mountCausalGraph } from '../renderers/graph-renderer'
import type { CausalGraphHandle, GraphEdgeSelection, GraphMode, GraphNodeSelection } from '../renderers/graph-renderer'
import type { CausalGraphData } from '../types/domain'

const props = withDefaults(defineProps<{ graph: CausalGraphData; mode?: GraphMode }>(), {
  mode: 'view',
})
const emit = defineEmits<{
  selectNode: [payload: GraphNodeSelection]
  selectEdge: [payload: GraphEdgeSelection]
}>()

const container = ref<HTMLElement | null>(null)
const failed = ref(false)
let handle: CausalGraphHandle | null = null
let mounting = false
let pendingGraph: CausalGraphData | null = null

async function mount(): Promise<void> {
  if (!container.value || handle || mounting) return
  mounting = true
  try {
    handle = await mountCausalGraph(container.value, props.graph, {
      mode: props.mode,
      onSelectNode: (payload) => emit('selectNode', payload),
      onSelectEdge: (payload) => emit('selectEdge', payload),
    })
    failed.value = false
    if (pendingGraph) {
      handle.update(pendingGraph)
      pendingGraph = null
    }
  } catch {
    failed.value = true
  } finally {
    mounting = false
  }
}

onMounted(mount)

// 报告中的因果图会在同一组件实例里换数据，vis-network 实例必须原地更新而不是重建。
watch(() => props.graph, (graph) => {
  if (handle) handle.update(graph)
  else pendingGraph = graph
})

onBeforeUnmount(() => {
  handle?.destroy()
  handle = null
})
</script>

<template>
  <p v-if="failed" class="causal-graph-fallback" role="note">图形加载失败。</p>
  <div v-show="!failed" ref="container" class="causal-graph causal-graph-container" role="img" aria-label="因果关系图"></div>
</template>

<style scoped>
.causal-graph-fallback {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-muted, #6b7280);
}
</style>
