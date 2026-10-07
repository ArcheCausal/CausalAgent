import { z } from 'zod'
import type { PublicEvent } from '../types/domain'

const phaseEventBase = {
  step_id: z.string().min(1),
  node_name: z.string().min(1),
  title: z.string().min(1),
}

export const nodeStartEventSchema = z.object({
  type: z.literal('node_start'),
  ...phaseEventBase,
}).passthrough()

export const progressEventSchema = z.object({
  type: z.literal('progress'),
  ...phaseEventBase,
  summary: z.string(),
}).passthrough()

export const decisionEventSchema = z.object({
  type: z.literal('decision'),
  ...phaseEventBase,
  summary: z.string(),
  // 公共决策的类型、工具名和置信度是后端新增的可选字段；这里只做字符串校验，
  // 未知取值按普通说明展示，避免后端新增枚举值时整条事件流进入协议错误。
  decision_kind: z.string().optional(),
  tool_name: z.string().optional(),
  confidence: z.string().optional(),
}).passthrough()

export const decisionDeltaEventSchema = z.object({
  type: z.literal('decision_delta'),
  ...phaseEventBase,
  stream_id: z.string().min(1),
  sequence: z.number().int().positive(),
  delta: z.string(),
  decision_kind: z.string().min(1),
  tool_name: z.string(),
}).passthrough()

export const toolCallStartEventSchema = z.object({
  type: z.literal('tool_call_start'),
  ...phaseEventBase,
  tool_name: z.string(),
  argument_keys: z.array(z.string()),
}).passthrough()

export const toolCallResultEventSchema = z.object({
  type: z.literal('tool_call_result'),
  ...phaseEventBase,
  tool_name: z.string(),
  summary: z.string(),
}).passthrough()

export const nodeRetryEventSchema = z.object({
  type: z.literal('node_retry'),
  ...phaseEventBase,
  message: z.string(),
  discard_stream_id: z.string().optional(),
}).passthrough()

export const nodeEndEventSchema = z.object({
  type: z.literal('node_end'),
  ...phaseEventBase,
  duration: z.number().nonnegative(),
  status: z.enum(['completed', 'failed']),
  message: z.string().optional(),
}).passthrough()

export const textDeltaEventSchema = z.object({
  type: z.literal('text_delta'),
  step_id: z.string().min(1),
  stream_id: z.string().min(1),
  sequence: z.number().int().positive(),
  delta: z.string(),
}).passthrough()

export const finalResultEventSchema = z.object({
  type: z.literal('final_result'),
  data: z.unknown(),
}).passthrough()

export const interruptEventSchema = z.object({
  type: z.literal('interrupt'),
  message: z.string(),
  question_id: z.string().min(1),
}).passthrough()

export const errorEventSchema = z.object({
  type: z.literal('error'),
  message: z.string(),
}).passthrough()

export const canceledEventSchema = z.object({
  type: z.literal('canceled'),
  message: z.string(),
}).passthrough()

export const heartbeatEventSchema = z.object({
  type: z.literal('heartbeat'),
  job_id: z.string().min(1),
}).passthrough()

export const publicEventSchema = z.union([
  nodeStartEventSchema,
  progressEventSchema,
  decisionEventSchema,
  decisionDeltaEventSchema,
  toolCallStartEventSchema,
  toolCallResultEventSchema,
  nodeRetryEventSchema,
  nodeEndEventSchema,
  textDeltaEventSchema,
  finalResultEventSchema,
  interruptEventSchema,
  errorEventSchema,
  canceledEventSchema,
  heartbeatEventSchema,
])

export const unknownPublicEventSchema = z.object({
  type: z.string().min(1),
}).passthrough()

export const publicEventTypes = new Set([
  'node_start', 'progress', 'decision', 'tool_call_start',
  'tool_call_result', 'node_retry', 'node_end', 'text_delta',
  'decision_delta', 'final_result', 'interrupt', 'error', 'canceled', 'heartbeat',
])

export type KnownPublicEvent = z.infer<typeof publicEventSchema>
export type UnknownPublicEvent = z.infer<typeof unknownPublicEventSchema>

export interface RawSseBlock {
  event: string
  id: string | null
  hasIdField: boolean
  data: string
}

export interface DecodedSseEvent {
  event: string
  id: number | null
  data: PublicEvent
  known: boolean
}

export class SseProtocolError extends Error {
  readonly code = 'sse_protocol_error'
  readonly rawBlock: RawSseBlock | null

  constructor(message: string, rawBlock: RawSseBlock | null = null) {
    super(message)
    this.name = 'SseProtocolError'
    this.rawBlock = rawBlock
  }
}

