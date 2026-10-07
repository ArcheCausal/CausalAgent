<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from './api/client'
import { isApiError } from './api/errors'
import ChatWorkspace from './components/ChatWorkspace.vue'
import { ensureMarkedLoaded } from './renderers/markdown-adapter'
import { JobController } from './runtime/jobs/job-controller'
import { readDashboardRoute, sessionHref, signInUrl } from './runtime/navigation/app-route'
import type { DashboardRoute } from './runtime/navigation/app-route'
import { useAuthStore } from './stores/auth.store'
import { useComposerStore } from './stores/composer.store'
import { useFilesStore } from './stores/files.store'
import { useJobsStore } from './stores/jobs.store'
import { useSessionsStore } from './stores/sessions.store'

const auth = useAuthStore()
const sessions = useSessionsStore()
const files = useFilesStore()
const jobs = useJobsStore()
const composer = useComposerStore()
const controller = new JobController(jobs)

const route = ref<DashboardRoute>(readDashboardRoute(globalThis.location.pathname))
const appReady = ref(false)
const notice = ref('')
let noticeTimer: number | null = null

function showNotice(message: string, duration = 3000): void {
  notice.value = message
  if (noticeTimer !== null) globalThis.clearTimeout(noticeTimer)
  noticeTimer = globalThis.setTimeout(() => {
    notice.value = ''
    noticeTimer = null
  }, duration)
}

function showError(error: unknown, fallback: string): void {
  if (isApiError(error) && error.status === 401) auth.handleUnauthorized()
  showNotice(error instanceof Error ? error.message : fallback)
}

function syncRoute(): void {
  route.value = readDashboardRoute(globalThis.location.pathname)
}

function navigate(target: string): void {
  if (target === globalThis.location.pathname) {
    syncRoute()
    return
  }
  globalThis.history.pushState({}, '', target)
  syncRoute()
}

/* 工作区只服务已登录用户：先加载列表与文件，再恢复仍在运行的任务。 */
async function loadWorkspace(): Promise<void> {
  await Promise.all([
    sessions.loadList().catch((error: unknown) => showError(error, '加载历史记录失败。')),
    files.load().catch((error: unknown) => showError(error, '加载文件列表失败。')),
  ])
  try {
    const active = await api.activeJobs()
    const latest = active.jobs?.at(-1)
    if (latest && !route.value.sessionId) {
      navigate(sessionHref(latest.session_id, globalThis.location.pathname))
    }
    for (const job of active.jobs ?? []) {
      const record = jobs.observeActive(job)
      if (record.uiState !== 'waiting_input') {
        void controller.subscribe(record.jobId).catch((error: unknown) => showError(error, '恢复任务订阅失败。'))
      }
    }
  } catch (error) {
    showError(error, '恢复活动任务失败。')
  }
}

async function logout(): Promise<void> {
  try {
    await auth.logout()
  } catch (error) {
    showError(error, '退出登录失败，请稍后再试。')
    return
  }
  controller.stopAll()
  sessions.reset()
  files.reset()
  jobs.reset()
  composer.reset()
  globalThis.location.assign('/')
}

onMounted(async () => {
  await ensureMarkedLoaded()
  const loggedIn = await auth.check()
  if (!loggedIn) {
    globalThis.location.replace(signInUrl(route.value.path))
    return
  }
  globalThis.addEventListener('popstate', syncRoute)
  await loadWorkspace()
  appReady.value = true
})

onBeforeUnmount(() => {
  globalThis.removeEventListener('popstate', syncRoute)
  if (noticeTimer !== null) globalThis.clearTimeout(noticeTimer)
})
</script>

<template>
  <div v-if="!appReady || auth.phase === 'checking'" class="app-loading">加载中...</div>
  <ChatWorkspace
    v-else
    :controller="controller"
    :route="route"
    @error="showError"
    @logout="logout"
    @navigate="navigate"
  />
  <div v-if="notice" class="toast" role="alert" @click="notice = ''">{{ notice }}</div>
</template>
