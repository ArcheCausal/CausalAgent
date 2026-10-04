export type HealthStatus = 'healthy' | 'warning' | 'error' | 'unknown'
export type MonitorSource = 'database' | 'environment' | 'default'

export interface SnapshotMeta {
  status?: HealthStatus
  observed_at?: string | null
  source_role?: string
  source_alias?: string
  is_estimate?: boolean
  is_stale?: boolean
  refresh_pending?: boolean
  warning?: string | null
  [key: string]: unknown
}

export interface DashboardData {
  realtime: SnapshotMeta
  sql_performance: SnapshotMeta
  capacity: SnapshotMeta
  integrity: SnapshotMeta
  agent_persistence_cleanup_runtime?: CleanupWorkerSnapshot
  agent_persistence_cleanup_outbox?: CleanupOutboxSnapshot
  refresh_policy: Record<string, unknown>
}

export interface CleanupWorkerSnapshot extends SnapshotMeta {
  worker_alias?: string
  worker_status?: 'idle' | 'processing' | 'stale' | 'stopped' | 'unknown' | string
  current_task_type?: 'checkpoint' | 'user_memory' | null
  started_at?: string | null
  heartbeat_at?: string | null
  current_outbox_id?: number | null
  run_success_count?: number
  run_failure_count?: number
  startup_success_count?: number
  startup_failure_count?: number
  heartbeat_interval_seconds?: number
  processing_started_at?: string | null
  processing_duration_seconds?: number | null
  current_processing_started_at?: string | null
  current_processing_duration_seconds?: number | null
  last_failure_at?: string | null
  last_error_present?: boolean
}

export interface CleanupOutboxItem {
  outbox_id: number
  task_type: 'checkpoint' | 'user_memory' | string
  thread_id: string | null
  user_id: number | null
  operation_id: string | null
  status: 'pending' | 'processing' | 'succeeded' | 'failed' | string
  attempts: number
  max_attempts: number
  available_at: string | null
  lease_expires_at: string | null
  created_at: string | null
  completed_at: string | null
  has_error: boolean
  error_state: 'failed' | 'lease_expired' | null
  is_due: boolean
  lease_expired: boolean
  priority?: number
}

export interface CleanupOutboxSnapshot extends SnapshotMeta {
  summary?: Partial<Record<CleanupTaskType, CleanupOutboxSummary>>
  items?: CleanupOutboxItem[]
}

export type CleanupTaskType = 'checkpoint' | 'user_memory'

export interface CleanupOutboxSummary {
  pending: number | null
  due_pending: number | null
  processing: number | null
  expired_processing: number | null
  failed: number | null
  latest_completed_at: string | null
  earliest_pending_at: string | null
}

export type MonitorField =
  | 'auto_refresh_enabled'
  | 'realtime_interval_seconds'
  | 'sql_interval_seconds'
  | 'table_capacity_interval_seconds'
  | 'slow_query_warning_delta'
  | 'integrity_enabled'
  | 'integrity_interval_seconds'

export type MonitorValueMap = Record<MonitorField, boolean | number>
export type MonitorOverrideMap = Record<MonitorField, boolean | number | null>

export interface MonitorLimit {
  type: 'boolean' | 'integer'
  minimum?: number
  maximum?: number
}

export interface MonitorSettings {
  version: number | null
  overrides: MonitorOverrideMap
  effective: MonitorValueMap
  sources: Record<MonitorField, MonitorSource>
  limits: Record<MonitorField, MonitorLimit>
  updated_by: { id: number; username: string | null } | null
  updated_at: string | null
  state: 'current' | 'degraded'
  warning: string | null
}

export interface AuditEvent {
  id: number
  actor_user_id: number | null
  actor_username: string
  action: string
  target_id: string | null
  old_values: Record<string, unknown> | null
  new_values: Record<string, unknown> | null
  result: 'success' | 'rejected' | 'failed'
  error_code: string | null
  request_id: string
  created_at: string
}

export interface Identity {
  isLoggedIn: boolean
  username?: string
  role?: 'user' | 'admin'
  csrf_token?: string
}

/** 由部署环境决定的前端地址；开发指向本机端口，预发指向网关同源路径。 */
export interface RuntimeConfig {
  grafana_url: string
}

export interface CursorPage<T> {
  items: T[]
  limit: number
  has_more: boolean
  next_cursor: string | null
}

export interface OverviewMetric {
  key: string
  label: string
  value: number
  is_estimate: boolean
  source_alias: string
}

export interface SnapshotSummary {
  snapshot_key: string
  observed_at: string | null
  refresh_requested_at: string | null
  status: HealthStatus
  warning: string | null
  source_alias: string
}

export interface BusinessOverview {
  metrics: OverviewMetric[]
  snapshots: SnapshotSummary[]
  observed_at: string
  source_alias: string
  is_estimate: boolean
}

export interface AdminUser {
  id: number
  username: string
  role: 'user' | 'admin'
  is_active: boolean
  created_at: string
  last_login_at: string | null
  auth_version?: number
  password_changed_at?: string | null
}

export type UserOperationAction = 'set_active' | 'set_role' | 'set_password'

export interface UserOperationPreviewItem {
  id: number
  username: string
  current: { role: 'user' | 'admin'; is_active: boolean }
  next: Partial<{
    role: 'user' | 'admin'
    is_active: boolean
    password_changed: boolean
  }>
  blockers: string[]
}

export interface UserOperationPreview {
  action: UserOperationAction
  target_count: number
  items: UserOperationPreviewItem[]
  can_execute: boolean
  requires_reauthentication: boolean
  batch_limit: number
}