function parseEventId(raw: RawSseBlock): number | null {
  if (!raw.hasIdField || raw.id === null || raw.id === '') return null
  if (!/^\d+$/.test(raw.id)) {
    throw new SseProtocolError('SSE 事件 ID 必须是非负整数文本', raw)
  }
  const id = Number(raw.id)
  if (!Number.isSafeInteger(id) || id < 1) {
    throw new SseProtocolError('SSE 事件 ID 超出有效范围', raw)
  }
  return id
}

export function decodeSseBlock(raw: RawSseBlock): DecodedSseEvent {
  const event = raw.event.trim()
  if (!event) throw new SseProtocolError('SSE 事件名称不能为空', raw)
  if (!raw.data) throw new SseProtocolError('SSE 事件缺少 data 字段', raw)

  let parsed: unknown
  try {
    parsed = JSON.parse(raw.data)
  } catch {
    throw new SseProtocolError('SSE data 不是合法 JSON', raw)
  }

  const generic = unknownPublicEventSchema.safeParse(parsed)
  if (!generic.success) {
    throw new SseProtocolError('SSE data 必须是带 type 的对象', raw)
  }
  if (generic.data.type !== event) {
    throw new SseProtocolError('SSE event 与 data.type 不一致', raw)
  }

  const id = parseEventId(raw)
  if (publicEventTypes.has(event)) {
    const result = publicEventSchema.safeParse(parsed)
    if (!result.success) {
      throw new SseProtocolError('SSE 公共事件字段不符合协议', raw)
    }
    if (event === 'heartbeat' && id !== null) {
      throw new SseProtocolError('heartbeat 不应推进业务事件游标', raw)
    }
    if (event !== 'heartbeat' && id === null) {
      throw new SseProtocolError('业务 SSE 事件必须带事件 ID', raw)
    }
    return { event, id, data: result.data as PublicEvent, known: true }
  }

  if (id === null) {
    throw new SseProtocolError('未知 SSE 事件必须带有效事件 ID', raw)
  }
  return { event, id, data: generic.data as PublicEvent, known: false }
}

function lineEndIndex(value: string): number {
  const lf = value.indexOf('\n')
  const cr = value.indexOf('\r')
  if (lf === -1) return cr
  if (cr === -1) return lf
  return Math.min(lf, cr)
}

export class SseParser {
  private buffer = ''
  private event = 'message'
  private id: string | null = null
  private hasIdField = false
  private dataLines: string[] = []

  push(chunk: string): RawSseBlock[] {
    this.buffer += chunk
    const blocks: RawSseBlock[] = []
    while (true) {
      const end = lineEndIndex(this.buffer)
      if (end < 0) break
      const isCr = this.buffer[end] === '\r'
      if (isCr && end === this.buffer.length - 1) break
      const separatorLength = isCr && this.buffer[end + 1] === '\n' ? 2 : 1
      const line = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + separatorLength)
      const block = this.consumeLine(line)
      if (block) blocks.push(block)
    }
    return blocks
  }

  finish(): RawSseBlock[] {
    const blocks: RawSseBlock[] = []
    if (this.buffer) {
      const block = this.consumeLine(this.buffer)
      if (block) blocks.push(block)
      this.buffer = ''
    }
    const finalBlock = this.dispatch()
    if (finalBlock) blocks.push(finalBlock)
    return blocks
  }

  private consumeLine(line: string): RawSseBlock | null {
    if (line === '') return this.dispatch()
    if (line.startsWith(':')) return null

    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    let value = separator < 0 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') this.event = value
    else if (field === 'id') {
      this.id = value
      this.hasIdField = true
    } else if (field === 'data') this.dataLines.push(value)
    return null
  }

  private dispatch(): RawSseBlock | null {
    if (this.dataLines.length === 0) {
      this.event = 'message'
      this.id = null
      this.hasIdField = false
      return null
    }
    const block: RawSseBlock = {
      event: this.event,
      id: this.id,
      hasIdField: this.hasIdField,
      data: this.dataLines.join('\n'),
    }
    this.event = 'message'
    this.id = null
    this.hasIdField = false
    this.dataLines = []
    return block
  }
}

export async function consumeSseResponse(
  response: Response,
  onEvent: (event: DecodedSseEvent) => Promise<void> | void,
): Promise<void> {
  if (!response.body) throw new SseProtocolError('SSE 响应没有可读取的 body')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      const text = decoder.decode(chunk.value, { stream: true })
      for (const block of parser.push(text)) await onEvent(decodeSseBlock(block))
    }
    const tail = decoder.decode()
    for (const block of parser.push(tail)) await onEvent(decodeSseBlock(block))
    for (const block of parser.finish()) await onEvent(decodeSseBlock(block))
  } finally {
    reader.releaseLock()
  }
}
