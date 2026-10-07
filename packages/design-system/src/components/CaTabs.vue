<script setup lang="ts">
import { nextTick, ref, useId } from 'vue'
import type { CaTabItem } from '../types'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    modelValue: string
    items: CaTabItem[]
    ariaLabel?: string
  }>(),
  { ariaLabel: undefined },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>()

const uid = useId()
const tabElements = ref<(HTMLButtonElement | undefined)[]>([])

function tabId(id: string) {
  return `${uid}-tab-${id}`
}

function panelId(id: string) {
  return `${uid}-panel-${id}`
}

function setTabElement(element: unknown, index: number) {
  tabElements.value[index] = element instanceof HTMLButtonElement ? element : undefined
}

function select(id: string) {
  if (id !== props.modelValue) emit('update:modelValue', id)
}

function onKeydown(event: KeyboardEvent) {
  const current = props.items.findIndex((item) => item.id === props.modelValue)
  if (current < 0) return

  let next = current
  if (event.key === 'ArrowRight') next = (current + 1) % props.items.length
  else if (event.key === 'ArrowLeft') next = (current - 1 + props.items.length) % props.items.length
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = props.items.length - 1
  else return

  const target = props.items[next]
  if (!target) return

  event.preventDefault()
  select(target.id)
  void nextTick(() => tabElements.value[next]?.focus())
}
</script>

<template>
  <div v-bind="$attrs" class="ca-tabs" role="tablist" :aria-label="props.ariaLabel">
    <button
      v-for="(item, index) in props.items"
      :id="tabId(item.id)"
      :key="item.id"
      :ref="(element) => setTabElement(element, index)"
      class="ca-tabs__tab"
      type="button"
      role="tab"
      :aria-selected="item.id === props.modelValue"
      :aria-controls="panelId(item.id)"
      :tabindex="item.id === props.modelValue ? 0 : -1"
      @click="select(item.id)"
      @keydown="onKeydown"
    >
      {{ item.label }}
    </button>
  </div>
  <template v-if="$slots.panel">
    <div
      v-for="item in props.items"
      v-show="item.id === props.modelValue"
      :id="panelId(item.id)"
      :key="item.id"
      class="ca-tabs__panel"
      role="tabpanel"
      :aria-labelledby="tabId(item.id)"
      tabindex="0"
    >
      <slot name="panel" :id="item.id" />
    </div>
  </template>
</template>
