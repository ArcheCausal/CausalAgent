export type Locale = 'zh' | 'en'

export type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }

export interface Reference {
  title: string
  url: string
}

export interface CausalGraphData {
  nodes: Array<Record<string, unknown>>
  edges: Array<Record<string, unknown>>
}

export interface CausalGraphNode {
  id: string
  variable: string
  label: string
  metadata?: Record<string, unknown>
  evidence_refs?: string[]
}

export interface CausalGraphEdge {
  id: string
  source: string
  target: string
  edge_type: string
  weight?: number | null
  metadata?: Record<string, unknown>
  evidence_refs?: string[]
}

export interface CausalGraphModel {
  graph_id: string
  schema_version: number
  nodes: CausalGraphNode[]
  edges: CausalGraphEdge[]
  metadata?: Record<string, unknown>
}

export type ChartType = 'histogram' | 'bar' | 'heatmap'

export interface ChartAssetData {
  bins?: number[]
  counts?: number[]
  categories?: string[]
  variables?: string[]
  matrix?: number[][]
}

export interface ChartAsset {
  asset_key: string
  type: 'chart'
  chart_type: ChartType
  data: ChartAssetData
  metadata?: Record<string, unknown>
  options?: Record<string, unknown>
}

/* 报告文档的业务模型；后端已校验，前端只消费投影后的数据。 */
export interface ReportSource {
  source_id: string
  kind: string
  title: string
  file_id?: number | null
  url?: string | null
}

export interface ReportEvidence {
  evidence_id: string
  source_ids?: string[]
  locator?: Record<string, unknown>
  description: string
}

export type ReportAsset = ChartAsset | CausalGraphModel

export type ReportBlock =
  | { id: string; type: 'section'; title?: string | null; children: ReportBlock[] }
  | { id: string; type: 'markdown'; content: string; evidence_refs?: string[] }
  | { id: string; type: 'chart'; title?: string | null; asset_key: string }
  | { id: string; type: 'causal_graph'; title?: string | null; asset_key: string }

export interface ReportDocument {
  schema_version: number
  report_id: string
  title: string
  blocks: ReportBlock[]
  assets: Record<string, ReportAsset>
  sources: ReportSource[]
  evidence_refs: ReportEvidence[]
}

export interface StructuredMessage {
  type: string
  summary?: string
  layout?: string
  render_mode?: string
  document?: unknown
  data?: unknown
  references?: Reference[]
  [key: string]: unknown
}

export type MessageText = string | StructuredMessage

export interface ExecutionPhase {
  phaseSequence: number
  status: string
  elapsedSeconds: number
  lastEventId: number
  analysisJobId?: string
  analysisJobInputId?: number
  events: Array<Record<string, unknown>>
}

export interface ChatMessage {
  localId: string
  sender: 'user' | 'ai'
  text: MessageText
  analysisJobId?: string
  analysisJobInputId?: number
  fileAttachment?: { filename: string }
  references?: Reference[]
  thinkingAfter?: ExecutionPhase
  /** 本页实时执行到一半时被追问固定下来的执行记录；只存在于内存，不来自接口。 */
  frozenThinking?: ThinkingProjection
}

export interface SessionSummary {
  id: string
  preview: string
  lastTime: string
}

export interface UserFile {
  id: number
  userFileId: number
  filename: string
  mimeType: string
  fileSize: number
  uploadedAt: string | null
  lastAccessedAt: string | null
  accessCount: number
}

export type BackendJobStatus = 'queued' | 'running' | 'waiting_input' | 'succeeded' | 'failed' | 'canceled'
export type JobConnectionState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'
export type JobUiState = 'idle' | 'creating' | 'queued' | 'running' | 'waiting_input' | 'resuming' | 'canceling' | 'completed' | 'failed' | 'canceled'

export interface WaitingInput {
  questionId: string
  prompt: string
}

export type DetailTone = 'default' | 'error' | 'retry'

export interface StepTextDetail {
  kind: 'text'
  text: string
  tone: DetailTone
}

/**
 * 一次工具调用的公开决策。`text` 是已经接收的完整公开说明（不含前缀），
 * 展示层按码点逐步推进；`pending` 保存同一工具尚未放行的生命周期事件。
 */
export interface StepDecisionDetail {
  kind: 'decision'
  key: string
  decisionKind: string
  toolName: string
  streamId: string | null
  text: string
  /** 已收到完整 decision 或该决策流已经结束。 */
  complete: boolean
  /** 展示已经追平或被强制放行，之后的工具事件不再等待这条决策。 */
  released: boolean
  pending: Array<Record<string, unknown>>
}

export type StepDetail = StepTextDetail | StepDecisionDetail

export interface ThinkingStep {
  stepId: string
  nodeName: string
  title: string
  status: 'in-progress' | 'completed' | 'failed' | 'canceled'
  duration: number | null
  details: StepDetail[]
}

export interface ThinkingProjection {
  status: 'active' | 'completed' | 'waiting_input' | 'failed' | 'canceled'
  startedAt: number
  elapsedSeconds: number
  steps: Record<string, ThinkingStep>
  stepOrder: string[]
  pendingStepEvents: Record<string, Array<Record<string, unknown>>>
  draftStreamId: string | null
  draftText: string
  finalResult: StructuredMessage | null
  errorMessage: string | null
  waitingInput: WaitingInput | null
}

export interface StoredEvent {
  id: number
  type: string
  data: Record<string, unknown>
}

export interface PublicEventBase {
  type: string
  step_id?: string
  node_name?: string
  title?: string
  event_id?: number
  [key: string]: unknown
}

export type PublicEvent = PublicEventBase

export interface JobRecord {
  jobId: string
  sessionId: string
  /** 运行态记录当前代表的分析输入；null 表示还没有对应的历史阶段。 */
  phaseInputId: number | null
  backendStatus: BackendJobStatus
  uiState: JobUiState
  connection: JobConnectionState
  generation: number
  transportCursor: number
  resumeEventId: number
  renderedEventId: number
  observedLastEventId: number | null
  events: StoredEvent[]
  textSeenKeys: string[]
  textStreams: Record<string, string>
  thinking: ThinkingProjection
  errorMessage: string | null
  errorCode: string | null
  cancelKey: string | null
  cancelInFlight: boolean
}
