<script setup lang="ts">
import {
  CaBadge,
  CaButton,
  CaCard,
  CaErrorState,
  CaPageHeader,
} from '@causalagent/design-system'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { adminApi, ApiError } from '../api'
import { formatDate } from '../lib/dashboard'
import { statusTone } from '../lib/statusTone'
import type {
  AuditEvent,
  MonitorField,
  MonitorOverrideMap,
  MonitorSettings,
} from '../types'

interface FieldDefinition {
  key: MonitorField
  label: string
  description: string
  unit?: string
  kind: 'boolean' | 'integer'
}

const fields: FieldDefinition[] = [
  { key: 'auto_refresh_enabled', label: '自动采集总开关', description: '关闭后不执行周期采集，但手动刷新和手动完整性审计仍然可用。', kind: 'boolean' },
  { key: 'realtime_interval_seconds', label: '实时状态采集周期', description: '主从、连接和 Worker/Job 快照周期。', unit: '秒', kind: 'integer' },
  { key: 'sql_interval_seconds', label: 'SQL 性能采集周期', description: '慢查询增量和高负载 SQL digest 采集周期。', unit: '秒', kind: 'integer' },
  { key: 'table_capacity_interval_seconds', label: '表容量采集周期', description: 'InnoDB 表容量估算的低频采集周期。', unit: '秒', kind: 'integer' },
  { key: 'slow_query_warning_delta', label: '慢查询增量告警阈值', description: '采集窗口内 Slow_queries 增量达到此值时标记警告。', kind: 'integer' },
  { key: 'integrity_enabled', label: '完整性定时审计开关', description: '只控制定时审计，不影响手动完整性审计。', kind: 'boolean' },
  { key: 'integrity_interval_seconds', label: '完整性审计周期', description: '启用定时审计后的低频执行周期。', unit: '秒', kind: 'integer' },
]

const settings = ref<MonitorSettings | null>(null)
const draft = reactive<Partial<MonitorOverrideMap>>({})
const loading = ref(true)
const saving = ref(false)
const resetting = ref(false)
const historyLoading = ref(false)
const history = ref<AuditEvent[]>([])
const nextBeforeId = ref<number | null>(null)
const pageError = ref('')

const canWrite = computed(() =>
  settings.value?.state === 'current' && settings.value.version !== null)

/** 把服务端覆盖快照复制到可编辑草稿。 */
function cloneOverrides(value: MonitorOverrideMap): void {
  for (const field of fields) draft[field.key] = value[field.key]
}

/** 读取当前版本、有效值、来源和降级状态。 */
async function loadSettings(): Promise<void> {
  loading.value = true
  pageError.value = ''
  try {
    settings.value = await adminApi.settings()
    cloneOverrides(settings.value.overrides)
  } catch (error) {
    pageError.value = `读取监控配置失败：${error instanceof Error ? error.message : String(error)}`
  } finally {
    loading.value = false
  }
}

/** 按有界游标读取或追加配置审计记录。 */
async function loadHistory(append = false): Promise<void> {
  historyLoading.value = true
  try {
    const data = await adminApi.settingsHistory(
      20,
      append && nextBeforeId.value ? nextBeforeId.value : undefined,
    )
    history.value = append ? [...history.value, ...data.items] : data.items
    nextBeforeId.value = data.next_before_id
  } catch (error) {
    ElMessage.error(`读取变更记录失败：${error instanceof Error ? error.message : String(error)}`)
  } finally {
    historyLoading.value = false
  }
}

/** 生成 PUT 所要求的完整七项可空覆盖快照。 */
function normalizedDraft(): MonitorOverrideMap {
  return Object.fromEntries(
    fields.map(field => [field.key, draft[field.key] ?? null]),
  ) as MonitorOverrideMap
}

/** 统一展示字段错误、版本冲突和可关联 request ID。 */
async function resolveWriteError(error: unknown): Promise<void> {
  if (error instanceof ApiError && error.code === 'version_conflict') {
    await ElMessageBox.alert(
      `配置已被其他管理员修改，请重新加载后再保存。请求 ID：${error.requestId || '未知'}`,
      '版本冲突',
      { type: 'warning', confirmButtonText: '重新加载' },
    )
    await loadSettings()
    await loadHistory()
    return
  }
  if (error instanceof ApiError && error.fields) {
    ElMessage.error(Object.values(error.fields).join('；'))
    return
  }
  ElMessage.error(
    `${error instanceof Error ? error.message : String(error)}`
    + (error instanceof ApiError && error.requestId ? `（请求 ID：${error.requestId}）` : ''),
  )
}

