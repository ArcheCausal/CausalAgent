<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  PRESENTATION_CHARS_PER_TICK,
  PRESENTATION_TICK_MS,
  advancePresentationText,
  prefersReducedMotion,
  textLength,
} from '../runtime/chat/presentation'
import { useChatScrollFollow } from '../runtime/chat/scroll-follow'
import MessageBody from './MessageBody.vue'

const props = defineProps<{ text: string; animate: boolean }>()
const scrollFollow = useChatScrollFollow()
const visible = ref(props.animate ? '' : props.text)
let timer: number | null = null

// 展示游标只在客户端推进；服务端已经收到完整缓冲区，终态只做一次校正。
const streaming = computed(() => props.animate)

function stopTimer(): void {
  if (timer !== null) {
    globalThis.clearInterval(timer)
    timer = null
  }
}

function tick(): void {
  const target = props.text
  if (textLength(visible.value) >= textLength(target)) {
    stopTimer()
    if (visible.value !== target) visible.value = target
    return
  }
  visible.value = advancePresentationText(visible.value, target, PRESENTATION_CHARS_PER_TICK)
  scrollFollow.keepLatest()
}

function sync(): void {
  if (!props.animate || prefersReducedMotion()) {
    stopTimer()
    visible.value = props.text
    return
  }
  // 缓冲区被替换（终态校正或换流）时从头推进，不保留上一份草稿的展示游标。
  if (!props.text.startsWith(visible.value)) visible.value = ''
  if (textLength(visible.value) < textLength(props.text)) {
    if (timer === null) timer = globalThis.setInterval(tick, PRESENTATION_TICK_MS)
  } else {
    stopTimer()
  }
}

watch(() => props.text, sync)
watch(() => props.animate, sync)

onMounted(() => {
  sync()
  scrollFollow.keepLatest()
})

onBeforeUnmount(stopTimer)
</script>

<template>
  <article class="message ai-message" :class="{ 'streaming-draft': streaming }">
    <MessageBody :text="{ type: 'text', summary: visible }" />
  </article>
</template>