export interface AdminOperationResult {
  operation_id: string
  operation_type: string
  status?: 'running' | 'succeeded' | 'failed'
  target_count: number
  succeeded_count?: number
  failed_count?: number
  replayed: boolean
  completed_at?: string | null
  checkpoint_cleanup?: {
    status: 'pending' | 'succeeded' | 'failed'
    total: number
    succeeded: number
    failed: number
    pending: number
  }
  user_memory_cleanup?: {
    status: 'pending' | 'succeeded' | 'failed'
    total: number
    succeeded: number
    failed: number
    pending: number
  }
  items?: Array<{
    id: number
    username: string
    changed: boolean
    role: 'user' | 'admin'
    is_active: boolean
    auth_version: number
  }>
  deleted?: boolean
  user_id?: number
  username?: string
  file_id?: number
  filename?: string
  blob_deleted?: boolean
}

export interface UserDeleteImpact {
  user: AdminUser
  impact: Record<string, number>
  can_delete: boolean
  blockers: string[]
  requires_confirmation: string
  requires_reauthentication: boolean
  synchronous_delete_limit: number
}

export interface AdminSession {
  id: string
  user_id: number
  username: string
  title: string | null
  created_at: string
  last_activity_at: string
  message_count: number
  is_archived: boolean
  archived_at: string | null
}

export interface AdminMessage {
  id: number
  session_id: string
  user_id: number
  username: string
  message_type: 'user' | 'ai'
  content_preview: string
  content_length: number
  has_attachment: boolean
  attachment_count: number
  created_at: string
}

export interface AdminAttachment {
  id: number
  message_id: number
  attachment_type: string
  content_size: number
  created_at: string
}

export interface AdminJob {
  row_id?: number
  job_id: string
  user_id: number
  username: string
  session_id: string
  status: 'queued' | 'running' | 'waiting_input' | 'succeeded' | 'failed' | 'canceled'
  execution_state: 'leased' | 'draining' | null
  worker_id: string | null
  lease_epoch: number
  attempt_count: number
  recovery_count: number
  resume_count: number
  max_attempts: number
  error_preview?: string | null
  has_input?: boolean
  has_result: boolean
  has_error?: boolean
  locked_at: string | null
  heartbeat_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  execution_released_at?: string | null
  execution_release_reason?: 'worker_confirmed' | 'lease_expired' | null
  chat_saved_at: string | null
  input_user_file_id?: number | null
  input_file_hash?: string | null
  input_filename?: string | null
  current_question_id?: string | null
  current_waiting_prompt?: string | null
  input_count?: number
  inputs?: AdminJobInput[]
  inputs_truncated?: boolean
}

export interface AdminJobInput {
  input_id: number
  sequence: number
  input_type: 'initial' | 'resume'
  question_id: string | null
  created_at: string
  input_bytes: number
}

export interface AdminJobEvent {
  id: number
  job_id: string
  event_type: string
  created_at: string
  has_payload: boolean
  node_name: string | null
  node_desc: string | null
  duration_seconds: number | null
}

export interface AgentWorkerSummary {
  jobs: Record<string, unknown>[]
  summary: {
    queued?: number | null
    running?: number | null
    waiting_input?: number | null
    stale?: number | null
    max_attempts_running?: number | null
    draining?: number | null
    oldest_draining_age_seconds?: number | null
    worker_confirmed?: number | null
    lease_expired?: number | null
  }
  meta: SnapshotMeta
}

export interface AdminCheckpointSummary {
  checkpoint_id: string
  parent_checkpoint_id: string | null
  checkpoint_ns: string
  created_at: string | null
  step: number | null
  source: string | null
  updated_channels: string[]
}

export interface AdminCheckpointPage extends CursorPage<AdminCheckpointSummary> {
  source_alias: 'checkpoint-postgres'
  attribution: 'thread_id+metadata.job_id'
  legacy_unattributed: boolean
}

export interface AdminFile {
  id: number
  user_id: number
  username: string
  filename: string
  original_filename: string
  file_hash: string
  mime_type: string
  file_size: number
  upload_timestamp: string
  last_accessed_at: string
  access_count: number
  object_reference_count: number
}

export interface FileDeleteImpact {
  file: AdminFile
  impact: {
    database_rows: number
    blob_bytes: number
    owner_active_jobs: number
    object_reference_count: number
  }
  can_delete: boolean
  blockers: string[]
  requires_confirmation: string
  requires_reauthentication: boolean
  recycle_bin: false
}

export interface SensitiveContentChunk {
  content: string
  offset: number
  limit: number
  total_length: number
  complete: boolean
  next_offset: number | null
  kind?: 'input' | 'result' | 'error'
}

export interface CsvPreview {
  file_id: number
  filename: string
  mime_type: string
  encoding: string
  columns: string[]
  rows: string[][]
  truncated: boolean
  limits: {
    bytes: number
    rows: number
    columns: number
    cell_chars: number
  }
}

export interface DeepAuditCheck {
  key: string
  label: string
  status: HealthStatus
  summary: string
  details: unknown
}

export interface DeepAuditSnapshot extends SnapshotMeta {
  mode: 'deep'
  auto_scheduled?: boolean
  sample_limit?: number
  query_timeout_ms?: number
  checks: DeepAuditCheck[]
}

export interface QuickAuditCheck {
  key: string
  label: string
  status: HealthStatus
  severity?: 'blocking' | 'warning' | string
  applicable?: boolean
  value?: number | null
  description?: string
  warning?: string | null
  [key: string]: unknown
}

export interface QuickAuditSnapshot extends SnapshotMeta {
  mode?: string
  blocking_count?: number
  blocking_record_count?: number
  checks: QuickAuditCheck[]
}
