<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { InteractionIdentity, ProcessingOptionAction, ProcessingOptionStep, WaitingInput } from '../types/domain'

const props = defineProps<{
  waiting: WaitingInput
  identity: InteractionIdentity
  submitting?: boolean
  serverExpired?: boolean
  errorMessage?: string | null
}>()
const emit = defineEmits<{
  'choose': [action: ProcessingOptionAction]
  'other': [identity: InteractionIdentity]
}>()

const now = ref(Date.now())
let timer: number | null = null
const isOpenTextQuestion = computed(() =>
  props.waiting.interactionType !== 'data_processing_plan_confirmation' && props.waiting.options.length === 0,
)
const isPlanConfirmation = computed(() =>
  props.waiting.interactionType === 'data_processing_plan_confirmation'
    && Boolean(props.waiting.planVersion)
    && Boolean(props.waiting.plan),
)

const remainingSeconds = computed(() => {
  if (props.waiting.status !== 'waiting' || !props.waiting.expiresAt) return null
  const deadline = Date.parse(props.waiting.expiresAt)
  if (!Number.isFinite(deadline)) return null
  return Math.max(0, Math.ceil((deadline - now.value) / 1000))
})
const expired = computed(() =>
  props.waiting.status === 'expired'
    || (props.waiting.status === 'waiting'
      && (props.waiting.expired || Boolean(props.serverExpired)
        || (remainingSeconds.value !== null && remainingSeconds.value <= 0))),
)
const isWaiting = computed(() => props.waiting.status === 'waiting' && !expired.value)
const identityMatches = computed(() => Boolean(props.identity.jobId)
  && Boolean(props.identity.questionId)
  && props.identity.questionId === props.waiting.questionId
  && props.identity.planVersion === props.waiting.planVersion,
)
const disabled = computed(() => !isWaiting.value || props.submitting || !identityMatches.value)
const countdown = computed(() => {
  const seconds = remainingSeconds.value
  if (seconds === null) return ''
  const minutes = Math.floor(seconds / 60)
  const rest = String(seconds % 60).padStart(2, '0')
  return minutes + ':' + rest
})

const METHOD_LABELS: Record<string, string> = {
  mean: '均值',
  median: '中位数',
  ordinal: '按确定性顺序编码',
}

function stepDescription(step: ProcessingOptionStep): string {
  const columns = step.columns.map((column) => `“${column}”`).join('、')
  if (!columns) return '未指定作用列'
  if (step.operation === 'select_columns') return `只保留${columns}，其余列不参与分析`
  if (step.operation === 'drop_rows') return `删除${columns}中含缺失值的行`
  if (step.operation === 'numeric_impute') {
    const method = step.method ? METHOD_LABELS[step.method] : null
    return method
      ? `对${columns}的缺失值使用该列${method}填补`
      : `对${columns}的缺失值执行数值插补（未指定方法）`
  }
  if (step.operation === 'categorical_encode') {
    return `将${columns}按确定性顺序编码为数值`
  }
  return `对${columns}执行 ${step.operation}`
}

function stopTimer(): void {
  if (timer !== null) window.clearInterval(timer)
  timer = null
}

watch(
  () => [props.identity.jobId, props.waiting.questionId, props.waiting.planVersion,
    props.waiting.expiresAt, props.waiting.status, props.waiting.expired, props.serverExpired],
  () => {
    stopTimer()
    now.value = Date.now()
    if (!isWaiting.value || remainingSeconds.value === null) return
    timer = window.setInterval(() => {
      now.value = Date.now()
      if (expired.value) stopTimer()
    }, 1000)
  },
  { immediate: true, flush: 'sync' },
)
onBeforeUnmount(stopTimer)

function actionIdentity(): InteractionIdentity | null {
  // 点击时重新核对截止时间，防止两次计时更新之间提交已过期的问题。
  now.value = Date.now()
  if (expired.value) stopTimer()
  if (disabled.value) return null
  return {
    jobId: props.identity.jobId,
    questionId: props.identity.questionId,
    planVersion: props.identity.planVersion,
  }
}

function choose(optionId: string): void {
  const identity = actionIdentity()
  if (!identity) return
  if (optionId === '__confirm_processing_plan__' ? !isPlanConfirmation.value
    : !props.waiting.options.some((option) => option.optionId === optionId)) return
  emit('choose', { ...identity, optionId })
}

function other(): void {
  const identity = actionIdentity()
  if (!identity || !props.waiting.allowOther) return
  emit('other', identity)
}
</script>

