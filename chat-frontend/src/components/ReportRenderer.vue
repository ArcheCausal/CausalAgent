<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { copyTextToClipboard, reportToMarkdown } from '../renderers/report-clipboard'
import {
  collectEvidenceUsage,
  evidencePage,
  evidenceSnippet,
  parseReportDocument,
  reportBlockAnchor,
} from '../renderers/report-document'
import ReportBlockView from './ReportBlockView.vue'

const props = defineProps<{ document: unknown }>()
const parsed = computed(() => parseReportDocument(props.document))
const expandedSourceIds = ref<Set<string>>(new Set())

type CopyState = 'idle' | 'copying' | 'copied' | 'failed'

const copyState = ref<CopyState>('idle')
const copyFailureReason = ref('')
const COPY_FEEDBACK_MS = 2000
let copyResetTimer: number | undefined

const copyLabel = computed(() => {
  if (copyState.value === 'copying') return '复制中…'
  if (copyState.value === 'copied') return '已复制'
  if (copyState.value === 'failed') return '复制失败，请重试'
  return '复制报告'
})

function scheduleCopyReset(): void {
  window.clearTimeout(copyResetTimer)
  copyResetTimer = window.setTimeout(() => {
    copyState.value = 'idle'
  }, COPY_FEEDBACK_MS)
}

/*
 * 一键复制：把当前报告投影成 Markdown 后写入剪贴板。
 * 复制期间忽略重复点击；成功与失败都在按钮上就地反馈，2 秒后回到初始文案。
 */
async function copyReport(): Promise<void> {
  const report = parsed.value
  if (!report || copyState.value === 'copying') return
  copyState.value = 'copying'
  try {
    await copyTextToClipboard(reportToMarkdown(report))
    copyState.value = 'copied'
    copyFailureReason.value = ''
  } catch (error) {
    copyState.value = 'failed'
    // 失败原因挂在按钮 title 上，浏览器权限策略差异导致时可以就地定位。
    copyFailureReason.value = error instanceof Error ? error.message : String(error)
  }
  scheduleCopyReset()
}

// 每次生成/切换报告都从收起状态开始，避免沿用上一份报告的展开状态和复制反馈。
watch(() => props.document, () => {
  expandedSourceIds.value = new Set()
  window.clearTimeout(copyResetTimer)
  copyState.value = 'idle'
  copyFailureReason.value = ''
})

onBeforeUnmount(() => window.clearTimeout(copyResetTimer))

/*
 * 来源导航：正文只保留文字，知识库与联网证据统一收进页脚，按来源分组；
 * 每条证据带页码和“定位正文”，点击后滚动到引用它的报告块并短暂高亮。
 */
const sourceGroups = computed(() => {
  const report = parsed.value
  if (!report) return []
  const usage = collectEvidenceUsage(
    report.blocks,
    new Set(report.evidenceRefs.map((evidence) => evidence.evidence_id)),
  )
  return report.sources.map((source) => ({
    source,
    collapsible: source.kind === 'knowledge_base',
    items: report.evidenceRefs
      .filter((evidence) => (evidence.source_ids ?? []).includes(source.source_id))
      .map((evidence) => ({
        evidenceId: evidence.evidence_id,
        text: evidenceSnippet(evidence.description, source.title),
        page: evidencePage(evidence.locator),
        blockIds: usage.get(evidence.evidence_id) ?? [],
      })),
  }))
})

function isSourceExpanded(sourceId: string): boolean {
  return expandedSourceIds.value.has(sourceId)
}

function toggleSource(sourceId: string) {
  const next = new Set(expandedSourceIds.value)
  if (next.has(sourceId)) next.delete(sourceId)
  else next.add(sourceId)
  expandedSourceIds.value = next
}

function sourceEvidencePanelId(sourceId: string): string {
  return `report-source-evidence-${sourceId}`
}

function focusEvidence(blockIds: string[]) {
  const target = blockIds[0]
  if (!target) return
  const element = window.document.getElementById(reportBlockAnchor(target))
  if (!element) return
  element.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  element.classList.add('is-cited-target')
  window.setTimeout(() => element.classList.remove('is-cited-target'), 1800)
}
</script>

