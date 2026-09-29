<script setup lang="ts">
import { CaButton, CaEmptyState } from '@causalagent/design-system'
import { ref, watch } from 'vue'
import { ApiError } from '../api'
import type { SensitiveContentChunk } from '../types'

const props = withDefaults(defineProps<{
  title: string
  loadChunk: (offset: number) => Promise<SensitiveContentChunk>
  showAuditNotice?: boolean
}>(), {
  showAuditNotice: true,
})

const visible = defineModel<boolean>({ required: true })
const content = ref('')
const nextOffset = ref<number | null>(0)
const loading = ref(false)
const error = ref('')

/** 从指定偏移读取一段正文并追加到只读文本区域。 */
async function load(offset: number): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const chunk = await props.loadChunk(offset)
    content.value = offset === 0 ? chunk.content : `${content.value}${chunk.content}`
    nextOffset.value = chunk.next_offset
  } catch (caught) {
    const apiError = caught as ApiError
    error.value = `${apiError.message}（请求 ID：${apiError.requestId || '未知'}）`
  } finally {
    loading.value = false
  }
}

watch(visible, (opened) => {
  if (opened) {
    content.value = ''
    nextOffset.value = 0
    void load(0)
  }
})
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="min(820px, 92vw)" destroy-on-close>
    <el-alert
      v-if="showAuditNotice"
      class="sensitive-notice"
      type="warning"
      :closable="false"
      show-icon
      title="读取正文将会记录管理员、目标、结果和 request ID。"
    />
    <el-alert v-if="error" class="content-error" type="error" :closable="false" :title="error" />
    <div v-loading="loading && !content" class="sensitive-content">
      <pre v-if="content" v-text="content" />
      <CaEmptyState v-else-if="!loading && !error" description="正文为空" />
    </div>
    <template #footer>
      <CaButton
        v-if="nextOffset !== null"
        :loading="loading"
        @click="load(nextOffset)"
      >
        继续加载
      </CaButton>
      <CaButton variant="secondary" @click="visible = false">关闭</CaButton>
    </template>
  </el-dialog>
</template>
