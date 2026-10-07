<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../api/client'
import { useLocale } from '../i18n/use-locale'
import { renderMarkdown } from '../renderers/markdown-adapter'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const { text, toggle } = useLocale()
const topic = ref<'userAgreement' | 'userManual' | 'checkUpdate' | null>(null)
const content = ref('')
const loading = ref(false)
const error = ref('')

watch(() => props.open, (open) => {
  if (open) {
    topic.value = null
    content.value = ''
    error.value = ''
  }
})

async function selectTopic(value: 'userAgreement' | 'userManual' | 'checkUpdate'): Promise<void> {
  topic.value = value
  error.value = ''
  if (value === 'checkUpdate') {
    content.value = text.value.versionUpdated
    return
  }
  loading.value = true
  try {
    const response = await api.setting(value)
    if (!response.success || !response.messages) throw new Error(response.error || '加载内容失败。')
    content.value = response.messages
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '加载内容失败。'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div v-if="open" class="modal-backdrop" role="presentation" @click.self="emit('close')">
    <section class="settings-dialog" role="dialog" aria-modal="true" :aria-label="text.settings">
      <h2>{{ text.settings }}</h2>
      <div v-if="!topic" class="settings-options">
        <button type="button" @click="selectTopic('userAgreement')">{{ text.userAgreement }}</button>
        <button type="button" @click="selectTopic('userManual')">{{ text.userManual }}</button>
        <button type="button" @click="selectTopic('checkUpdate')">{{ text.checkUpdate }}</button>
        <button type="button" @click="toggle">{{ text.toggleLanguage }}</button>
      </div>
      <div v-else class="settings-content">
        <p v-if="loading">{{ text.loading }}</p>
        <p v-else-if="error" class="form-error">{{ error }}</p>
        <div v-else class="markdown-content" v-html="renderMarkdown(content)"></div>
      </div>
      <div class="dialog-actions">
        <button v-if="topic" class="secondary-button" type="button" @click="topic = null; content = ''">{{ text.back }}</button>
        <button class="secondary-button" type="button" @click="emit('close')">{{ text.close }}</button>
      </div>
    </section>
  </div>
</template>