<template>
  <section v-if="parsed" class="report-document">
    <header class="report-header">
      <h1 class="report-title">{{ parsed.title }}</h1>
      <button
        type="button"
        class="report-copy"
        :class="`report-copy-${copyState}`"
        :disabled="copyState === 'copying'"
        :title="copyState === 'failed' ? copyFailureReason : undefined"
        @click="copyReport"
      >
        <svg class="report-copy-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <rect x="5.6" y="5.6" width="8.6" height="8.6" rx="1.8" />
          <path d="M10.4 5.4V3.6a1.8 1.8 0 0 0-1.8-1.8H3.6a1.8 1.8 0 0 0-1.8 1.8v5a1.8 1.8 0 0 0 1.8 1.8h1.9" />
        </svg>
        <span class="report-copy-label" role="status" aria-live="polite">{{ copyLabel }}</span>
      </button>
    </header>
    <ReportBlockView
      v-for="block in parsed.blocks"
      :key="block.id"
      :block="block"
    />
    <footer v-if="sourceGroups.length" class="report-sources">
      <h2 class="report-sources-title">来源</h2>
      <ul class="report-sources-list">
        <li v-for="group in sourceGroups" :key="group.source.source_id" class="report-source-item">
          <div class="report-source-heading">
            <a v-if="group.source.url" :href="group.source.url" target="_blank" rel="noopener noreferrer">{{ group.source.title }}</a>
            <span v-else class="report-source-title">{{ group.source.title }}</span>
            <button
              v-if="group.collapsible && group.items.length"
              type="button"
              class="report-evidence-toggle"
              :aria-expanded="isSourceExpanded(group.source.source_id)"
              :aria-label="isSourceExpanded(group.source.source_id) ? '收起知识库引用' : '展开知识库引用'"
              :aria-controls="sourceEvidencePanelId(group.source.source_id)"
              @click="toggleSource(group.source.source_id)"
            >
              <span class="report-evidence-toggle-icon" aria-hidden="true"></span>
            </button>
          </div>
          <ul
            v-if="group.items.length && (!group.collapsible || isSourceExpanded(group.source.source_id))"
            :id="group.collapsible ? sourceEvidencePanelId(group.source.source_id) : undefined"
            class="report-source-evidence"
          >
            <li v-for="item in group.items" :key="item.evidenceId" class="report-source-evidence-item">
              <span class="report-evidence-text">{{ item.text }}</span>
              <span v-if="item.page" class="report-evidence-page">第 {{ item.page }} 页</span>
              <button
                v-if="item.blockIds.length"
                type="button"
                class="report-evidence-jump"
                @click="focusEvidence(item.blockIds)"
              >
                定位正文
              </button>
            </li>
          </ul>
        </li>
      </ul>
    </footer>
  </section>
  <div v-else class="report-document report-document-error" role="alert">
    报告内容不可用，请刷新会话或重新生成报告。
  </div>
</template>

<style scoped>
.report-document {
  display: flex;
  flex-direction: column;
  gap: 32px;
  width: 100%;
  max-width: 100%;
  overflow-wrap: anywhere;
  color: var(--report-text, #1f2937);
}

.report-document-error {
  font-size: 14px;
  color: var(--color-text-muted, #6b7280);
}

.report-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px 16px;
}

.report-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: var(--report-title-size, 22px);
  font-weight: 400;
  line-height: 1.35;
}

.report-copy {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  padding: 4px 9px;
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-muted, #6b7280);
  white-space: nowrap;
  background: transparent;
  border: 1px solid var(--report-border, #e2e5e9);
  border-radius: 6px;
  cursor: pointer;
  transition: color 160ms ease, border-color 160ms ease, background-color 160ms ease;
}

.report-copy-icon {
  flex: none;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.report-copy:hover:not(:disabled) {
  color: var(--report-accent, #0067c0);
  border-color: currentColor;
  background: var(--report-muted-background, #eef1f4);
}

.report-copy:focus-visible {
  outline: 2px solid var(--report-accent, #0067c0);
  outline-offset: 2px;
}

.report-copy:disabled {
  cursor: progress;
  opacity: 0.7;
}

.report-copy-copied {
  color: var(--report-accent, #0067c0);
  border-color: currentColor;
}

.report-copy-failed {
  color: #b42318;
  border-color: currentColor;
}

.report-sources {
  padding-top: 12px;
  font-size: 13px;
  border-top: 1px solid var(--report-border, #e2e5e9);
}

.report-sources-title {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 400;
  color: var(--report-accent, #0067c0);
}

.report-sources-list {
  margin: 0;
  padding-left: 18px;
  color: var(--color-text-muted, #6b7280);
}

.report-sources-list a {
  color: var(--report-accent, #0067c0);
}

.report-source-item {
  margin-bottom: 6px;
}

.report-source-heading {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 3px;
}

.report-source-evidence {
  margin: 4px 0 0;
  padding-left: 16px;
}

.report-source-evidence-item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
}

.report-evidence-text {
  color: var(--color-text-muted, #6b7280);
}

.report-evidence-page {
  flex: none;
  font-size: 12px;
  color: var(--color-text-muted, #6b7280);
}

.report-evidence-jump {
  flex: none;
  padding: 0;
  font: inherit;
  color: var(--report-accent, #0067c0);
  background: none;
  border: 0;
  cursor: pointer;
}

.report-evidence-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  margin: 0;
  color: var(--color-text-muted, #6b7280);
  background: none;
  border: 0;
  cursor: pointer;
}

.report-evidence-toggle-icon {
  width: 6px;
  height: 6px;
  margin-top: -3px;
  border-right: 1px solid currentColor;
  border-bottom: 1px solid currentColor;
  transform: rotate(45deg);
  transition: transform 160ms ease;
}

.report-evidence-toggle[aria-expanded='true'] .report-evidence-toggle-icon {
  margin-top: 3px;
  transform: rotate(225deg);
}

.report-evidence-toggle:hover {
  color: var(--report-accent, #0067c0);
}

.report-document :deep(.is-cited-target) {
  background: var(--report-cited-background, #f3f3f3);
  box-shadow: 0 0 0 6px var(--report-cited-background, #f3f3f3);
  border-radius: 4px;
  transition: background-color 200ms ease, box-shadow 200ms ease;
}
</style>
