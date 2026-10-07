<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiError, adminApi } from '../api'
import { CaButton, CaCard, CaErrorState, CaPageHeader } from '@causalagent/design-system'
import CursorPager from '../components/CursorPager.vue'
import SensitiveContentDialog from '../components/SensitiveContentDialog.vue'
import { formatDate } from '../lib/dashboard'
import type {
  AdminAttachment,
  AdminMessage,
  AdminSession,
  CursorPage,
  SensitiveContentChunk,
} from '../types'

const page = ref<CursorPage<AdminSession> | null>(null)
const loading = ref(false)
const error = ref('')
const q = ref('')
const userId = ref('')
const archived = ref('')
const cursors = ref<(string | undefined)[]>([undefined])

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<AdminSession | null>(null)
const messages = ref<CursorPage<AdminMessage> | null>(null)
const messageCursors = ref<(string | undefined)[]>([undefined])
const attachments = ref<AdminAttachment[]>([])
const attachmentsVisible = ref(false)
const attachmentsLoading = ref(false)

const contentVisible = ref(false)
const contentTitle = ref('')
const contentLoader = ref<(offset: number) => Promise<SensitiveContentChunk>>(
  async () => Promise.reject(new Error('正文加载器尚未初始化')),
)

const currentCursor = computed(() => cursors.value[cursors.value.length - 1])
const currentMessageCursor = computed(
  () => messageCursors.value[messageCursors.value.length - 1],
)

/** 按当前筛选和游标读取会话摘要。 */
async function loadSessions(reset = false): Promise<void> {
  if (reset) cursors.value = [undefined]
  loading.value = true
  error.value = ''
  try {
    page.value = await adminApi.sessions({
      limit: 20,
      cursor: currentCursor.value,
      q: q.value,
      user_id: userId.value ? Number(userId.value) : undefined,
      is_archived: archived.value === '' ? undefined : archived.value === 'true',
    })
  } catch (caught) {
    showError(caught)
  } finally {
    loading.value = false
  }
}

/** 把 API 错误转换为带 request ID 的页面提示。 */
function showError(caught: unknown): void {
  const apiError = caught as ApiError
  error.value = `${apiError.message}（请求 ID：${apiError.requestId || '未知'}）`
}

/** 进入会话后一页。 */
function nextPage(): void {
  if (!page.value?.next_cursor) return
  cursors.value.push(page.value.next_cursor)
  void loadSessions()
}

/** 返回会话上一页。 */
function previousPage(): void {
  if (cursors.value.length <= 1) return
  cursors.value.pop()
  void loadSessions()
}

/** 读取当前会话的一页消息摘要。 */
async function loadMessages(): Promise<void> {
  if (!detail.value) return
  detailLoading.value = true
  try {
    messages.value = await adminApi.sessionMessages(detail.value.id, {
      limit: 20,
      cursor: currentMessageCursor.value,
    })
  } catch (caught) {
    showError(caught)
  } finally {
    detailLoading.value = false
  }
}

