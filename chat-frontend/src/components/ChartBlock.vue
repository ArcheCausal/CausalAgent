<script setup lang="ts">
import { computed } from 'vue'
import type { ChartAsset } from '../types/domain'

const props = defineProps<{
  title: string | null
  asset: ChartAsset | null
  error: string | null
}>()

interface HistogramBar {
  key: string
  label: string
  count: number
  ratio: number
}

interface BarRow {
  key: string
  label: string
  count: number
  ratio: number
}

function formatNumber(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ''
  return Number(value.toPrecision(4)).toString()
}

const histogramBars = computed<HistogramBar[]>(() => {
  const asset = props.asset
  if (!asset || asset.chart_type !== 'histogram') return []
  const bins = asset.data.bins ?? []
  const counts = asset.data.counts ?? []
  const max = Math.max(1, ...counts)
  return counts.map((count, index) => {
    const start = formatNumber(bins[index])
    const end = formatNumber(bins[index + 1])
    return {
      key: `${start}-${end}-${index}`,
      label: `${start} ~ ${end}`,
      count,
      ratio: count / max,
    }
  })
})

const barRows = computed<BarRow[]>(() => {
  const asset = props.asset
  if (!asset || asset.chart_type !== 'bar') return []
  const categories = asset.data.categories ?? []
  const counts = asset.data.counts ?? []
  const max = Math.max(1, ...counts)
  return categories.map((category, index) => {
    const count = counts[index] ?? 0
    return {
      key: `${category}-${index}`,
      label: category,
      count,
      ratio: count / max,
    }
  })
})

const heatmap = computed(() => {
  const asset = props.asset
  if (!asset || asset.chart_type !== 'heatmap') return null
  const variables = asset.data.variables ?? []
  const matrix = asset.data.matrix ?? []
  if (variables.length < 2 || matrix.length !== variables.length) return null
  return { variables, matrix }
})

function heatStyle(value: number): Record<string, string> {
  const clamped = Math.max(-1, Math.min(1, value))
  const alpha = Math.min(0.22, Math.abs(clamped) * 0.22)
  return { background: 'rgba(23, 23, 23, ' + alpha.toFixed(3) + ')' }
}

function barOpacity(ratio: number): number {
  const relativeCount = Math.max(0, Math.min(1, ratio))
  return Number((0.24 + relativeCount * 0.76).toFixed(3))
}

const chartLabel = computed(() => props.title ?? '数据图表')
</script>

<template>
  <figure class="report-chart">
    <figcaption v-if="title" class="report-chart-title">{{ title }}</figcaption>
    <p v-if="error" class="report-chart-state" role="note">{{ error }}</p>
    <template v-else-if="asset?.chart_type === 'histogram'">
      <p v-if="!histogramBars.length" class="report-chart-state" role="note">暂无数据。</p>
      <div v-else class="report-histogram">
        <svg
          class="report-histogram-svg"
          :viewBox="`0 0 ${histogramBars.length * 10} 100`"
          preserveAspectRatio="none"
          role="img"
          :aria-label="`${chartLabel}直方图`"
        >
          <rect
            v-for="(bar, index) in histogramBars"
            :key="bar.key"
            :x="index * 10 + 1"
            :y="100 - bar.ratio * 96"
            width="8"
            :height="Math.max(2, bar.ratio * 96)"
            :opacity="barOpacity(bar.ratio)"
          >
            <title>{{ `${bar.label}：${bar.count}` }}</title>
          </rect>
        </svg>
        <p class="report-chart-hint">
          区间 {{ histogramBars[0]?.label }} 至 {{ histogramBars[histogramBars.length - 1]?.label }}
        </p>
      </div>
    </template>
    <template v-else-if="asset?.chart_type === 'bar'">
      <p v-if="!barRows.length" class="report-chart-state" role="note">暂无数据。</p>
      <ul v-else class="report-bar-list" :aria-label="`${chartLabel}柱状图`">
        <li v-for="row in barRows" :key="row.key" class="report-bar-row">
          <span class="report-bar-label" :title="row.label">{{ row.label }}</span>
          <svg class="report-bar-track" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">
            <rect x="0" y="0" :width="Math.max(0.5, row.ratio * 100)" height="10" :opacity="barOpacity(row.ratio)" />
          </svg>
          <span class="report-bar-value">{{ row.count }}</span>
        </li>
      </ul>
    </template>
    <template v-else-if="heatmap">
      <div class="report-heatmap" :style="{ gridTemplateColumns: `minmax(64px, auto) repeat(${heatmap.variables.length}, minmax(0, 1fr))` }">
        <span class="report-heatmap-corner"></span>
        <span v-for="variable in heatmap.variables" :key="`head-${variable}`" class="report-heatmap-head">{{ variable }}</span>
        <template v-for="(rowName, rowIndex) in heatmap.variables" :key="`row-${rowName}`">
          <span class="report-heatmap-head">{{ rowName }}</span>
          <span
            v-for="(columnName, columnIndex) in heatmap.variables"
            :key="`cell-${rowName}-${columnName}`"
            class="report-heatmap-cell"
            :style="heatStyle(heatmap.matrix[rowIndex]?.[columnIndex] ?? 0)"
            :title="`${rowName} × ${columnName}：${formatNumber(heatmap.matrix[rowIndex]?.[columnIndex])}`"
          >{{ formatNumber(heatmap.matrix[rowIndex]?.[columnIndex]) }}</span>
        </template>
      </div>
      <p class="report-chart-hint">颜色越深表示相关性绝对值越大。</p>
    </template>
    <p v-else class="report-chart-state" role="note">暂无数据。</p>
  </figure>
</template>

<style scoped>
.report-chart {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 14px 16px;
  background: var(--report-surface, #ffffff);
  border: 1px solid var(--report-border, #e2e5e9);
  border-radius: var(--report-card-radius, 8px);
}

.report-chart-title {
  font-size: var(--report-chart-title-size, 15px);
  font-weight: 400;
}

.report-chart-state,
.report-chart-hint {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-muted, #6b7280);
}

.report-histogram {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.report-histogram-svg {
  width: 100%;
  height: 180px;
  fill: var(--report-chart-color, #0067c0);
}

.report-bar-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.report-bar-row {
  display: grid;
  grid-template-columns: minmax(72px, 32%) minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  font-size: 13px;
}

.report-bar-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.report-bar-track {
  width: 100%;
  height: 12px;
  fill: var(--report-chart-color, #0067c0);
}

.report-bar-value {
  color: var(--color-text-muted, #6b7280);
  font-variant-numeric: tabular-nums;
}

.report-heatmap {
  display: grid;
  gap: 3px;
  align-items: center;
  font-size: 12px;
}

.report-heatmap-head {
  overflow: hidden;
  color: var(--color-text-muted, #6b7280);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.report-heatmap-cell {
  display: grid;
  min-height: 34px;
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
  place-items: center;
}

@media (max-width: 640px) {
  .report-bar-row {
    grid-template-columns: minmax(60px, 40%) minmax(0, 1fr) auto;
  }

  .report-histogram-svg {
    height: 140px;
  }
}
</style>
