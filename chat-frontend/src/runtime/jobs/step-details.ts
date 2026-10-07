import type {
  DetailTone,
  StepDecisionDetail,
  StepDetail,
  StepTextDetail,
  ThinkingStep,
} from '../../types/domain'

/** 阶段明细事件的稳定读取方式；公共事件对前端是 unknown，缺字段时退化。 */
export function stringField(data: Record<string, unknown>, name: string): string {
  const value = data[name]
  return typeof value === 'string' ? value : ''
}

export function numberField(data: Record<string, unknown>, name: string): number | null {
  const value = data[name]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const DECISION_PREFIXES: Record<string, string> = {
  algorithm: '算法决策：',
  evidence: '检索决策：',
  final: '最终决策：',
}

export function decisionKindPrefix(decisionKind: string): string {
  return DECISION_PREFIXES[decisionKind] ?? ''
}

export function decisionStreamKey(stepId: string, decisionKind: string, toolName: string): string {
  return `${stepId}:${decisionKind}:${toolName}`
}

/** 把一条阶段明细事件转换成步骤详情文本，公开决策按类型加前缀。 */
export function stepDetailText(data: Record<string, unknown>): string {
  const type = stringField(data, 'type')
  if (type === 'decision') {
    return `${decisionKindPrefix(stringField(data, 'decision_kind'))}${stringField(data, 'summary')}`
  }
  if (type === 'tool_call_start') {
    const keys = Array.isArray(data.argument_keys)
      ? data.argument_keys.filter((key): key is string => typeof key === 'string')
      : []
    const fields = keys.length ? `（参数字段：${keys.join('、')}）` : ''
    return `调用工具：${stringField(data, 'tool_name')}${fields}`
  }
  if (type === 'tool_call_result') {
    return `${stringField(data, 'tool_name')}：${stringField(data, 'summary')}`
  }
  return stringField(data, 'summary')
}

export function textDetail(text: string, tone: DetailTone = 'default'): StepTextDetail {
  return { kind: 'text', text, tone }
}

export function decisionDetail(
  stepId: string,
  data: Record<string, unknown>,
  text: string,
): StepDecisionDetail {
  const decisionKind = stringField(data, 'decision_kind')
  const toolName = stringField(data, 'tool_name')
  return {
    kind: 'decision',
    key: decisionStreamKey(stepId, decisionKind, toolName),
    decisionKind,
    toolName,
    streamId: stringField(data, 'stream_id') || null,
    text,
    complete: false,
    released: false,
    pending: [],
  }
}

export function findDecisionDetail(step: ThinkingStep, key: string): StepDecisionDetail | null {
  for (const detail of step.details) {
    if (detail.kind === 'decision' && detail.key === key) return detail
  }
  return null
}

/** 同一工具尚未完成展示的公开决策；命中时后续工具事件必须等待。 */
export function openDecisionForTool(step: ThinkingStep, toolName: string): StepDecisionDetail | null {
  for (const detail of step.details) {
    if (detail.kind !== 'decision' || detail.released) continue
    if (detail.toolName !== toolName) continue
    if (detail.decisionKind !== 'algorithm' && detail.decisionKind !== 'evidence') continue
    return detail
  }
  return null
}

/** 放行一条公开决策：标记展示结束，并把被挂起的工具事件按到达顺序补在它后面。 */
export function releaseDecisionDetail(
  step: ThinkingStep,
  entry: StepDecisionDetail,
): void {
  const released = entry.pending.map((data) => textDetail(stepDetailText(data)))
  replaceDecisionDetail(step, entry, {
    ...entry,
    complete: true,
    released: true,
    pending: [],
  })
  appendDetails(step, released)
}

export function replaceDecisionDetail(
  step: ThinkingStep,
  entry: StepDecisionDetail,
  next: StepDecisionDetail,
): void {
  step.details = step.details.map((detail) => (detail === entry ? next : detail))
}

export function appendDetails(step: ThinkingStep, details: StepDetail[]): void {
  step.details = [...step.details, ...details]
}