<template>
  <section class="processing-options" :aria-label="isOpenTextQuestion ? '数据问题回答' : '数据处理方案确认'" :aria-busy="isWaiting && submitting ? 'true' : 'false'">
    <p v-if="waiting.status === 'answered'" class="processing-options-status">已回答</p>
    <p v-else-if="waiting.status === 'closed'" class="processing-options-status">该交互已结束。</p>
    <p v-else-if="expired" class="processing-options-expired">该确认已过期，当前任务不能继续提交。</p>
    <p v-else class="processing-options-hint">
      {{ isOpenTextQuestion ? '请在下方输入框回答这个问题后继续。' : isPlanConfirmation ? '请检查方案。只有确认后才会执行脚本；修改或拒绝时，请在下方说明。' : '请选择一种处理方式；其他要求可在下方输入框说明。' }}
    </p>
    <div v-if="isPlanConfirmation && waiting.plan" class="processing-plan-summary">
      <p>{{ waiting.plan.summary }}</p>
      <dl>
        <dt>观察单位</dt>
        <dd>{{ waiting.plan.observationUnit }}</dd>
        <template v-if="waiting.plan.roleColumns?.target">
          <dt>结果变量对应列</dt>
          <dd>{{ waiting.plan.roleColumns.target }}</dd>
        </template>
        <template v-if="waiting.plan.roleColumns?.treatment">
          <dt>处理变量对应列</dt>
          <dd>{{ waiting.plan.roleColumns.treatment }}</dd>
        </template>
        <dt>处理步骤</dt>
        <dd>
          <ul>
            <li v-for="(step, index) in waiting.plan.transformations" :key="index">{{ step }}</li>
          </ul>
        </dd>
        <dt>字段含义</dt>
        <dd>
          <ul>
            <li v-for="(meaning, variable) in waiting.plan.variableSemantics" :key="variable">
              <strong>{{ variable }}</strong>：{{ meaning }}
            </li>
          </ul>
        </dd>
        <template v-if="waiting.plan.limitations.length">
          <dt>限制</dt>
          <dd>
            <ul>
              <li v-for="(limitation, index) in waiting.plan.limitations" :key="index">{{ limitation }}</li>
            </ul>
          </dd>
        </template>
      </dl>
    </div>
    <ul v-if="waiting.options.length" class="processing-option-list">
      <li
        v-for="option in waiting.options"
        :key="option.optionId"
        class="processing-option"
        :class="{ selected: waiting.selectedOptionId === option.optionId }"
      >
        <button
          v-if="isWaiting"
          type="button"
          class="processing-option-button"
          :disabled="disabled"
          :aria-pressed="waiting.selectedOptionId === option.optionId"
          @click="choose(option.optionId)"
        >
          <strong v-if="option.steps.length">
            {{ option.steps.map(stepDescription).join('；') }}
          </strong>
          <strong v-else>{{ option.label }}</strong>
          <span class="processing-option-algorithm">
            {{ waiting.algorithmName || option.capabilityId }}
          </span>
        </button>
        <strong v-else>{{ option.steps.length ? option.steps.map(stepDescription).join('；') : option.label }}</strong>
        <ul v-if="option.steps.length" class="processing-option-steps">
          <li v-for="(step, index) in option.steps" :key="index">
            {{ stepDescription(step) }}
          </li>
        </ul>
        <ul v-if="option.impact.length" class="processing-option-notes">
          <li v-for="(item, index) in option.impact" :key="index">{{ item }}</li>
        </ul>
        <ul v-if="option.risks.length" class="processing-option-risks">
          <li v-for="(item, index) in option.risks" :key="index">{{ item }}</li>
        </ul>
      </li>
    </ul>
    <p v-if="isWaiting && errorMessage" class="processing-options-error" role="alert">{{ errorMessage }}</p>
    <button
      v-if="isWaiting && isPlanConfirmation"
      type="button"
      class="processing-option-button processing-plan-confirm"
      :disabled="disabled"
      @click="choose('__confirm_processing_plan__')"
    >
      {{ submitting ? '正在提交…' : '确认方案并继续' }}
    </button>
    <button
      v-if="isWaiting && waiting.allowOther"
      type="button"
      class="processing-option-other"
      :disabled="disabled"
      @click="other"
    >
      {{ isOpenTextQuestion ? '输入回答' : isPlanConfirmation ? '修改或拒绝方案' : '其他要求' }}
    </button>
    <p v-if="isWaiting && submitting && !isPlanConfirmation" class="processing-options-status" role="status">正在提交…</p>
    <p v-if="isWaiting && countdown" class="processing-options-countdown">剩余 {{ countdown }}</p>
  </section>
</template>
