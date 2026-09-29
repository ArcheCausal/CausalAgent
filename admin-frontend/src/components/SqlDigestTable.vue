<script setup lang="ts">
import { CaBadge, CaButton } from '@causalagent/design-system'
import { computed, ref } from 'vue'
import { displayValue, formatNumber } from '../lib/dashboard'
import { toSqlDigestView, type SqlDigestView } from '../lib/sqlSemantics'
import { statusTone } from '../lib/statusTone'

const props = defineProps<{
  statements: Record<string, unknown>[]
}>()

const detailVisible = ref(false)
const selectedStatement = ref<SqlDigestView | null>(null)

/** 将可能来自 JSON 数字或字符串的耗时转换为可排序数值，异常值统一视为空值。 */
function numericDuration(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const duration = Number(value)
  return Number.isFinite(duration) ? duration : null
}

/** 按降序比较两个可选耗时，并把缺失或非法值稳定放到有效数值之后。 */
function compareDurationDescending(left: unknown, right: unknown): number {
  const leftDuration = numericDuration(left)
  const rightDuration = numericDuration(right)
  if (leftDuration === null) return rightDuration === null ? 0 : 1
  if (rightDuration === null) return -1
  return rightDuration - leftDuration
}

/** 按平均耗时降序排列 Digest，同值时使用累计耗时作为次排序。 */
function compareSqlDigestLoad(left: SqlDigestView, right: SqlDigestView): number {
  return compareDurationDescending(left.averageSeconds, right.averageSeconds)
    || compareDurationDescending(left.totalSeconds, right.totalSeconds)
}

/** 以秒为单位格式化有效耗时，缺失或非法值显示统一占位符。 */
function formatDurationSeconds(value: unknown): string {
  return numericDuration(value) === null ? '—' : `${displayValue(value)} 秒`
}

const rows = computed(() => props.statements.map(toSqlDigestView).sort(compareSqlDigestLoad))

/** 打开指定 SQL 摘要的业务解释和原始字段详情。 */
function openDetails(row: SqlDigestView): void {
  selectedStatement.value = row
  detailVisible.value = true
}

/** 在抽屉关闭动画完成后清理当前行，避免残留不可见详情状态。 */
function clearDetails(): void {
  selectedStatement.value = null
}
</script>

<template>
  <el-table class="sql-business-table" :data="rows" table-layout="auto">
    <el-table-column label="业务模块" min-width="150" align="center">
      <template #default="{ row }">
        <CaBadge tone="neutral">{{ row.meaning.module }}</CaBadge>
      </template>
    </el-table-column>
    <el-table-column label="功能" min-width="240">
      <template #default="{ row }">
        <strong class="sql-business-action">{{ row.meaning.action }}</strong>
      </template>
    </el-table-column>
    <el-table-column label="平均耗时" min-width="130" align="right">
      <template #default="{ row }">
        <strong>{{ formatDurationSeconds(row.averageSeconds) }}</strong>
      </template>
    </el-table-column>
    <el-table-column label="业务说明" min-width="360">
      <template #default="{ row }">
        <span class="sql-business-description">{{ row.meaning.description }}</span>
      </template>
    </el-table-column>
    <el-table-column label="识别方式" min-width="110">
      <template #default="{ row }">
        <CaBadge :tone="statusTone(row.meaning.confidence)">
          {{ row.meaning.confidence === 'confirmed' ? '代码确认' : '推断' }}
        </CaBadge>
      </template>
    </el-table-column>
    <el-table-column label="操作" fixed="right" width="112">
      <template #default="{ row }">
        <CaButton variant="quiet" size="sm" @click="openDetails(row)">查看详情</CaButton>
      </template>
    </el-table-column>
  </el-table>

  <el-drawer
    v-model="detailVisible"
    class="sql-detail-drawer"
    direction="rtl"
    size="min(720px, 100vw)"
    destroy-on-close
    @closed="clearDetails"
  >
    <template #header>
      <div>
        <h3>SQL详情</h3>
      </div>
    </template>

    <div v-if="selectedStatement" class="sql-detail-content">
      <section class="sql-detail-business">
        <div class="sql-detail-tags">
          <CaBadge tone="neutral">{{ selectedStatement.meaning.module }}</CaBadge>
          <CaBadge :tone="statusTone(selectedStatement.meaning.confidence)">
            {{ selectedStatement.meaning.confidence === 'confirmed' ? '代码确认' : '推断' }}
          </CaBadge>
        </div>
        <strong>{{ selectedStatement.meaning.action }}</strong>
        <p>{{ selectedStatement.meaning.description }}</p>
        <div class="sql-detail-evidence">
          <span>判断依据</span>
          <code>{{ selectedStatement.meaning.evidence }}</code>
        </div>
      </section>

      <section class="sql-detail-section">
        <h4>Digest 模板</h4>
        <pre class="sql-detail-digest">{{ selectedStatement.digestText || '—' }}</pre>
      </section>

      <section class="sql-detail-section">
        <h4>统计信息</h4>
        <dl class="sql-raw-metrics">
          <div>
            <dt>执行次数</dt>
            <dd>{{ formatNumber(selectedStatement.countStar) }}</dd>
          </div>
          <div>
            <dt>累计总耗时</dt>
            <dd>{{ formatDurationSeconds(selectedStatement.totalSeconds) }}</dd>
          </div>
          <div>
            <dt>平均耗时</dt>
            <dd>{{ formatDurationSeconds(selectedStatement.averageSeconds) }}</dd>
          </div>
          <div>
            <dt>扫描行</dt>
            <dd>{{ formatNumber(selectedStatement.rowsExamined) }}</dd>
          </div>
          <div>
            <dt>返回行</dt>
            <dd>{{ formatNumber(selectedStatement.rowsSent) }}</dd>
          </div>
        </dl>
      </section>

      <div class="sql-detail-actions">
        <CaButton variant="secondary" @click="detailVisible = false">关闭详情</CaButton>
      </div>
    </div>
  </el-drawer>
</template>