/** 点击后读取会话元数据和首屏消息摘要。 */
async function openDetail(row: AdminSession): Promise<void> {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  messages.value = null
  messageCursors.value = [undefined]
  try {
    detail.value = await adminApi.session(row.id)
    await loadMessages()
  } catch (caught) {
    showError(caught)
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

/** 翻到当前会话后一页消息。 */
function nextMessages(): void {
  if (!messages.value?.next_cursor) return
  messageCursors.value.push(messages.value.next_cursor)
  void loadMessages()
}

/** 返回当前会话上一页消息。 */
function previousMessages(): void {
  if (messageCursors.value.length <= 1) return
  messageCursors.value.pop()
  void loadMessages()
}

/** 明确点击后打开消息正文读取对话框。 */
function revealMessage(row: AdminMessage): void {
  contentTitle.value = `消息 #${row.id} 正文`
  contentLoader.value = (offset) => adminApi.messageContent(row.id, offset)
  contentVisible.value = true
}

/** 读取一条消息的附件元数据。 */
async function openAttachments(row: AdminMessage): Promise<void> {
  attachmentsVisible.value = true
  attachmentsLoading.value = true
  attachments.value = []
  try {
    attachments.value = (await adminApi.messageAttachments(row.id)).items
  } catch (caught) {
    showError(caught)
    attachmentsVisible.value = false
  } finally {
    attachmentsLoading.value = false
  }
}

/** 明确点击后打开附件正文读取对话框。 */
function revealAttachment(row: AdminAttachment): void {
  contentTitle.value = `附件 #${row.id} 正文`
  contentLoader.value = (offset) => adminApi.attachmentContent(row.id, offset)
  contentVisible.value = true
}

onMounted(() => loadSessions())
</script>

<template>
  <section class="admin-page">
    <CaPageHeader title="会话与内容管理" :level="1" size="md" />

    <section class="filter-bar">
      <el-input v-model="q" clearable placeholder="会话 ID" @keyup.enter="loadSessions(true)" />
      <el-input v-model="userId" clearable placeholder="用户 ID" @keyup.enter="loadSessions(true)" />
      <el-select v-model="archived" placeholder="全部归档状态" clearable>
        <el-option label="未归档" value="false" />
        <el-option label="已归档" value="true" />
      </el-select>
      <CaButton variant="primary" :loading="loading" @click="loadSessions(true)">筛选</CaButton>
    </section>

    <CaErrorState v-if="error" class="page-notice" title="会话读取失败" :description="error" />

    <CaCard as="section" class="panel table-panel" variant="outline" padding="md">
      <el-table v-loading="loading" :data="page?.items || []" empty-text="没有符合条件的会话">
        <el-table-column prop="id" label="会话 ID" min-width="260" show-overflow-tooltip />
        <el-table-column prop="username" label="归属用户" min-width="140" />
        <el-table-column label="标题" min-width="220">
          <template #default="{ row }">
            <el-tooltip
              :content="row.title || '未命名'"
              placement="top"
              :show-after="250"
              popper-class="session-title-tooltip"
            >
              <span class="session-title-cell">{{ row.title || '未命名' }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="message_count" label="消息数" width="90" />
        <el-table-column label="归档" width="90">
          <template #default="{ row }">{{ row.is_archived ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column label="最后活动" min-width="180">
          <template #default="{ row }">{{ formatDate(row.last_activity_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <CaButton variant="quiet" size="sm" @click="openDetail(row)">查看详情</CaButton>
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

    <el-drawer v-model="detailVisible" title="会话详情" size="min(980px, 100vw)">
      <div v-loading="detailLoading">
        <el-descriptions v-if="detail" :column="2" border>
          <el-descriptions-item label="会话 ID" :span="2">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="归属用户">{{ detail.username }} (#{{ detail.user_id }})</el-descriptions-item>
          <el-descriptions-item label="消息数">{{ detail.message_count }}</el-descriptions-item>
          <el-descriptions-item label="标题" :span="2">{{ detail.title || '未命名' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="最后活动">{{ formatDate(detail.last_activity_at) }}</el-descriptions-item>
        </el-descriptions>

        <h3 class="drawer-section-title">消息摘要</h3>
        <el-alert
          class="sensitive-notice"
          type="warning"
          :closable="false"
          show-icon
          title="读取正文将会记录管理员、目标、结果和 request ID。"
        />
        <el-table :data="messages?.items || []" empty-text="该会话暂无消息">
          <el-table-column prop="id" label="ID" width="80" />
          <el-table-column prop="message_type" label="类型" width="90" />
          <el-table-column prop="content_preview" label="摘要" min-width="280" show-overflow-tooltip />
          <el-table-column prop="attachment_count" label="附件" width="80" />
          <el-table-column label="时间" min-width="170">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <CaButton variant="quiet" size="sm" @click="revealMessage(row)">查看正文</CaButton>
              <CaButton
                v-if="row.attachment_count"
                variant="quiet"
                size="sm"
                @click="openAttachments(row)"
              >
                查看附件
              </CaButton>
            </template>
          </el-table-column>
        </el-table>
        <CursorPager
          :can-previous="messageCursors.length > 1"
          :has-more="Boolean(messages?.has_more)"
          :loading="detailLoading"
          @previous="previousMessages"
          @next="nextMessages"
        />
      </div>
    </el-drawer>

    <el-drawer v-model="attachmentsVisible" title="消息附件" size="min(640px, 100vw)">
      <el-table v-loading="attachmentsLoading" :data="attachments" empty-text="没有附件记录">
        <el-table-column prop="id" label="ID" width="90" />
        <el-table-column prop="attachment_type" label="类型" min-width="170" />
        <el-table-column prop="content_size" label="字符数" width="110" />
        <el-table-column label="时间" min-width="170">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <CaButton variant="quiet" size="sm" @click="revealAttachment(row)">查看正文</CaButton>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <SensitiveContentDialog
      v-model="contentVisible"
      :title="contentTitle"
      :load-chunk="contentLoader"
      :show-audit-notice="false"
    />
  </section>
</template>