/** 按当前乐观版本整页保存七项覆盖。 */
async function save(): Promise<void> {
  if (!settings.value || settings.value.version === null) return
  saving.value = true
  try {
    settings.value = await adminApi.saveSettings(settings.value.version, normalizedDraft())
    cloneOverrides(settings.value.overrides)
    ElMessage.success('数据库监控配置已保存，其他进程将在 5 秒内热加载。')
    await loadHistory()
  } catch (error) {
    await resolveWriteError(error)
  } finally {
    saving.value = false
  }
}

/** 经二次确认后把全部数据库覆盖恢复为继承。 */
async function resetAll(): Promise<void> {
  if (!settings.value || settings.value.version === null) return
  try {
    await ElMessageBox.confirm(
      '重置后七项配置全部恢复为环境变量或代码默认值。',
      '重置全部覆盖',
      { type: 'warning', confirmButtonText: '确认重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  resetting.value = true
  try {
    settings.value = await adminApi.resetSettings(settings.value.version)
    cloneOverrides(settings.value.overrides)
    ElMessage.success('全部数据库覆盖值已重置。')
    await loadHistory()
  } catch (error) {
    await resolveWriteError(error)
  } finally {
    resetting.value = false
  }
}

/** 切换数值配置的继承/覆盖状态，并以当前有效值初始化覆盖。 */
function enableIntegerOverride(field: FieldDefinition, enabled: boolean): void {
  if (!settings.value) return
  draft[field.key] = enabled ? Number(settings.value.effective[field.key]) : null
}

/** 把可空布尔草稿映射为 Element Plus 支持的字符串选项。 */
function booleanSelection(field: FieldDefinition): 'inherit' | 'true' | 'false' {
  const value = draft[field.key]
  return value === null || value === undefined ? 'inherit' : value ? 'true' : 'false'
}

/** 将布尔下拉选项写回可空覆盖值。 */
function updateBooleanOverride(field: FieldDefinition, value: string): void {
  draft[field.key] = value === 'inherit' ? null : value === 'true'
}

/** 将数值输入写回覆盖草稿，清空时恢复继承。 */
function updateIntegerOverride(field: FieldDefinition, value: number | undefined): void {
  draft[field.key] = value ?? null
}

/** 返回逐字段有效值来源的用户可见标签。 */
function sourceLabel(field: MonitorField): string {
  return ({
    database: '数据库覆盖',
    environment: '环境变量',
    default: '代码默认',
  })[settings.value?.sources[field] || 'default']
}

/** 将审计动作代码映射为配置页操作名称。 */
function actionLabel(action: string): string {
  return action.endsWith('.reset') ? '重置全部覆盖' : '保存监控配置'
}

/** 把可空或布尔审计值转换为配置语义文本。 */
function auditValue(value: unknown): string {
  if (value === null || value === undefined) return '继承'
  if (value === true) return '开启'
  if (value === false) return '关闭'
  return String(value)
}

/** 逐字段生成审计事件的前后值差异摘要。 */
function changeSummary(event: AuditEvent): string {
  const before = event.old_values || {}
  const after = event.new_values || {}
  const changes = fields
    .filter(field => !Object.is(before[field.key], after[field.key]))
    .map(field =>
      `${field.label}: ${auditValue(before[field.key])} → ${auditValue(after[field.key])}`)
  return changes.length ? changes.join('；') : '无可展示字段变更'
}

onMounted(async () => {
  await Promise.all([loadSettings(), loadHistory()])
})
</script>

<template>
  <section class="admin-page">
  <CaPageHeader
    title="采集配置"
    description="覆盖七项监控参数。monitor将在更新后5秒内完成热加载。"
    :level="1"
    size="md"
  >
    <template #actions>
      <CaButton variant="secondary" :disabled="!canWrite" :loading="resetting" @click="resetAll">重置全部</CaButton>
      <CaButton variant="primary" :disabled="!canWrite" :loading="saving" @click="save">保存配置</CaButton>
    </template>
  </CaPageHeader>

  <CaErrorState v-if="pageError" class="page-notice" title="读取配置失败" :description="pageError" />
  <el-alert
    v-if="settings?.state === 'degraded'"
    class="page-notice"
    :title="settings.warning || '在线配置读取失败'"
    description="当前仅展示最后有效值或环境默认值；为避免覆盖未知版本，保存和重置已禁用。"
    type="warning"
    show-icon
    :closable="false"
  />

  <CaCard as="section" class="panel settings-panel" variant="outline" padding="md" v-loading="loading">
    <div class="settings-summary" v-if="settings">
      <span>版本 {{ settings.version ?? '不可用' }}</span>
      <span>最后修改：{{ settings.updated_at ? formatDate(settings.updated_at) : '尚未修改' }}</span>
      <span>修改人：{{ settings.updated_by?.username || '—' }}</span>
    </div>

    <div v-for="field in fields" :key="field.key" class="setting-row">
      <div class="setting-copy">
        <div class="setting-title">
          <strong>{{ field.label }}</strong>
          <CaBadge tone="neutral">{{ sourceLabel(field.key) }}</CaBadge>
        </div>
        <p>{{ field.description }}</p>
        <small v-if="settings">
          当前有效值：{{ String(settings.effective[field.key]) }}{{ field.unit ? ` ${field.unit}` : '' }}
        </small>
      </div>
      <div class="setting-control">
        <el-select
          v-if="field.kind === 'boolean'"
          :model-value="booleanSelection(field)"
          :disabled="!canWrite"
          aria-label="布尔覆盖值"
          @update:model-value="(value: string) => updateBooleanOverride(field, value)"
        >
          <el-option label="继承环境/默认" value="inherit" />
          <el-option label="开启" value="true" />
          <el-option label="关闭" value="false" />
        </el-select>
        <template v-else>
          <el-switch
            :model-value="draft[field.key] !== null && draft[field.key] !== undefined"
            :disabled="!canWrite"
            active-text="覆盖"
            inactive-text="继承"
            @change="(value: string | number | boolean) => enableIntegerOverride(field, Boolean(value))"
          />
          <el-input-number
            v-if="draft[field.key] !== null && draft[field.key] !== undefined"
            :model-value="Number(draft[field.key])"
            :min="settings?.limits[field.key].minimum"
            :max="settings?.limits[field.key].maximum"
            :disabled="!canWrite"
            controls-position="right"
            @update:model-value="(value: number | undefined) => updateIntegerOverride(field, value)"
          />
          <span v-if="field.unit && draft[field.key] !== null && draft[field.key] !== undefined" class="setting-unit">{{ field.unit }}</span>
        </template>
      </div>
    </div>
  </CaCard>

  <CaCard as="section" class="panel" variant="outline" padding="md">
    <div class="panel-header">
      <div>
        <h2>配置变更记录</h2>
      </div>
    </div>
    <el-table :data="history" v-loading="historyLoading" table-layout="auto" empty-text="尚无配置变更记录">
      <el-table-column label="时间" min-width="180"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
      <el-table-column
        prop="actor_username"
        label="管理员"
        min-width="130"
        show-overflow-tooltip
      />
      <el-table-column label="动作" min-width="150"><template #default="{ row }">{{ actionLabel(row.action) }}</template></el-table-column>
      <el-table-column label="结果" min-width="100"><template #default="{ row }"><CaBadge :tone="statusTone(row.result)">{{ row.result }}</CaBadge></template></el-table-column>
      <el-table-column label="变更内容" min-width="360"><template #default="{ row }">{{ changeSummary(row) }}</template></el-table-column>
      <el-table-column
        prop="error_code"
        label="错误码"
        min-width="130"
        show-overflow-tooltip
      />
      <el-table-column prop="request_id" label="Request ID" min-width="260" />
    </el-table>
    <div class="history-more" v-if="nextBeforeId">
      <CaButton variant="secondary" :loading="historyLoading" @click="loadHistory(true)">加载更多</CaButton>
    </div>
  </CaCard>
  </section>
</template>
