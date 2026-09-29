<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, adminApi } from '../api'
import {
  CaButton,
  CaCard,
  CaEmptyState,
  CaErrorState,
  CaPageHeader,
} from '@causalagent/design-system'
import CursorPager from '../components/CursorPager.vue'
import { formatBytes, formatDate } from '../lib/dashboard'
import type { AdminFile, CsvPreview, CursorPage, FileDeleteImpact } from '../types'

const page = ref<CursorPage<AdminFile> | null>(null)
const loading = ref(false)
const error = ref('')
const q = ref('')
const userId = ref('')
const mimeType = ref('')
const cursors = ref<(string | undefined)[]>([undefined])
const detail = ref<AdminFile | null>(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const preview = ref<CsvPreview | null>(null)
const previewVisible = ref(false)
const previewLoading = ref(false)
const downloadingId = ref<number | null>(null)
const deleteVisible = ref(false)
const deleteLoading = ref(false)
const deleteSubmitting = ref(false)
const deleteImpact = ref<FileDeleteImpact | null>(null)
const deleteTarget = ref<AdminFile | null>(null)
const deleteConfirmation = ref('')
const deleteReauthPassword = ref('')
const deleteIdempotencyKey = ref('')
const deleteError = ref('')

const currentCursor = computed(() => cursors.value[cursors.value.length - 1])

/** 显示带 request ID 的文件页面错误。 */
function showError(caught: unknown): void {
  const apiError = caught as ApiError
  error.value = `${apiError.message}（请求 ID：${apiError.requestId || '未知'}）`
}

/** 把文件删除错误转换为弹窗内可直接处理的提示。 */
function dialogErrorMessage(caught: unknown): string {
  const apiError = caught as ApiError
  const message = apiError.code === 'reauth_failed'
    ? '当前管理员密码不正确，请重新输入。'
    : apiError.message
  return `${message}（请求 ID：${apiError.requestId || '未知'}）`
}

/** 按当前筛选和游标读取文件元数据。 */
async function loadFiles(reset = false): Promise<void> {
  if (reset) cursors.value = [undefined]
  loading.value = true
  error.value = ''
  try {
    page.value = await adminApi.files({
      limit: 20,
      cursor: currentCursor.value,
      q: q.value,
      user_id: userId.value ? Number(userId.value) : undefined,
      mime_type: mimeType.value,
    })
  } catch (caught) {
    showError(caught)
  } finally {
    loading.value = false
  }
}

/** 进入文件后一页。 */
function nextPage(): void {
  if (!page.value?.next_cursor) return
  cursors.value.push(page.value.next_cursor)
  void loadFiles()
}

/** 返回文件上一页。 */
function previousPage(): void {
  if (cursors.value.length <= 1) return
  cursors.value.pop()
  void loadFiles()
}

/** 点击后读取并审计文件元数据详情。 */
async function openDetail(row: AdminFile): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await adminApi.file(row.id)
  } catch (caught) {
    showError(caught)
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

/** 安全预览 CSV，并在成功后刷新访问计数。 */
async function openPreview(row: AdminFile): Promise<void> {
  previewVisible.value = true
  previewLoading.value = true
  preview.value = null
  try {
    preview.value = await adminApi.previewFile(row.id)
    await loadFiles()
  } catch (caught) {
    showError(caught)
    previewVisible.value = false
  } finally {
    previewLoading.value = false
  }
}

/** 受控下载文件，并在成功后刷新访问计数。 */
async function download(row: AdminFile): Promise<void> {
  downloadingId.value = row.id
  error.value = ''
  try {
    await adminApi.downloadFile(row.id)
    await loadFiles()
  } catch (caught) {
    showError(caught)
  } finally {
    downloadingId.value = null
  }
}

/** 生成文件删除对话框内稳定复用的幂等键。 */
function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `admin-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

/** 打开文件行、BLOB 大小和活动任务阻断预览。 */
async function openDelete(row: AdminFile): Promise<void> {
  deleteVisible.value = true
  deleteLoading.value = true
  deleteImpact.value = null
  deleteTarget.value = row
  deleteConfirmation.value = ''
  deleteReauthPassword.value = ''
  deleteIdempotencyKey.value = newIdempotencyKey()
  deleteError.value = ''
  error.value = ''
  try {
    deleteImpact.value = await adminApi.fileDeleteImpact(row.id)
  } catch (caught) {
    showError(caught)
    deleteVisible.value = false
  } finally {
    deleteLoading.value = false
  }
}

/** 经文件名确认与重新认证后物理删除数据库行和 BLOB。 */
async function submitDelete(): Promise<void> {
  if (!deleteTarget.value || !deleteImpact.value?.can_delete) return
  deleteSubmitting.value = true
  deleteError.value = ''
  error.value = ''
  try {
    const result = await adminApi.deleteFile(
      deleteTarget.value.id,
      {
        confirm_filename: deleteConfirmation.value,
        reauth_password: deleteReauthPassword.value,
        confirmed: true,
      },
      deleteIdempotencyKey.value,
    )
    ElMessage.success(`文件已删除${result.replayed ? '（幂等重放）' : ''}`)
    deleteVisible.value = false
    await loadFiles(true)
  } catch (caught) {
    const apiError = caught as ApiError
    deleteError.value = dialogErrorMessage(caught)
    if (apiError.code === 'reauth_failed' || apiError.code === 'reauth_required') {
      deleteReauthPassword.value = ''
    }
  } finally {
    deleteSubmitting.value = false
  }
}

onMounted(() => loadFiles())
</script>

<template>
  <section class="admin-page">
    <CaPageHeader
      title="对话文件管理"
      description="CSV 预览与下载会更新访问时间、次数并写入审计。"
      :level="1"
      size="md"
    />

    <section class="filter-bar">
      <el-input v-model="q" clearable placeholder="文件名" @keyup.enter="loadFiles(true)" />
      <el-input v-model="userId" clearable placeholder="用户 ID" @keyup.enter="loadFiles(true)" />
      <el-select v-model="mimeType" clearable placeholder="MIME类型">
        <el-option label="text/csv" value="text/csv" />
        <el-option label="application/vnd.ms-excel" value="application/vnd.ms-excel" />
      </el-select>
      <CaButton variant="primary" :loading="loading" @click="loadFiles(true)">筛选</CaButton>
    </section>

    <CaErrorState v-if="error" class="page-notice" title="文件读取失败" :description="error" />

    <CaCard as="section" class="panel table-panel" variant="outline" padding="md">
      <el-table v-loading="loading" :data="page?.items || []" empty-text="没有符合条件的文件">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="original_filename" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column prop="username" label="归属用户" min-width="130" />
        <el-table-column prop="mime_type" label="MIME" min-width="180" />
        <el-table-column label="大小" width="110">
          <template #default="{ row }">{{ formatBytes(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="access_count" label="使用次数" width="100" />
        <el-table-column prop="object_reference_count" label="对象引用" width="100" />
        <el-table-column label="最近访问" min-width="180">
          <template #default="{ row }">{{ formatDate(row.last_accessed_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="290" fixed="right">
          <template #default="{ row }">
            <CaButton variant="quiet" size="sm" @click="openDetail(row)">详情</CaButton>
            <CaButton variant="quiet" size="sm" @click="openPreview(row)">预览</CaButton>
            <CaButton
              variant="quiet"
              size="sm"
              :loading="downloadingId === row.id"
              @click="download(row)"
            >
              下载
            </CaButton>
            <el-button link type="danger" @click="openDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <CursorPager
        :can-previous="cursors.length > 1"
        :has-more="Boolean(page?.has_more)"
        :loading="loading"
        @previous="previousPage"
        @next="nextPage"
      />
    </CaCard>

    <el-drawer v-model="detailVisible" title="文件详情" size="min(560px, 100vw)">
      <div v-loading="detailLoading">
        <el-descriptions v-if="detail" :column="1" border>
          <el-descriptions-item label="文件 ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="归属用户">{{ detail.username }} (#{{ detail.user_id }})</el-descriptions-item>
          <el-descriptions-item label="原始文件名">{{ detail.original_filename }}</el-descriptions-item>
          <el-descriptions-item label="存储文件名">{{ detail.filename }}</el-descriptions-item>
          <el-descriptions-item label="MIME">{{ detail.mime_type }}</el-descriptions-item>
          <el-descriptions-item label="大小">{{ formatBytes(detail.file_size) }}</el-descriptions-item>
          <el-descriptions-item label="上传时间">{{ formatDate(detail.upload_timestamp) }}</el-descriptions-item>
          <el-descriptions-item label="最近访问">{{ formatDate(detail.last_accessed_at) }}</el-descriptions-item>
          <el-descriptions-item label="使用次数">{{ detail.access_count }}</el-descriptions-item>
          <el-descriptions-item label="对象引用">{{ detail.object_reference_count }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-drawer>

    <el-dialog v-model="previewVisible" title="CSV 预览" width="min(1100px, 96vw)">
      <div v-loading="previewLoading" class="csv-preview-wrap">
        <p v-if="preview" class="preview-meta">
          {{ preview.filename }} · {{ preview.encoding }}
          <span v-if="preview.truncated">· 按安全上限截断</span>
        </p>
        <div v-if="preview" class="csv-table-scroll">
          <table class="csv-preview-table">
            <thead>
              <tr>
                <th v-for="(column, index) in preview.columns" :key="index" v-text="column || `列 ${index + 1}`" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rowIndex) in preview.rows" :key="rowIndex">
                <td v-for="(cell, cellIndex) in row" :key="cellIndex" v-text="cell" />
              </tr>
            </tbody>
          </table>
        </div>
        <CaEmptyState v-else-if="!previewLoading" description="CSV 内容为空" />
      </div>
    </el-dialog>

    <el-dialog v-model="deleteVisible" title="删除文件" width="min(660px, 96vw)">
      <div v-loading="deleteLoading">
        <el-alert
          class="danger-alert"
          type="error"
          :closable="false"
          show-icon
          title="删除记录会同时删除数据库 BLOB，不提供回收站且不可恢复！"
        />
        <el-alert
          v-if="deleteError"
          class="dialog-error danger-alert"
          type="error"
          :closable="false"
          show-icon
          :title="deleteError"
        />
        <template v-if="deleteImpact">
          <el-descriptions :column="1" border class="file-delete-impact">
            <el-descriptions-item label="文件">
              {{ deleteImpact.file.original_filename }} (#{{ deleteImpact.file.id }})
            </el-descriptions-item>
            <el-descriptions-item label="归属用户">
              {{ deleteImpact.file.username }} (#{{ deleteImpact.file.user_id }})
            </el-descriptions-item>
            <el-descriptions-item label="BLOB 大小">
              {{ formatBytes(deleteImpact.impact.blob_bytes) }}
            </el-descriptions-item>
            <el-descriptions-item label="归属用户活动任务">
              {{ deleteImpact.impact.owner_active_jobs }}
            </el-descriptions-item>
          </el-descriptions>
          <el-alert
            v-if="deleteImpact.blockers.length"
            class="page-notice danger-alert"
            type="error"
            :closable="false"
            :title="deleteImpact.blockers.join('；')"
          />
          <div class="file-delete-form">
            <div class="danger-confirmation-field">
              <label for="file-delete-confirmation">
                第一步：输入文件名以确认删除
              </label>
              <el-input
                id="file-delete-confirmation"
                v-model="deleteConfirmation"
                :aria-label="`输入文件名 ${deleteImpact.requires_confirmation} 确认`"
                autocomplete="off"
                :placeholder="`请输入完整文件名：${deleteImpact.requires_confirmation}`"
              />
              <p>请完整输入“{{ deleteImpact.requires_confirmation }}”，防止误删其他文件。</p>
            </div>
            <div class="danger-confirmation-field">
              <label for="file-delete-reauth-password">
                第二步：输入当前管理员登录密码
              </label>
              <el-input
                id="file-delete-reauth-password"
                v-model="deleteReauthPassword"
                aria-label="当前管理员密码（重新认证）"
                type="password"
                show-password
                autocomplete="current-password"
                placeholder="请输入当前管理员密码"
              />
            </div>
          </div>
        </template>
      </div>
      <template #footer>
        <CaButton variant="secondary" @click="deleteVisible = false">取消</CaButton>
        <el-button
          type="danger"
          :loading="deleteSubmitting"
          :disabled="
            !deleteImpact?.can_delete ||
              deleteConfirmation !== deleteImpact?.requires_confirmation ||
              !deleteReauthPassword
          "
          @click="submitDelete"
        >
          确认删除
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.file-delete-impact,
.file-delete-form {
  margin-top: var(--ca-space-16);
}

.dialog-error {
  margin-top: var(--ca-space-12);
}

.file-delete-form {
  display: grid;
  gap: var(--ca-space-16);
}

.danger-confirmation-field label {
  display: block;
  margin-bottom: var(--ca-space-8);
  color: var(--ca-text-default);
  font-weight: var(--ca-weight-regular);
}

.danger-confirmation-field p {
  margin: var(--ca-space-4) 0 0;
  color: var(--ca-text-muted);
  font-size: var(--ca-text-caption);
  line-height: 1.5;
}
</style>
