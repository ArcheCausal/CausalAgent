<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  CaBadge,
  CaButton,
  CaCard,
  CaEmptyState,
  CaErrorState,
  CaInput,
  CaLoadingState,
  CaPageHeader,
  CaTabs,
} from "@causalagent/design-system";
import brandMarkUrl from "../../../../packages/design-system/src/assets/brand/causalagent-mark.svg?url";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  CircleDot,
  Database,
  FileChartColumn,
  FileText,
  Gauge,
  GitCompare,
  LayoutDashboard,
  LoaderCircle,
  Minus,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  TrendingUp,
  Target,
  Upload,
} from "lucide-vue-next";
import {
  getReviewCounts,
  nextReviewState,
  summarizeEvaluationProgress,
} from "./reviewWorkflow";

type RunStatus = "created" | "queued" | "running" | "cancelling" | "staged" | "succeeded" | "cancelled" | "failed";
type NavId = "workspace" | "candidates" | "evaluation" | "release" | "reports";
type EvaluationSection = "config" | "events" | "comparison";
type ComparisonMode = "time_trend" | "run_diff" | "strategy_diff";
type ReportTab = "pipeline" | "retrieval" | "ragas";
type CandidateReviewPhase = "intro" | "review" | "complete";

interface SourceEntry {
  source_id: string;
  name: string;
  display_name?: string;
  size_bytes: number;
  content_sha256: string;
  source_kind?: "frozen" | "uploaded";
  page_count?: number | null;
}

interface ReleaseCheck {
  key: string;
  label: string;
  status: "pass" | "fail";
  blocking?: boolean;
  detail: string;
}

interface ReleaseStatus {
  state?: "ready_to_publish" | "blocked" | "published";
  publishable?: boolean;
  checked_at?: string;
  release?: {
    release_id?: string;
    ingestion_run_id?: string;
    index_version?: string;
    manifest_sha256?: string;
    source_count?: number;
    sources?: Array<Record<string, string | undefined>>;
    evaluation_run_id?: string;
  } | null;
  checks?: ReleaseCheck[];
  active?: Record<string, unknown> | null;
  previous?: Record<string, unknown> | null;
  generation?: number;
  candidates?: string[];
  candidate_overflow?: boolean;
  requires_worker_restart?: boolean;
}

interface PageRangeDraft { start: string; end: string; }

interface RunEvent {
  type: string;
  message?: string;
  timestamp?: string;
  data?: Record<string, unknown>;
}

interface RunState {
  run_id: string;
  kind: string;
  status: RunStatus;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  source_ids?: string[];
  source_names?: string[];
  source_label?: string;
  remote_enabled?: boolean;
  authorized_source_ids?: string[];
  max_pages?: number | null;
  page_ranges?: Array<{ source_id: string; start_page: number; end_page: number }>;
  current_stage?: string;
  index_version?: string;
  manifest_sha256?: string;
  unit_count?: number;
  vector_count?: number;
  staged_unit_count?: number;
  requested_candidate_count?: number;
  candidate_capacity?: number;
  question_count?: number;
  missing_count?: number;
  round?: number;
  ingestion_run_id?: string;
  evaluation_run_id?: string;
  old_revision?: string;
  new_revision?: string;
  protected_count?: number;
  diagnosed_count?: number;
  replaced_count?: number;
  rejected_candidate_count?: number;
  archived_dataset_path?: string;
  error?: string;
  result_available?: boolean;
  events?: RunEvent[];
  candidate_artifact_name?: string;
  audit_artifact_name?: string;
  review_manifest_artifact_name?: string;
  candidate_dataset_revision?: string;
  review_status?: string;
  batch_id?: string;
  batch_position?: number;
  batch_size?: number;
  strategy_profile?: { profile_id?: string; name?: string; kind?: string };
}

interface TuningDatasetRunState extends RunState {
  requested_count?: number;
  generated_count?: number;
  accepted_count?: number;
  rejected_count?: number;
  output_dataset_revision?: string;
  summary?: Record<string, number | string | null>;
  baseline_source?: string;
  carried_evidence_count?: number;
  dropped_fail_count?: number;
  fresh_evaluated_count?: number;
  reused_across_configs?: boolean;
}

interface EvaluationBatch {
  batch_id: string;
  run_count: number;
  runs: RunState[];
}

interface CandidateSample { sample_id: string; question: string; reference_answer: string; expected_claims?: string[]; gold_evidence?: Array<Record<string, unknown>>; }
interface CandidateDataset { dataset_id?: string; dataset_kind?: string; dataset_revision: string; source_snapshot?: Record<string, unknown>; samples: CandidateSample[]; }
interface CandidateAudit {
  coverage?: { selected_unit_count?: number; covered_unit_count?: number; samples_with_evidence?: number; };
  generated_candidate_count?: number;
  accepted_count?: number;
  rejected_count?: number;
  generation_errors?: string[];
}
interface CandidateReviewManifest { reviewer?: string; decisions?: Array<{ sample_id: string; decision: "approved" | "rejected" | "needs_revision"; note?: string }>; }
interface GoldDatasetStatus {
  exists: boolean;
  dataset_id?: string;
  dataset_revision?: string;
  sample_count?: number;
  fixed_sample_count?: number;
  generated_sample_count?: number;
  freeze_status?: "frozen" | "invalid";
  index_status?: "unselected" | "local_available" | "remote_recoverable" | "missing" | "incompatible";
  checked_sample_count?: number;
  checked_fixed_sample_count?: number;
  checked_generated_sample_count?: number;
  bound_index_version?: string;
  production_index_version?: string;
  compatibility?: "unselected" | "compatible" | "rebind_required";
  compatibility_message?: string;
}

interface IngestionHistory {
  items: RunState[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

interface EvaluationResult {
  summary: {
    status: string;
    status_reason?: string;
    key_metrics: Record<string, number | string | null>;
    steps: Array<{ name?: string; step?: string; status: string; error?: string; message?: string }>;
  };
  artifacts?: Record<string, string>;
}

interface EvaluationHistoryItem {
  run_id: string;
  status: string;
  status_reason?: string;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  stale?: boolean;
  ingestion_run_id?: string;
  index_version?: string;
  source_ids?: string[];
  source_names?: string[];
  source_label?: string;
  question_count?: number;
  dataset_identity?: Record<string, unknown>;
  config_identity?: string;
  strategy?: Record<string, unknown>;
  key_metrics: Record<string, number | string | null>;
}

interface EvaluationDiff {
  available: boolean;
  base: EvaluationHistoryItem;
  candidate: EvaluationHistoryItem;
  metric_deltas: Array<{ metric: string; base: number | string | null; candidate: number | string | null; delta: number | null }>;
  config_deltas: Array<{ field: string; base: unknown; candidate: unknown }>;
  sample_deltas: Array<{ sample_id: string; question: string; classification: string; metrics: Array<{ metric: string; base: number | string | null; candidate: number | string | null; delta: number | null }>; base_bad_case: boolean; candidate_bad_case: boolean }>;
  summary: Record<string, number>;
}

interface ParameterMeta {
  label: string;
  meaning: string;
  allowed?: [number, number];
  recommended?: [number, number];
  integer?: boolean;
  allow_null?: boolean;
}

interface RagEvalConfig {
  active_retrieval_profile: string;
  retrieval_profiles: Record<string, Record<string, unknown>>;
  retrieval_current: Record<string, unknown>;
  retrieval_profile_limits: Record<string, number | null>;
  retrieval_eval: Record<string, unknown>;
  ragas: {
    active_profile: string;
    available_profiles: string[];
    profiles: Record<string, Record<string, unknown>>;
    selected_metrics: string[];
    [key: string]: unknown;
  };
  parameter_meta: Record<string, ParameterMeta>;
}

interface StrategyProfile {
  profile_id: string;
  name: string;
  kind: "builtin" | "custom";
  editable: boolean;
  retrieval_profile: string;
  ragas_profile: string;
  retrieval: Record<string, unknown>;
  ragas: Record<string, unknown>;
  version?: number;
}

interface StrategyProfileCatalog {
  default_profile_id: string;
  published_profile_id: string;
  profiles: StrategyProfile[];
}

interface ProductionConfig {
  exists: boolean;
  retrieval_config?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  path?: string;
}

const sources = ref<SourceEntry[]>([]);
const selectedSourceIds = ref<string[]>([]);
const remoteAuthorizedSourceIds = ref<string[]>([]);
const ingestion = ref<RunState | null>(null);
const ingestionHistory = ref<RunState[]>([]);
const selectedIngestionRunId = ref("");
const evaluationRun = ref<RunState | null>(null);
const evaluationBatchRuns = ref<RunState[]>([]);
const tuningDatasetRun = ref<TuningDatasetRunState | null>(null);
const candidateRun = ref<RunState | null>(null);
const candidateDataset = ref<CandidateDataset | null>(null);
const candidateAudit = ref<CandidateAudit | null>(null);
const evaluationResult = ref<EvaluationResult | null>(null);
const events = ref<RunEvent[]>([]);
const goldDataset = ref<GoldDatasetStatus>({ exists: false });
const reviewerName = ref("");
const candidateDecisions = ref<Record<string, "approved" | "rejected" | "needs_revision">>({});
const candidateNotes = ref<Record<string, string>>({});
const candidateReviewIndex = ref(0);
const candidateReviewPhase = ref<CandidateReviewPhase>("intro");
const candidateReviewedIds = ref<Set<string>>(new Set());
const candidateLoading = ref(false);
const candidateDeleteLoading = ref(false);
const candidateFreezeLoading = ref(false);
const candidateGenerationConfigOpen = ref(false);
const candidateQuestionCount = ref(20);
const candidateMaxWorkers = ref(2);
const candidateMessage = ref("");
const candidateActionError = ref("");
const goldReplaceDialogOpen = ref(false);
const showGoldGovernance = ref(false);
const pageLimit = ref<"4" | "12" | "all" | "custom">("4");
const pageRanges = ref<Record<string, PageRangeDraft>>({});
const activeNav = ref<NavId>("workspace");
const evaluationSection = ref<EvaluationSection>("config");
const comparisonMode = ref<ComparisonMode>("time_trend");
const reportTab = ref<ReportTab>("pipeline");
const reportTabItems = [
  { id: "pipeline", label: "流程报告" },
  { id: "retrieval", label: "检索报告" },
  { id: "ragas", label: "Ragas 报告" },
];
const historyRange = ref("30d");
const historySource = ref("");
const comparisonGranularity = ref("day");
const diffBaseRunId = ref("");
const diffCandidateRunId = ref("");
const diffResult = ref<EvaluationDiff | null>(null);
const sampleFilter = ref("all");
const sampleSort = ref("default");
const historyRuns = ref<EvaluationHistoryItem[]>([]);
const historyLoading = ref(false);
const diffLoading = ref(false);
const comparisonError = ref("");
const reportRunId = ref("");
const reportMarkdown = ref("");
const reportLoading = ref(false);
const reportError = ref("");
const reportNotice = ref("");
const reportDeleteLoading = ref(false);
const configData = ref<RagEvalConfig | null>(null);
const strategyProfiles = ref<StrategyProfile[]>([]);
const strategyProfileId = ref("active_current");
const publishedProfileId = ref("active_current");
const profileNameDraft = ref("");
const strategyProfileStorageKey = "rag_eval_strategy_profile_id";
const productionConfig = ref<ProductionConfig | null>(null);
const retrievalProfile = ref("active_current");
const ragasProfile = ref("generic_pipeline");
const retrievalDraft = ref<Record<string, unknown>>({});
const ragasDraft = ref<Record<string, unknown>>({});
const executeRagas = ref(true);
const parallelEvaluationEnabled = ref(false);
const parallelProfileIds = ref<string[]>([]);
const configLoading = ref(false);
const configSaving = ref(false);
const configMessage = ref("");
const configError = ref("");
const releaseStatus = ref<ReleaseStatus | null>(null);
const releaseLoading = ref(false);
const releasePublishing = ref(false);
const releaseError = ref("");
const releaseNotice = ref("");
const releaseConfirmOpen = ref(false);
const releaseRollbackLoading = ref(false);
const sidebarCollapsed = ref(false);
const sourceLoading = ref(false);
const uploadInput = ref<HTMLInputElement | null>(null);
const uploadLoading = ref(false);
const sourceDeleteLoading = ref<string | null>(null);
const ingestionDeleteLoading = ref(false);
const ingestionLoading = ref(false);
const evaluationLoading = ref(false);
const tuningDatasetLoading = ref(false);
const tuningDatasetDeleteLoading = ref(false);
const tuningDatasetError = ref("");
const catalogError = ref("");
const sourceNotice = ref("");
const actionError = ref("");
const evaluationToast = ref("");
const evaluationAwaitingCompletion = ref<string | null>(null);
let eventSource: EventSource | null = null;
let pollTimer: number | null = null;
let evaluationToastTimer: number | null = null;
let batchPollTimer: number | null = null;

const retrievalFieldKeys = [
  "dense_fetch_k", "dense_mmr_k", "sparse_fetch_k", "final_top_k",
  "dense_score_threshold", "final_rerank_threshold", "mmr_lambda", "answer_max_contexts",
];
const answerCompressionOptions = ["none", "page_dedupe"];
const ragasFieldKeys = [
  "limit", "max_contexts", "max_context_chars", "max_response_chars",
  "ragas_timeout", "ragas_max_workers", "ragas_max_retries", "ragas_max_wait",
  "repeat_count", "low_score_threshold", "retrieval_recall_low_threshold", "retrieval_mrr_low_threshold",
];
const evaluationRagasKeys = [
  "limit", "selected_metrics", "include_reference_metrics", "max_contexts", "max_context_chars",
  "max_response_chars", "ragas_timeout", "ragas_max_workers", "ragas_max_retries", "ragas_max_wait",
  "answer_relevancy_strictness", "judge_profile", "repeat_count", "low_score_threshold",
  "retrieval_recall_low_threshold", "retrieval_mrr_low_threshold",
];
const metricOptions = ["faithfulness", "answer_relevancy", "context_utilization", "context_recall"];
const terminalStatuses: RunStatus[] = ["staged", "succeeded", "cancelled", "failed"];
const supportedUploadExtensions = [
  ".pdf", ".txt", ".md", ".markdown", ".csv", ".xlsx",
  ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff",
];
const supportedUploadLabel = supportedUploadExtensions.join(", ");
const selectedSources = computed(() => sources.value.filter((item) => selectedSourceIds.value.includes(item.source_id)));
const selectedRemoteSources = computed(() => selectedSources.value.filter((item) => remoteAuthorizedSourceIds.value.includes(item.source_id)));
const ingestionReady = computed(() => ingestion.value?.status === "staged" && Boolean(ingestion.value.index_version));
const busy = computed(() => uploadLoading.value || sourceDeleteLoading.value !== null || ingestionDeleteLoading.value || ingestionLoading.value || evaluationLoading.value);
const releasePublishable = computed(() => Boolean(releaseStatus.value?.publishable && releaseStatus.value?.state === "ready_to_publish"));
const evaluationMetrics = computed(() => Object.entries(evaluationResult.value?.summary.key_metrics || {}));
const displayEvents = computed(() => {
  const visible: RunEvent[] = [];
  const progressIndexes = new Map<string, number>();
  events.value.forEach((event) => {
    const progressMatch = event.type === "step_progress"
      ? String(event.message || "").match(/^(.+?):\s*\d+\/\d+/)
      : null;
    const progressKey = progressMatch?.[1]?.trim();
    if (progressKey) {
      const previousIndex = progressIndexes.get(progressKey);
      if (previousIndex !== undefined) {
        visible[previousIndex] = event;
        return;
      }
      progressIndexes.set(progressKey, visible.length);
    }
    visible.push(event);
  });
  return visible;
});
const evaluationProgressRows = computed(() => summarizeEvaluationProgress(events.value));
const evaluationCompletedStages = computed(() => evaluationProgressRows.value.filter((row) => row.status === "done").length);
const evaluationActive = computed(() => Boolean(evaluationRun.value && ["created", "queued", "running", "cancelling"].includes(evaluationRun.value.status)));
const tuningDatasetActive = computed(() => Boolean(tuningDatasetRun.value && ["created", "queued", "running", "cancelling"].includes(tuningDatasetRun.value.status)));
const parallelEvaluationActive = computed(() => evaluationBatchRuns.value.some((run) => !terminalStatuses.includes(run.status)));
const parallelCompletedCount = computed(() => evaluationBatchRuns.value.filter((run) => terminalStatuses.includes(run.status)).length);
const candidateActive = computed(() => Boolean(candidateRun.value && ["created", "queued", "running", "cancelling"].includes(candidateRun.value.status)));
const goldCandidateTarget = 48;
const currentCandidateSample = computed(() => candidateDataset.value?.samples[candidateReviewIndex.value] || null);
const candidateReviewCounts = computed(() => getReviewCounts(candidateDataset.value?.samples || [], candidateDecisions.value, candidateReviewedIds.value));
const candidateApprovalDelta = computed(() => Math.max(0, goldCandidateTarget - candidateReviewCounts.value.approved));
const candidateRequestedCount = computed(() => candidateQuestionCount.value);
const candidateUnitLimit = computed(() => Math.max(1, Number(ingestion.value?.unit_count || 1)));
const candidateBoundToSelectedIndex = computed(() => Boolean(
  candidateRun.value
  && ingestion.value
  && candidateRun.value.ingestion_run_id === ingestion.value.run_id
 && candidateRun.value.index_version === ingestion.value.index_version,
));
const candidateEvidenceCoverage = computed(() => {
  const total = candidateDataset.value?.samples.length || 0;
  const covered = Number(candidateAudit.value?.coverage?.samples_with_evidence || 0);
  return { total, covered: Math.min(Math.max(covered, 0), total), complete: total > 0 && covered >= total };
});
const generatedEvaluationDatasetReady = computed(() => Boolean(
  candidateDataset.value
  && candidateDataset.value.samples.length > 0
  && candidateBoundToSelectedIndex.value,
));
const evaluationDatasetReady = computed(() => generatedEvaluationDatasetReady.value);
const evaluationDatasetLabel = computed(() => generatedEvaluationDatasetReady.value
  ? `自动生成评测集 · ${candidateDataset.value?.samples.length || 0} 题`
  : "尚未生成评测集");
const candidateReviewSaved = computed(() => candidateRun.value?.review_status === "reviewed");
const candidateGateSummary = computed(() => [
  {
    key: "evidence",
    label: "证据蕴含",
    detail: candidateDataset.value ? `${candidateEvidenceCoverage.value.covered} / ${candidateEvidenceCoverage.value.total} 题有 Gold evidence` : "候选题生成后自动检查",
    state: candidateDataset.value ? (candidateEvidenceCoverage.value.complete ? "pass" : "review") : "pending",
  },
  {
    key: "retrieval",
    label: "检索合理性",
    detail: candidateBoundToSelectedIndex.value ? "证据定位已绑定当前索引，待发布前验证" : "需绑定当前索引并复审",
    state: candidateBoundToSelectedIndex.value && candidateReviewSaved.value ? "review" : "pending",
  },
  {
    key: "distribution",
    label: "分布平衡",
    detail: candidateDataset.value ? "模态、来源与难度分布待自动校验" : "候选题生成后自动检查",
    state: "pending",
  },
]);
const canBindProductionBaseline = computed(() => Boolean(
  goldDataset.value.exists
  && goldDataset.value.bound_index_version
  && goldDataset.value.bound_index_version === goldDataset.value.production_index_version,
));
const ragasInProgress = computed(() => evaluationActive.value && (
  String(evaluationRun.value?.current_stage || "").toLowerCase().includes("ragas")
  || events.value.some((event) => /ragas/i.test(String(event.message || "")))
));
const evaluationWaitingMessage = computed(() => ragasInProgress.value
  ? "当前正在调用 Ragas judge 大模型，请耐心等待；样本越多，评测耗时越长。"
  : "当前流程包含回答生成或模型评审，请耐心等待；页面会持续更新执行进度。"
);
const historyMetricKeys = computed(() => Array.from(new Set(historyRuns.value.flatMap((run) => Object.keys(run.key_metrics || {})))).slice(0, 6));
const sourceFilterOptions = computed(() => Array.from(new Set(historyRuns.value.flatMap((run) => run.source_names || []))).sort());
const selectedReportRun = computed(() => historyRuns.value.find((run) => run.run_id === reportRunId.value) || null);
const reportDeleteCanProceed = computed(() => {
  const run = selectedReportRun.value;
  if (!run) return false;
  return !["created", "queued", "running", "cancelling"].includes(run.status) || run.stale === true;
});
const currentReportRunLabel = computed(() => selectedReportRun.value ? historyLabel(selectedReportRun.value) : reportRunId.value || "未选择评测运行");
const trendRows = computed(() => {
  const completed = historyRuns.value.filter((run) => run.status === "pass" || run.status === "succeeded");
  if (comparisonGranularity.value === "run") return completed.map((run) => ({
    label: formatBeijingDateTime(run.finished_at || run.created_at) || run.run_id,
    count: 1,
    metrics: run.key_metrics,
  }));
  const groups = new Map<string, EvaluationHistoryItem[]>();
  completed.forEach((run) => {
    const day = formatBeijingDate(run.finished_at || run.created_at) || "unknown";
    groups.set(day, [...(groups.get(day) || []), run]);
  });
  return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b)).map(([label, runs]) => {
    const metrics: Record<string, number | null> = {};
    historyMetricKeys.value.forEach((key) => {
      const values = runs.map((run) => run.key_metrics?.[key]).filter((value): value is number => typeof value === "number" && Number.isFinite(value));
      metrics[key] = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    });
    return { label, count: runs.length, metrics };
  });
});
const customPageTotal = computed(() => selectedSources.value.reduce((total, source) => {
  const range = pageRanges.value[source.source_id];
  const start = Number(range?.start);
  const end = Number(range?.end);
  return total + (Number.isInteger(start) && Number.isInteger(end) && end >= start ? end - start + 1 : 0);
}, 0));

function formatBytes(value = 0): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function parseTimestamp(value?: string): Date | null {
  if (!value) return null;
  const hasTimezone = value.endsWith("Z") || value.length > 10 && /[+-][0-9]{2}:?[0-9]{2}$/.test(value);
  const timestamp = new Date(hasTimezone ? value : value + "Z");
  return Number.isNaN(timestamp.getTime()) ? null : timestamp;
}

function formatBeijingParts(value?: string): Record<string, string> | null {
  const timestamp = parseTimestamp(value);
  if (!timestamp) return null;
  return Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(timestamp).map(({ type, value: part }) => [type, part]));
}

function formatBeijingTime(value?: string): string {
  const parts = formatBeijingParts(value);
  return parts ? `${parts.hour}:${parts.minute}:${parts.second}` : "--:--:--";
}

function formatBeijingDate(value?: string): string {
  const parts = formatBeijingParts(value);
  return parts ? `${parts.year}-${parts.month}-${parts.day}` : "";
}

function formatBeijingDateTime(value?: string): string {
  const date = formatBeijingDate(value);
  return date ? `${date} ${formatBeijingTime(value)}` : "";
}

function ingestionSourceLabel(run?: Pick<RunState, "source_label" | "source_names"> | null): string {
  const explicit = String(run?.source_label || "").trim();
  if (explicit) return explicit;
  const names = (run?.source_names || []).map((name) => String(name).trim()).filter(Boolean);
  return names.length ? names.join("、") : "知识源未记录";
}

function ingestionDisplayName(run?: RunState | null): string {
  if (!run) return "未选择索引";
  const date = formatBeijingDate(run.created_at) || "日期未知";
  return `${date} · ${ingestionSourceLabel(run)} · ${statusLabel(run.status)}`;
}

function indexReferenceLabel(indexVersion?: string): string {
  if (!indexVersion) return "--";
  const matchingRun = ingestionHistory.value.find((run) => run.index_version === indexVersion);
  return matchingRun ? ingestionDisplayName(matchingRun) : `历史索引 · ${indexVersion}`;
}

function statusLabel(status?: string): string {
  return ({ created: "已创建", queued: "排队中", running: "运行中", cancelling: "取消中", staged: "已就绪", succeeded: "已完成", cancelled: "已取消", failed: "失败", pass: "通过", needs_review: "待复核" } as Record<string, string>)[status || ""] || "未开始";
}

function evaluationStageLabel(step: string): string {
  return ({
    validate_datasets: "数据校验",
    retrieval_eval: "检索评测",
    ragas_eval: "Ragas 评测",
    trace_export: "Trace 导出",
    summary: "汇总报告",
  } as Record<string, string>)[step] || step;
}

function evaluationPhaseLabel(phase?: string): string {
  return ({ prepare: "准备题集", judge: "Judge 评分", retrieval: "逐题检索", answer: "生成回答" } as Record<string, string>)[phase || ""] || phase || "阶段处理中";
}

function evaluationProgressPercent(current?: number, total?: number, status?: string): number {
  if (status === "done") return 100;
  if (typeof current !== "number" || typeof total !== "number" || total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((current / total) * 100)));
}

function evaluationProgressStatusLabel(status: string): string {
  return ({ pending: "待开始", running: "进行中", done: "已完成", error: "失败" } as Record<string, string>)[status] || status;
}

function statusTone(status?: string): string {
  if (status === "failed" || status === "fail") return "danger";
  if (status === "staged" || status === "succeeded" || status === "pass") return "success";
  if (status === "cancelled") return "muted";
  return "active";
}

let csrfToken = "";

/* 写请求需要 Session 绑定的 CSRF 令牌，令牌从同源认证接口获取。 */
async function loadCsrfToken() {
  try {
    const response = await fetch("/api/check_auth");
    const payload = await response.json() as { csrf_token?: string };
    csrfToken = payload.csrf_token || "";
  } catch {
    csrfToken = "";
  }
}

function writeHeaders(): Record<string, string> {
  return csrfToken ? { "X-CSRF-Token": csrfToken } : {};
}

async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
      ...(method === "GET" || method === "HEAD" ? {} : writeHeaders()),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.success === false) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload.data as T;
}

function openUploadDialog() { uploadInput.value?.click(); }

async function uploadSource(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  actionError.value = "";
  sourceNotice.value = "";
  const extension = file.name.toLowerCase().slice(file.name.lastIndexOf("."));
  if (!supportedUploadExtensions.includes(extension)) {
    actionError.value = `不支持该文件格式，可上传：${supportedUploadLabel}`;
    return;
  }
  uploadLoading.value = true;
  try {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch("/api/rag_eval/isolated/sources", { method: "POST", body: formData, headers: writeHeaders() });
    const payload = await response.json().catch(() => ({})) as { success?: boolean; error?: string; data?: { source?: SourceEntry } };
    if (!response.ok || payload.success === false) throw new Error(payload.error || `上传失败 (${response.status})`);
    await loadCatalog();
    sourceNotice.value = `已登记 ${payload.data?.source?.name || file.name}，请选择来源后启动摄取`;
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "知识源上传失败";
  } finally { uploadLoading.value = false; }
}

async function deleteSource(source: SourceEntry) {
  if (source.source_kind !== "uploaded") return;
  if (!window.confirm(`确认删除“${source.name}”？这只会删除上传文件，不会删除已生成的隔离索引或评测报告。`)) return;
  actionError.value = "";
  sourceNotice.value = "";
  sourceDeleteLoading.value = source.source_id;
  try {
    await api(`/api/rag_eval/isolated/sources/${encodeURIComponent(source.source_id)}`, { method: "DELETE" });
    selectedSourceIds.value = selectedSourceIds.value.filter((value) => value !== source.source_id);
    const nextRanges = { ...pageRanges.value };
    delete nextRanges[source.source_id];
    pageRanges.value = nextRanges;
    await loadCatalog();
    sourceNotice.value = `已删除 ${source.name}`;
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "知识源删除失败";
  } finally { sourceDeleteLoading.value = null; }
}

async function renameSource(source: SourceEntry) {
  const currentName = source.display_name || source.name;
  const displayName = window.prompt(`修改“${currentName}”的显示名称`, currentName)?.trim();
  if (!displayName || displayName === currentName) return;
  actionError.value = "";
  sourceNotice.value = "";
  try {
    await api(`/api/rag_eval/isolated/sources/${encodeURIComponent(source.source_id)}`, {
      method: "PATCH",
      body: JSON.stringify({ display_name: displayName }),
    });
    await loadCatalog();
    sourceNotice.value = `已将来源改名为 ${displayName}`;
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "来源改名失败";
  }
}

async function deleteSelectedIngestion() {
  const run = ingestion.value;
  if (!run || !terminalStatuses.includes(run.status)) return;
  if (!window.confirm(`确认删除工作索引“${ingestionDisplayName(run)}”？\n\n这会同时删除该摄取运行及其终态候选、评测和治理报告；如果 active pointer、Gold 或仍在运行的任务引用它，服务端会拒绝。`)) return;
  ingestionDeleteLoading.value = true;
  actionError.value = "";
  sourceNotice.value = "";
  try {
    await api(`/api/rag_eval/isolated/ingestion-runs/${encodeURIComponent(run.run_id)}`, {
      method: "DELETE",
      body: JSON.stringify({ cascade: true }),
    });
    stopWatching();
    ingestion.value = null;
    selectedIngestionRunId.value = "";
    candidateRun.value = null;
    candidateDataset.value = null;
    candidateAudit.value = null;
    tuningDatasetRun.value = null;
    evaluationRun.value = null;
    evaluationResult.value = null;
    localStorage.removeItem("ingestion_run_id");
    localStorage.removeItem("rag_eval_ingestion_run_id");
    localStorage.removeItem("candidate_run_id");
    localStorage.removeItem("evaluation_run_id");
    localStorage.removeItem("tuning_dataset_run_id");
    await loadIngestionHistory();
    await loadGoldDatasetStatus();
    sourceNotice.value = `已删除工作索引 ${run.run_id}`;
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "工作索引删除失败";
  } finally { ingestionDeleteLoading.value = false; }
}

async function deleteCandidateRun() {
  const run = candidateRun.value;
  if (!run || !terminalStatuses.includes(run.status)) return;
  if (!window.confirm(`确认删除候选题运行“${run.run_id}”？已被评测或 Gold 使用时服务端会拒绝。`)) return;
  candidateDeleteLoading.value = true;
  candidateActionError.value = "";
  try {
    await api(`/api/rag_eval/isolated/candidate-runs/${encodeURIComponent(run.run_id)}`, { method: "DELETE", body: "{}" });
    candidateRun.value = null;
    candidateDataset.value = null;
    candidateAudit.value = null;
    candidateReviewedIds.value = new Set();
    localStorage.removeItem("candidate_run_id");
    candidateMessage.value = "候选题运行已删除";
  } catch (error) {
    candidateActionError.value = error instanceof Error ? error.message : "候选题运行删除失败";
  } finally { candidateDeleteLoading.value = false; }
}

async function deleteTuningDatasetRun() {
  const run = tuningDatasetRun.value;
  if (!run || !terminalStatuses.includes(run.status)) return;
  if (!window.confirm(`确认删除调参集治理运行“${run.run_id}”？`)) return;
  tuningDatasetDeleteLoading.value = true;
  tuningDatasetError.value = "";
  try {
    await api(`/api/rag_eval/isolated/tuning-dataset-runs/${encodeURIComponent(run.run_id)}`, { method: "DELETE", body: "{}" });
    tuningDatasetRun.value = null;
    localStorage.removeItem("tuning_dataset_run_id");
  } catch (error) {
    tuningDatasetError.value = error instanceof Error ? error.message : "调参集治理运行删除失败";
  } finally { tuningDatasetDeleteLoading.value = false; }
}

async function loadCatalog() {
  sourceLoading.value = true;
  catalogError.value = "";
  try {
    const data = await api<{ sources: SourceEntry[] }>("/api/rag_eval/isolated/source-catalog");
    sources.value = data.sources;
    const nextRanges = { ...pageRanges.value };
    data.sources.forEach((source) => { nextRanges[source.source_id] ||= { start: "1", end: String(Math.min(source.page_count || 12, 12)) }; });
    pageRanges.value = nextRanges;
    const availableIds = new Set(data.sources.map((item) => item.source_id));
    selectedSourceIds.value = selectedSourceIds.value.filter((value) => availableIds.has(value));
    remoteAuthorizedSourceIds.value = remoteAuthorizedSourceIds.value.filter((value) => availableIds.has(value));
    if (!selectedSourceIds.value.length) selectedSourceIds.value = data.sources.map((item) => item.source_id);
  } catch (error) {
    catalogError.value = error instanceof Error ? error.message : "来源目录加载失败";
  } finally { sourceLoading.value = false; }
}

function appendEvent(event: RunEvent) {
  if (event.type === "heartbeat" || event.type === "connected") return;
  events.value = [...events.value, event].slice(-100);
}

function notifyEvaluationFinished(state: RunState) {
  if (!evaluationAwaitingCompletion.value || evaluationAwaitingCompletion.value !== state.run_id) return;
  if (!(["succeeded", "failed", "cancelled"] as RunStatus[]).includes(state.status)) return;
  evaluationAwaitingCompletion.value = null;
  evaluationToast.value = state.status === "succeeded"
    ? "Ragas 评测已完成，可以查看本次报告。"
    : state.status === "cancelled"
      ? "评测已取消。"
      : "评测失败，请查看流程事件和后台日志。";
  if (evaluationToastTimer !== null) window.clearTimeout(evaluationToastTimer);
  evaluationToastTimer = window.setTimeout(() => {
    evaluationToast.value = "";
    evaluationToastTimer = null;
  }, 7000);
}

function dismissEvaluationToast() {
  evaluationToast.value = "";
  if (evaluationToastTimer !== null) window.clearTimeout(evaluationToastTimer);
  evaluationToastTimer = null;
}

function stopWatching() {
  eventSource?.close();
  eventSource = null;
  if (pollTimer !== null) window.clearInterval(pollTimer);
  pollTimer = null;
}

function stopBatchPolling() {
  if (batchPollTimer !== null) window.clearInterval(batchPollTimer);
  batchPollTimer = null;
}

async function refreshEvaluationBatch() {
  if (!evaluationBatchRuns.value.length) return;
  const states = await Promise.all(evaluationBatchRuns.value.map((run) =>
    api<RunState>(`/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(run.run_id)}`),
  ));
  evaluationBatchRuns.value = states;
  const selected = states.find((run) => run.run_id === evaluationRun.value?.run_id);
  if (selected) {
    evaluationRun.value = selected;
    events.value = selected.events || [];
  }
  if (states.every((run) => terminalStatuses.includes(run.status))) {
    stopBatchPolling();
    evaluationLoading.value = false;
    localStorage.setItem("evaluation_batch_run_ids", JSON.stringify(states.map((run) => run.run_id)));
    await loadEvaluationHistory();
    const succeeded = states.filter((run) => run.status === "succeeded").length;
    evaluationToast.value = `并行实验已结束：${succeeded}/${states.length} 个成功，可进入对比分析。`;
  }
}

function startBatchPolling() {
  stopBatchPolling();
  void refreshEvaluationBatch();
  batchPollTimer = window.setInterval(() => refreshEvaluationBatch().catch(() => undefined), 3000);
}

async function focusBatchRun(run: RunState) {
  evaluationRun.value = run;
  events.value = run.events || [];
  if (terminalStatuses.includes(run.status)) {
    stopWatching();
    if (["succeeded", "failed"].includes(run.status) && run.result_available) await loadEvaluationResult(run.run_id);
  } else {
    watchRun("evaluation", run.run_id);
  }
}

type RunKind = "ingestion" | "candidate" | "evaluation" | "tuning_dataset";

async function refreshRun(kind: RunKind, runId: string) {
  const url = kind === "ingestion"
    ? `/api/rag_eval/isolated/ingestion-runs/${encodeURIComponent(runId)}`
    : kind === "candidate"
      ? `/api/rag_eval/isolated/candidate-runs/${encodeURIComponent(runId)}`
      : kind === "evaluation"
        ? `/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(runId)}`
        : `/api/rag_eval/isolated/tuning-dataset-runs/${encodeURIComponent(runId)}`;
  const state = await api<RunState>(url);
  if (kind === "ingestion") ingestion.value = state;
  else if (kind === "candidate") candidateRun.value = state;
  else if (kind === "evaluation") {
    evaluationRun.value = state;
    events.value = state.events || [];
  } else if (kind === "tuning_dataset") {
    tuningDatasetRun.value = state as TuningDatasetRunState;
    events.value = state.events || [];
  }
  if (kind === "evaluation") notifyEvaluationFinished(state);
  if (terminalStatuses.includes(state.status)) {
    if (kind === "ingestion") ingestionLoading.value = false;
    else if (kind === "candidate") {
      candidateLoading.value = false;
      if (state.status === "succeeded") await loadCandidateDataset(runId);
    } else if (kind === "evaluation") evaluationLoading.value = false;
    else if (kind === "tuning_dataset") tuningDatasetLoading.value = false;
    if (kind === "evaluation" && ["succeeded", "failed"].includes(state.status) && state.result_available) await loadEvaluationResult(runId);
    stopWatching();
  }
  return state;
}

async function loadEvaluationResult(runId: string) {
  try {
    evaluationResult.value = await api<EvaluationResult>(`/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(runId)}/result`);
    await loadEvaluationHistory();
  } catch (error) { actionError.value = error instanceof Error ? error.message : "评测结果加载失败"; }
}

function watchRun(kind: RunKind, runId: string) {
  stopWatching();
  const stream = kind === "ingestion"
    ? `/api/rag_eval/isolated/ingestion-runs/${encodeURIComponent(runId)}/stream`
    : kind === "candidate"
      ? `/api/rag_eval/isolated/candidate-runs/${encodeURIComponent(runId)}/stream`
      : kind === "evaluation"
        ? `/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(runId)}/stream`
        : `/api/rag_eval/isolated/tuning-dataset-runs/${encodeURIComponent(runId)}/stream`;
  eventSource = new EventSource(stream);
  pollTimer = window.setInterval(() => {
    void refreshRun(kind, runId).catch(() => undefined);
  }, 5000);
  eventSource.onmessage = async (message) => {
    const event = JSON.parse(message.data) as RunEvent;
    if (kind === "evaluation" || kind === "tuning_dataset") appendEvent(event);
    if (["stage_start", "candidate_progress", "ingestion_progress", "governance_progress", "question_start", "step_start", "step_done", "step_error"].includes(event.type)) await refreshRun(kind, runId);
    if (["run_done", "run_error", "run_cancelled"].includes(event.type)) await refreshRun(kind, runId);
  };
  eventSource.onerror = async () => {
    eventSource?.close(); eventSource = null;
    if (pollTimer !== null) window.clearInterval(pollTimer);
    pollTimer = null;
    try {
      await refreshRun(kind, runId);
      const current = kind === "ingestion" ? ingestion.value : kind === "candidate" ? candidateRun.value : kind === "evaluation" ? evaluationRun.value : tuningDatasetRun.value;
      if (current && !terminalStatuses.includes(current.status)) pollTimer = window.setInterval(() => refreshRun(kind, runId).catch(() => undefined), 5000);
    } catch (error) { actionError.value = error instanceof Error ? error.message : "运行状态读取失败"; }
  };
}

async function startIngestion() {
  actionError.value = "";
  if (!selectedSourceIds.value.length) { actionError.value = "请选择至少一个知识源"; return; }
  ingestionLoading.value = true;
  events.value = [];
  try {
    let customRanges: Array<{ source_id: string; start_page: number; end_page: number }> | null = null;
    if (pageLimit.value === "custom") {
      customRanges = selectedSources.value.map((source) => {
        const draft = pageRanges.value[source.source_id];
        const start = Number(draft?.start); const end = Number(draft?.end);
        if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) throw new Error(`请填写 ${source.name} 的有效页码范围`);
        return { source_id: source.source_id, start_page: start, end_page: end };
      });
    }
    const state = await api<RunState>("/api/rag_eval/isolated/ingestion-runs", {
      method: "POST",
      body: JSON.stringify({
        source_ids: selectedSourceIds.value,
        max_pages: pageLimit.value === "4" || pageLimit.value === "12" ? Number(pageLimit.value) : null,
        page_ranges: customRanges,
        allow_remote_data: selectedRemoteSources.value.length > 0,
        authorized_source_ids: selectedRemoteSources.value.map((source) => source.source_id),
      }),
    });
    ingestion.value = state;
    localStorage.setItem("ingestion_run_id", state.run_id);
    watchRun("ingestion", state.run_id);
  } catch (error) {
    ingestionLoading.value = false;
    actionError.value = error instanceof Error ? error.message : "摄取任务启动失败";
  }
}

async function cancelIngestion() {
  if (!ingestion.value) return;
  try { ingestion.value = await api<RunState>(`/api/rag_eval/isolated/ingestion-runs/${encodeURIComponent(ingestion.value.run_id)}/cancel`, { method: "POST", body: "{}" }); }
  catch (error) { actionError.value = error instanceof Error ? error.message : "取消失败"; }
}

function comparisonSince(): string | undefined {
  if (historyRange.value === "all") return undefined;
  const days = historyRange.value === "90d" ? 90 : historyRange.value === "30d" ? 30 : 7;
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

async function loadEvaluationHistory() {
  historyLoading.value = true;
  comparisonError.value = "";
  try {
    const query: Record<string, string> = { page: "1", page_size: "100" };
    const since = comparisonSince();
    if (since) query.since = since;
    if (historySource.value) query.source_name = historySource.value;
    const params = new URLSearchParams(query);
    const data = await api<{ items: EvaluationHistoryItem[] }>(`/api/rag_eval/isolated/evaluation-history?${params.toString()}`);
    historyRuns.value = data.items || [];
    const ids = historyRuns.value.map((run) => run.run_id);
    if (!ids.includes(reportRunId.value)) reportRunId.value = evaluationRun.value?.run_id && ids.includes(evaluationRun.value.run_id) ? evaluationRun.value.run_id : ids[0] || "";
    if (!ids.includes(diffCandidateRunId.value)) diffCandidateRunId.value = ids[0] || "";
    if (!ids.includes(diffBaseRunId.value) || diffBaseRunId.value === diffCandidateRunId.value) diffBaseRunId.value = ids.find((id) => id !== diffCandidateRunId.value) || "";
    if (activeNav.value === "reports" && reportRunId.value) await loadReportArtifact();
    if (!reportRunId.value) reportMarkdown.value = "";
  } catch (error) {
    comparisonError.value = error instanceof Error ? error.message : "评测历史加载失败";
  } finally { historyLoading.value = false; }
}

async function loadEvaluationDiff() {
  if (!diffBaseRunId.value || !diffCandidateRunId.value || diffBaseRunId.value === diffCandidateRunId.value) { diffResult.value = null; return; }
  diffLoading.value = true;
  comparisonError.value = "";
  try {
    const params = new URLSearchParams({ base_run_id: diffBaseRunId.value, candidate_run_id: diffCandidateRunId.value });
    diffResult.value = await api<EvaluationDiff>(`/api/rag_eval/isolated/evaluation-diff?${params.toString()}`);
  } catch (error) {
    diffResult.value = null;
    const message = error instanceof Error ? error.message : "评测 diff 加载失败";
    comparisonError.value = message.includes("different dataset identities")
      ? incompatibleDatasetComparisonMessage()
      : message;
  } finally { diffLoading.value = false; }
}

async function refreshComparison() { await loadEvaluationHistory(); if (comparisonMode.value !== "time_trend") await loadEvaluationDiff(); }

function reportArtifactPath(): string {
  return reportTab.value === "pipeline" ? "summary.md" : reportTab.value === "retrieval" ? "reports/rag_eval_report.md" : "reports/ragas_eval_report.md";
}

async function loadReportArtifact() {
  if (!reportRunId.value) { reportMarkdown.value = ""; return; }
  reportLoading.value = true;
  reportError.value = "";
  const url = `/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(reportRunId.value)}/artifacts/${reportArtifactPath().split("/").map(encodeURIComponent).join("/")}`;
  try {
    const response = await fetch(url);
    const text = await response.text();
    if (!response.ok) {
      let message = text;
      try { message = JSON.parse(text).error || message; } catch { /* response may be plain text */ }
      throw new Error(message || `报告读取失败 (${response.status})`);
    }
    reportMarkdown.value = text;
  } catch (error) {
    reportMarkdown.value = "";
    reportError.value = error instanceof Error ? error.message : "报告读取失败";
  } finally { reportLoading.value = false; }
}

async function deleteReport() {
  const runId = reportRunId.value;
  const run = selectedReportRun.value;
  if (!runId || !run) return;
  const warning = run.stale
    ? "该任务长时间没有新的执行事件，将强制移除其运行目录。"
    : "删除后历史记录、报告和对比数据都不可见。";
  if (!reportDeleteCanProceed.value || !window.confirm(`确定删除这次评测报告吗？\n\n${historyLabel(run)}\n\n${warning}`)) return;
  reportDeleteLoading.value = true;
  reportError.value = "";
  reportNotice.value = "";
  try {
    await api(`/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(runId)}`, {
      method: "DELETE",
      body: JSON.stringify({ force: run.stale === true }),
    });
    if (evaluationRun.value?.run_id === runId) {
      stopWatching();
      evaluationRun.value = null;
      evaluationResult.value = null;
      evaluationAwaitingCompletion.value = null;
      localStorage.removeItem("evaluation_run_id");
    }
    reportRunId.value = "";
    reportMarkdown.value = "";
    diffResult.value = null;
    diffBaseRunId.value = "";
    diffCandidateRunId.value = "";
    await loadEvaluationHistory();
    reportNotice.value = "报告已删除，历史记录和对比数据已同步移除。";
  } catch (error) {
    reportError.value = error instanceof Error ? error.message : "报告删除失败";
  } finally { reportDeleteLoading.value = false; }
}

function openReport(runId: string, tab: ReportTab = "pipeline") {
  activeNav.value = "reports";
  reportRunId.value = runId;
  reportTab.value = tab;
  void loadEvaluationHistory().then(() => loadReportArtifact());
}

function selectReportTab(tab: ReportTab) { reportTab.value = tab; void loadReportArtifact(); }

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function renderInlineMarkdown(value: string): string {
  let rendered = escapeHtml(value);
  rendered = rendered.replace(/`([^`]+)`/g, "<code>$1</code>");
  rendered = rendered.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  rendered = rendered.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  rendered = rendered.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  rendered = rendered.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return rendered;
}

function splitMarkdownTableRow(line: string): string[] { return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim()); }

function renderMarkdown(markdown: string): string {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const html: string[] = [];
  let paragraph: string[] = [];
  let listType = "";
  let inCode = false;
  let codeLines: string[] = [];
  const flushParagraph = () => { if (paragraph.length) { html.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`); paragraph = []; } };
  const closeList = () => { if (listType) { html.push(`</${listType}>`); listType = ""; } };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim().startsWith("```")) {
      flushParagraph(); closeList();
      if (inCode) { html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`); codeLines = []; }
      inCode = !inCode; continue;
    }
    if (inCode) { codeLines.push(line); continue; }
    if (/^\|.*\|$/.test(line) && /^\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
      flushParagraph(); closeList();
      const headers = splitMarkdownTableRow(line); index += 2;
      const rows: string[][] = [];
      while (index < lines.length && /^\|.*\|$/.test(lines[index])) { rows.push(splitMarkdownTableRow(lines[index])); index += 1; }
      index -= 1;
      html.push(`<table><thead><tr>${headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${headers.map((_, i) => `<td>${renderInlineMarkdown(row[i] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) { flushParagraph(); closeList(); html.push(`<h${heading[1].length}>${renderInlineMarkdown(heading[2])}</h${heading[1].length}>`); continue; }
    if (/^\s*([-*_])\s*\1\s*\1/.test(line)) { flushParagraph(); closeList(); html.push("<hr>"); continue; }
    const bullet = /^\s*[-*+]\s+(.+)$/.exec(line);
    const ordered = /^\s*\d+[.)]\s+(.+)$/.exec(line);
    if (bullet || ordered) {
      flushParagraph(); const nextList = bullet ? "ul" : "ol";
      if (listType !== nextList) { closeList(); html.push(`<${nextList}>`); listType = nextList; }
      html.push(`<li>${renderInlineMarkdown((bullet || ordered)![1])}</li>`); continue;
    }
    if (/^>\s?/.test(line)) { flushParagraph(); closeList(); html.push(`<blockquote>${renderInlineMarkdown(line.replace(/^>\s?/, ""))}</blockquote>`); continue; }
    if (!line.trim()) { flushParagraph(); closeList(); continue; }
    paragraph.push(line.trim());
  }
  if (inCode) html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  flushParagraph(); closeList();
  return html.join("");
}

function runProfileName(run: EvaluationHistoryItem): string {
  return String(run.strategy?.profile_name || run.strategy?.profile_id || "未记录 profile");
}
function runDatasetRevision(run: EvaluationHistoryItem): string {
  const revision = String(run.dataset_identity?.dataset_revision || "").trim();
  return revision ? `rev ${revision.slice(0, 16)}` : "未记录 revision";
}
function runDatasetSubtitle(run: EvaluationHistoryItem): string {
  return `${String(run.dataset_identity?.dataset_id || "dataset")} · ${runDatasetRevision(run)} · ${formatBeijingDateTime(run.created_at) || run.run_id}`;
}
function historyLabel(run: EvaluationHistoryItem): string { return `${runProfileName(run)} · ${runDatasetSubtitle(run)}`; }
function incompatibleDatasetComparisonMessage(): string {
  const base = historyRuns.value.find((run) => run.run_id === diffBaseRunId.value);
  const candidate = historyRuns.value.find((run) => run.run_id === diffCandidateRunId.value);
  if (!base || !candidate) return "无法进行严格 A/B：两次运行使用了不同版本的题集。请选择相同 Gold revision 的运行。";
  const baseDataset = String(base.dataset_identity?.dataset_id || "题集");
  const candidateDataset = String(candidate.dataset_identity?.dataset_id || "题集");
  return `无法进行严格 A/B：基线使用 ${baseDataset}（${runDatasetRevision(base)}），候选使用 ${candidateDataset}（${runDatasetRevision(candidate)}）。同名题集不代表题目内容相同；逐题指标只可比较完全相同的 Gold revision。请选择相同 revision 的运行，或仅将两次结果作非严格的趋势参考。`;
}
function formatMetric(value: unknown): string { return typeof value === "number" ? value.toFixed(4) : value === null || value === undefined || value === "" ? "未评分" : String(value); }
function formatConfigValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未设置";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function configFieldLabel(field: string): string {
  const labels: Record<string, string> = {
    strategy_profile: "策略配置",
    retrieval: "检索",
    ragas: "Ragas",
    profile: "配置档案",
    overrides: "覆盖参数",
    selected_metrics: "评测指标",
    judge_profile: "评审档案",
    steps: "执行步骤",
  };
  return field.split(".").map((part) => labels[part] || part.replaceAll("_", " ")).join(" / ");
}
function metricLabel(value: string): string { return ({ retrieval_recall_at_k: "检索 Recall@K", retrieval_mrr: "检索 MRR", retrieval_hit_rate: "检索命中率", ragas_faithfulness: "Ragas 忠实性", ragas_answer_relevancy: "回答相关性", ragas_context_utilization: "上下文利用率", ragas_context_recall: "上下文召回率", faithfulness: "忠实度", answer_relevancy: "回答相关性", context_utilization: "上下文利用率", context_recall: "上下文召回率", recall: "检索召回率", reciprocal_rank: "检索 MRR" } as Record<string, string>)[value] || value.replaceAll("_", " "); }
function metricDeltaTone(delta: number | null): string { return delta === null ? "unscored" : delta > 0 ? "positive" : delta < 0 ? "negative" : "flat"; }
function metricDeltaLabel(delta: number | null): string { return delta === null ? "无可比数据" : delta > 0 ? "候选升高" : delta < 0 ? "候选降低" : "保持不变"; }
function sampleClassificationLabel(value: string): string { return ({ improved: "整体改善", regressed: "整体退化", mixed: "指标分歧", unchanged: "基本持平", unscored: "未评分" } as Record<string, string>)[value] || value; }
function sampleClassificationTone(value: string): string { return ({ improved: "positive", regressed: "negative", mixed: "mixed", unchanged: "flat", unscored: "unscored" } as Record<string, string>)[value] || "unscored"; }
function sampleMetric(sample: EvaluationDiff["sample_deltas"][number], metric: string) { return sample.metrics.find((item) => item.metric === metric); }

const sampleMetricOrder = ["answer_relevancy", "context_recall", "context_utilization", "faithfulness", "recall", "reciprocal_rank"];
const sampleMetricKeys = computed(() => {
  const present = new Set((diffResult.value?.sample_deltas || []).flatMap((sample) => sample.metrics.filter((metric) => typeof metric.base === "number" || typeof metric.candidate === "number").map((metric) => metric.metric)));
  return [...sampleMetricOrder.filter((metric) => present.has(metric)), ...Array.from(present).filter((metric) => !sampleMetricOrder.includes(metric)).sort()];
});
const visibleSampleDeltas = computed(() => {
  const rows = (diffResult.value?.sample_deltas || []).filter((sample) => sampleFilter.value === "all" || sample.classification === sampleFilter.value);
  const sorted = [...rows];
  if (sampleSort.value === "classification") {
    const order: Record<string, number> = { regressed: 0, mixed: 1, unchanged: 2, improved: 3, unscored: 4 };
    sorted.sort((left, right) => (order[left.classification] ?? 9) - (order[right.classification] ?? 9) || left.sample_id.localeCompare(right.sample_id));
  } else if (sampleSort.value === "largest_drop") {
    const worstDelta = (sample: EvaluationDiff["sample_deltas"][number]) => Math.min(...sample.metrics.map((metric) => metric.delta ?? 0));
    sorted.sort((left, right) => worstDelta(left) - worstDelta(right) || left.sample_id.localeCompare(right.sample_id));
  }
  return sorted;
});
function toggleSource(sourceId: string) {
  selectedSourceIds.value = selectedSourceIds.value.includes(sourceId)
    ? selectedSourceIds.value.filter((value) => value !== sourceId)
    : [...selectedSourceIds.value, sourceId];
  if (!selectedSourceIds.value.includes(sourceId)) {
    remoteAuthorizedSourceIds.value = remoteAuthorizedSourceIds.value.filter((value) => value !== sourceId);
  }
}

function toggleRemoteAuthorization(sourceId: string) {
  if (!selectedSourceIds.value.includes(sourceId)) return;
  remoteAuthorizedSourceIds.value = remoteAuthorizedSourceIds.value.includes(sourceId)
    ? remoteAuthorizedSourceIds.value.filter((value) => value !== sourceId)
    : [...remoteAuthorizedSourceIds.value, sourceId];
}

function selectNav(nav: NavId) {
  activeNav.value = nav;
  if (nav === "candidates") { void loadGoldDatasetStatus(); }
  if (nav === "evaluation") { evaluationSection.value = "config"; void loadConfig(); }
  if (nav === "release") { void loadReleaseStatus(); }
  if (nav === "reports") { void loadEvaluationHistory(); }
}

function selectEvaluationSection(section: EvaluationSection) {
  activeNav.value = "evaluation";
  evaluationSection.value = section;
  if (section === "config") void loadConfig();
  if (section === "comparison") void refreshComparison();
}

function openEvaluationDiagnostics() {
  selectNav("evaluation");
  selectEvaluationSection("comparison");
}

function toggleSidebar() { sidebarCollapsed.value = !sidebarCollapsed.value; localStorage.setItem("sidebar_collapsed", String(sidebarCollapsed.value)); }

function configMeta(key: string): ParameterMeta { return configData.value?.parameter_meta?.[key] || { label: key, meaning: "" }; }
function formatRange(range?: [number, number]): string { return range ? `[${range[0]}, ${range[1]}]` : "未定义"; }
function configTooltip(key: string): string { const meta = configMeta(key); return `${meta.meaning} 建议范围：${formatRange(meta.recommended)}；硬限制：${formatRange(meta.allowed)}`; }
function draftDisplay(draft: Record<string, unknown>, key: string): string { const value = draft[key]; return value === null || value === undefined ? "" : String(value); }
function updateDraft(draft: Record<string, unknown>, key: string, event: Event) {
  const raw = (event.target as HTMLInputElement).value;
  if (raw === "") { draft[key] = null; return; }
  draft[key] = configMeta(key).integer ? Number.parseInt(raw, 10) : Number(raw);
}
function selectedMetrics(): string[] { return Array.isArray(ragasDraft.value.selected_metrics) ? ragasDraft.value.selected_metrics as string[] : []; }
function isMetricSelected(metric: string): boolean { return selectedMetrics().includes(metric); }
function toggleMetric(metric: string) { const values = selectedMetrics(); ragasDraft.value.selected_metrics = values.includes(metric) ? values.filter((value) => value !== metric) : [...values, metric]; }

function evaluationRagasOptions(): Record<string, unknown> {
  return Object.fromEntries(
    evaluationRagasKeys
      .filter((key) => key in ragasDraft.value)
      .map((key) => [key, ragasDraft.value[key]]),
  );
}

function profileRagasOptions(profile: StrategyProfile): Record<string, unknown> {
  return Object.fromEntries(
    evaluationRagasKeys
      .filter((key) => key in profile.ragas)
      .map((key) => [key, profile.ragas[key]]),
  );
}

function toggleParallelProfile(profileId: string) {
  parallelProfileIds.value = parallelProfileIds.value.includes(profileId)
    ? parallelProfileIds.value.filter((value) => value !== profileId)
    : parallelProfileIds.value.length < 4
      ? [...parallelProfileIds.value, profileId]
      : parallelProfileIds.value;
}

function profileName(profileId?: string): string {
  return strategyProfiles.value.find((profile) => profile.profile_id === profileId)?.name || profileId || "未命名实验";
}

function selectedStrategyProfile(): StrategyProfile | null {
  return strategyProfiles.value.find((profile) => profile.profile_id === strategyProfileId.value) || null;
}

function applyStrategyProfile(profileId = strategyProfileId.value) {
  const profile = strategyProfiles.value.find((item) => item.profile_id === profileId);
  if (!profile) return;
  strategyProfileId.value = profile.profile_id;
  localStorage.setItem(strategyProfileStorageKey, profile.profile_id);
  profileNameDraft.value = profile.name;
  retrievalProfile.value = profile.retrieval_profile;
  ragasProfile.value = profile.ragas_profile;
  retrievalDraft.value = {
    answer_max_contexts: 6,
    answer_context_compression: "none",
    ...profile.retrieval,
  };
  ragasDraft.value = { ...profile.ragas };
}

async function loadStrategyProfileCatalog() {
  const catalog = await api<StrategyProfileCatalog>("/api/rag_eval/profiles");
  strategyProfiles.value = catalog.profiles || [];
  publishedProfileId.value = catalog.published_profile_id || "active_current";
  const storedProfileId = localStorage.getItem(strategyProfileStorageKey);
  const storedProfile = strategyProfiles.value.find((profile) => profile.profile_id === storedProfileId);
  const currentProfile = strategyProfiles.value.find((profile) => profile.profile_id === strategyProfileId.value);
  const preferredProfileId = storedProfile?.profile_id
    || currentProfile?.profile_id
    || catalog.default_profile_id
    || "active_current";
  if (storedProfileId && !storedProfile) localStorage.removeItem(strategyProfileStorageKey);
  applyStrategyProfile(preferredProfileId);
  const availableIds = new Set(strategyProfiles.value.map((profile) => profile.profile_id));
  parallelProfileIds.value = parallelProfileIds.value.filter((profileId) => availableIds.has(profileId));
  if (!parallelProfileIds.value.length && strategyProfiles.value.length) {
    parallelProfileIds.value = strategyProfiles.value.slice(0, Math.min(2, strategyProfiles.value.length)).map((profile) => profile.profile_id);
  }
}

async function loadProductionConfig() {
  try { productionConfig.value = await api<ProductionConfig>("/api/rag_eval/production-config"); }
  catch (error) { configError.value = error instanceof Error ? error.message : "正式配置加载失败"; }
}

async function loadConfig() {
  configLoading.value = true;
  configError.value = "";
  try {
    const data = await api<RagEvalConfig>("/api/rag_eval/config");
    configData.value = data;
    await loadStrategyProfileCatalog();
    await loadProductionConfig();
  } catch (error) { configError.value = error instanceof Error ? error.message : "评测配置加载失败"; }
  finally { configLoading.value = false; }
}


async function saveConfig() {
  configSaving.value = true; configMessage.value = ""; configError.value = "";
  try {
    const profile = selectedStrategyProfile();
    if (!profile) throw new Error("当前策略 profile 不存在，请重新加载");
    if (!profile.editable) throw new Error("内置 profile 只读，请使用“另存为”创建自定义 profile");
    await api<StrategyProfile>("/api/rag_eval/profiles/" + encodeURIComponent(profile.profile_id), {
      method: "PUT",
      body: JSON.stringify({
        name: profileNameDraft.value,
        retrieval_profile: retrievalProfile.value,
        ragas_profile: ragasProfile.value,
        retrieval: { ...retrievalDraft.value },
        ragas: { ...evaluationRagasOptions() },
      }),
    });
    await loadStrategyProfileCatalog();
    applyStrategyProfile(profile.profile_id);
    configMessage.value = "自定义 profile 已保存";
  } catch (error) { configError.value = error instanceof Error ? error.message : "配置保存失败"; }
  finally { configSaving.value = false; }
}

async function saveAsProfile() {
  configSaving.value = true; configMessage.value = ""; configError.value = "";
  try {
    const name = profileNameDraft.value.trim();
    if (!name) throw new Error("请先填写自定义 profile 名称");
    const created = await api<StrategyProfile>("/api/rag_eval/profiles", {
      method: "POST",
      body: JSON.stringify({
        name,
        retrieval_profile: retrievalProfile.value,
        ragas_profile: ragasProfile.value,
        retrieval: { ...retrievalDraft.value },
        ragas: { ...evaluationRagasOptions() },
      }),
    });
    await loadStrategyProfileCatalog();
    applyStrategyProfile(created.profile_id);
    configMessage.value = "已创建自定义 profile：" + created.name;
  } catch (error) { configError.value = error instanceof Error ? error.message : "创建 profile 失败"; }
  finally { configSaving.value = false; }
}

async function deleteProfile() {
  const profile = selectedStrategyProfile();
  if (!profile || !profile.editable) { configError.value = "内置 profile 不可删除"; return; }
  if (!window.confirm("确定删除 profile“" + profile.name + "”吗？")) return;
  configSaving.value = true; configMessage.value = ""; configError.value = "";
  try {
    await api("/api/rag_eval/profiles/" + encodeURIComponent(profile.profile_id), { method: "DELETE" });
    await loadStrategyProfileCatalog();
    configMessage.value = "自定义 profile 已删除";
  } catch (error) { configError.value = error instanceof Error ? error.message : "删除 profile 失败"; }
  finally { configSaving.value = false; }
}

async function publishConfig() {
  configSaving.value = true; configMessage.value = ""; configError.value = "";
  try {
    const profile = selectedStrategyProfile();
    if (!profile || !profile.editable) throw new Error("只有自定义 profile 可以发布为正式配置");
    await api<StrategyProfile>("/api/rag_eval/profiles/" + encodeURIComponent(profile.profile_id) + "/publish", {
      method: "POST",
      body: JSON.stringify({ note: "从 RAG 评测中心显式发布" }),
    });
    await loadProductionConfig();
    await loadStrategyProfileCatalog();
    applyStrategyProfile(profile.profile_id);
    configMessage.value = "已发布为正式 RAG 配置，默认 profile 已切换";
  } catch (error) { configError.value = error instanceof Error ? error.message : "正式配置发布失败"; }
  finally { configSaving.value = false; }
}

async function loadGoldDatasetStatus() {
  try {
    const target = ingestion.value;
    const query = target?.run_id && target?.index_version
      ? `?ingestion_run_id=${encodeURIComponent(target.run_id)}&index_version=${encodeURIComponent(target.index_version)}`
      : "";
    goldDataset.value = await api<GoldDatasetStatus>(`/api/rag_eval/gold-v2/status${query}`);
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : "本地 Gold 基准集状态加载失败";
  }
}

async function loadIngestionHistory() {
  const history = await api<IngestionHistory>("/api/rag_eval/isolated/ingestion-runs?page=1&page_size=50");
  ingestionHistory.value = history.items.filter((run) => run.status === "staged");
}

async function loadReleaseStatus() {
  releaseError.value = "";
  try {
    const params = new URLSearchParams();
    if (ingestionReady.value && ingestion.value?.run_id && ingestion.value.index_version) {
      params.set("ingestion_run_id", ingestion.value.run_id);
      params.set("index_version", ingestion.value.index_version);
      if (evaluationRun.value?.run_id) params.set("evaluation_run_id", evaluationRun.value.run_id);
    }
    releaseStatus.value = await api<ReleaseStatus>(`/api/rag_eval/multimodal/releases/status${params.toString() ? `?${params.toString()}` : ""}`);
  } catch (error) { releaseError.value = error instanceof Error ? error.message : "正式 release 状态加载失败"; }
}

async function checkReleaseGate() {
  releaseError.value = "";
  releaseNotice.value = "";
  if (!ingestionReady.value || !ingestion.value?.run_id || !ingestion.value.index_version) {
    releaseError.value = "请先选择一个已完成的 staged 索引";
    return;
  }
  if (!evaluationRun.value?.run_id) {
    releaseError.value = "请先完成绑定当前索引的自动 Ragas 评测";
    return;
  }
  releaseLoading.value = true;
  try {
    releaseStatus.value = await api<ReleaseStatus>("/api/rag_eval/multimodal/releases/gate-check", {
      method: "POST",
      body: JSON.stringify({
        ingestion_run_id: ingestion.value.run_id,
        index_version: ingestion.value.index_version,
        evaluation_run_id: evaluationRun.value.run_id,
        expected_active_index_version: String(releaseStatus.value?.active?.index_version || ""),
        expected_generation: releaseStatus.value?.generation,
      }),
    });
  } catch (error) { releaseError.value = error instanceof Error ? error.message : "正式发布门禁检查失败"; }
  finally { releaseLoading.value = false; }
}

function openReleaseConfirmation() {
  if (!releasePublishable.value) return;
  releaseConfirmOpen.value = true;
}

async function publishRelease() {
  if (!releasePublishable.value || !ingestion.value?.run_id || !ingestion.value.index_version || !evaluationRun.value?.run_id) return;
  releasePublishing.value = true;
  releaseError.value = "";
  try {
    await api<ReleaseStatus>("/api/rag_eval/multimodal/releases/publish", {
      method: "POST",
      body: JSON.stringify({
        ingestion_run_id: ingestion.value.run_id,
        index_version: ingestion.value.index_version,
        evaluation_run_id: evaluationRun.value.run_id,
        expected_active_index_version: String(releaseStatus.value?.active?.index_version || ""),
        expected_generation: releaseStatus.value?.generation,
        confirm: true,
      }),
    });
    releaseConfirmOpen.value = false;
    releaseNotice.value = "正式 active pointer 已更新；运行中的 worker 需要 drain/restart 后才会使用新索引。";
    await loadReleaseStatus();
  } catch (error) { releaseError.value = error instanceof Error ? error.message : "正式 active pointer 发布失败"; }
  finally { releasePublishing.value = false; }
}

async function rollbackRelease() {
  const previousVersion = String(releaseStatus.value?.previous?.index_version || "");
  const activeVersion = String(releaseStatus.value?.active?.index_version || "");
  if (!previousVersion || releaseRollbackLoading.value) return;
  if (!window.confirm(`确认回滚到 ${previousVersion}？回滚仍会重新执行正式发布门禁。`)) return;
  releaseRollbackLoading.value = true;
  releaseError.value = "";
  try {
    await api<ReleaseStatus>("/api/rag_eval/multimodal/releases/rollback", {
      method: "POST",
      body: JSON.stringify({ index_version: previousVersion, expected_active_index_version: activeVersion, expected_generation: releaseStatus.value?.generation, confirm: true }),
    });
    releaseNotice.value = `已请求回滚到 ${previousVersion}；运行中的 worker 需要 drain/restart 后才会生效。`;
    await loadReleaseStatus();
  } catch (error) { releaseError.value = error instanceof Error ? error.message : "正式 active pointer 回滚失败"; }
  finally { releaseRollbackLoading.value = false; }
}

async function selectIngestionRun(runId: string) {
  const state = await refreshRun("ingestion", runId);
  selectedIngestionRunId.value = state.run_id;
  localStorage.setItem("ingestion_run_id", state.run_id);
  await loadGoldDatasetStatus();
}

async function loadCandidateDataset(runId: string) {
  const state = candidateRun.value;
  if (!state?.candidate_artifact_name) return;
  try {
    const artifact = (name: string) => `/api/rag_eval/isolated/candidate-runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`;
    const [dataset, audit, review] = await Promise.all([
      api<CandidateDataset>(artifact(state.candidate_artifact_name)),
      state.audit_artifact_name ? api<CandidateAudit>(artifact(state.audit_artifact_name)).catch(() => null) : Promise.resolve(null),
      state.review_manifest_artifact_name ? api<CandidateReviewManifest>(artifact(state.review_manifest_artifact_name)).catch(() => null) : Promise.resolve(null),
    ]);
    candidateDataset.value = dataset;
    candidateAudit.value = audit;
    const saved = new Map((review?.decisions || []).map((item) => [item.sample_id, item]));
    candidateReviewedIds.value = new Set(dataset.samples.filter((sample) => saved.has(sample.sample_id)).map((sample) => sample.sample_id));
    candidateDecisions.value = Object.fromEntries(dataset.samples.map((sample) => [sample.sample_id, saved.get(sample.sample_id)?.decision || "needs_revision"]));
    candidateNotes.value = Object.fromEntries(dataset.samples.map((sample) => [sample.sample_id, saved.get(sample.sample_id)?.note || ""]));
    reviewerName.value = review?.reviewer || reviewerName.value;
    candidateReviewIndex.value = Math.min(candidateReviewIndex.value, Math.max(dataset.samples.length - 1, 0));
    candidateReviewPhase.value = candidateReviewedIds.value.size >= dataset.samples.length && dataset.samples.length > 0 ? "complete" : "intro";
  } catch (error) {
    candidateActionError.value = error instanceof Error ? error.message : "候选题产物加载失败";
  }
}

function openCandidateGenerationConfig() {
  candidateActionError.value = "";
  candidateMessage.value = "";
  if (!ingestionReady.value) {
    candidateActionError.value = "请先在工作台选择一个已就绪的 staged 索引。";
    return;
  }
  candidateQuestionCount.value = Math.min(candidateQuestionCount.value, candidateUnitLimit.value);
  candidateGenerationConfigOpen.value = true;
}

async function startCandidateGeneration() {
  candidateActionError.value = "";
  candidateMessage.value = "";
  if (!ingestionReady.value || !ingestion.value) {
    candidateActionError.value = "请先完成知识源摄取并生成隔离索引";
    return;
  }
  if (candidateQuestionCount.value > candidateUnitLimit.value) {
    candidateActionError.value = `当前索引最多支持 ${candidateUnitLimit.value} 道自动评测题；请扩大索引或降低题目数量。`;
    return;
  }
  candidateLoading.value = true;
  try {
    const state = await api<RunState>("/api/rag_eval/isolated/candidate-runs", {
      method: "POST",
      body: JSON.stringify({
        ingestion_run_id: ingestion.value.run_id,
        index_version: ingestion.value.index_version,
        question_count: candidateQuestionCount.value,
        max_workers: candidateMaxWorkers.value,
      }),
    });
    candidateRun.value = state;
    candidateDataset.value = null;
    candidateAudit.value = null;
    candidateReviewedIds.value = new Set();
    candidateReviewPhase.value = "intro";
    candidateReviewIndex.value = 0;
    candidateGenerationConfigOpen.value = false;
    localStorage.setItem("candidate_run_id", state.run_id);
    candidateMessage.value = `已基于 ${ingestionDisplayName(ingestion.value)} 创建新的候选题生成任务；旧审核记录保留在历史运行中。`;
    watchRun("candidate", state.run_id);
  } catch (error) {
    candidateLoading.value = false;
    candidateActionError.value = error instanceof Error ? error.message : "候选题生成启动失败";
  }
}

function startCandidateReview() {
  if (!candidateDataset.value) return;
  const firstPendingIndex = candidateDataset.value.samples.findIndex((sample) => !candidateReviewedIds.value.has(sample.sample_id));
  candidateReviewIndex.value = firstPendingIndex >= 0 ? firstPendingIndex : 0;
  candidateReviewPhase.value = candidateReviewCounts.value.reviewed >= candidateReviewCounts.value.total ? "complete" : "review";
}

function openCandidateSample(index: number) {
  if (!candidateDataset.value?.samples.length) return;
  candidateReviewIndex.value = Math.min(Math.max(index, 0), candidateDataset.value.samples.length - 1);
  candidateReviewPhase.value = "review";
}

function setCandidateDecision(decision: "approved" | "rejected" | "needs_revision") {
  const sample = currentCandidateSample.value;
  if (!sample) return;
  candidateDecisions.value = { ...candidateDecisions.value, [sample.sample_id]: decision };
  candidateReviewedIds.value = new Set(candidateReviewedIds.value).add(sample.sample_id);
  const next = nextReviewState(
    candidateReviewIndex.value,
    candidateDataset.value?.samples.length || 0,
    candidateReviewedIds.value.size,
  );
  candidateReviewPhase.value = next.phase;
  if (next.phase === "review") candidateReviewIndex.value = next.index;
}

async function saveCandidateReview() {
  if (!candidateRun.value || !candidateDataset.value) return;
  const phaseBeforeSave = candidateReviewPhase.value;
  const reviewedBeforeSave = new Set(candidateReviewedIds.value);
  candidateActionError.value = "";
  candidateMessage.value = "";
  candidateLoading.value = true;
  try {
    const payload = {
      reviewer: reviewerName.value.trim(),
      decisions: candidateDataset.value.samples.map((sample) => ({ sample_id: sample.sample_id, decision: candidateDecisions.value[sample.sample_id] || "needs_revision", note: candidateNotes.value[sample.sample_id] || "" })),
      updates: candidateDataset.value.samples.map((sample) => ({ sample_id: sample.sample_id, question: sample.question, reference_answer: sample.reference_answer, expected_claims: sample.expected_claims || [], gold_evidence: sample.gold_evidence || [] })),
    };
    const result = await api<{ candidate_artifact_name: string; review_manifest_artifact_name: string }>(`/api/rag_eval/isolated/candidate-runs/${encodeURIComponent(candidateRun.value.run_id)}/review`, { method: "POST", body: JSON.stringify(payload) });
    candidateRun.value = { ...candidateRun.value, ...result };
    candidateMessage.value = "复核结果已保存；当前自动评测集仍可直接使用。";
    await loadCandidateDataset(candidateRun.value.run_id);
    if (phaseBeforeSave === "review" && reviewedBeforeSave.size < (candidateDataset.value?.samples.length || 0)) {
      candidateReviewedIds.value = reviewedBeforeSave;
      candidateReviewPhase.value = "review";
    }
  } catch (error) {
    candidateActionError.value = error instanceof Error ? error.message : "审核保存失败";
  } finally { candidateLoading.value = false; }
}

async function rebindCandidateToCurrentIndex() {
  if (!candidateRun.value || !ingestionReady.value || !ingestion.value?.index_version) {
    candidateActionError.value = "请先在评测中心选择一个已就绪的 staged 索引。";
    return;
  }
  candidateActionError.value = "";
  candidateMessage.value = "";
  candidateLoading.value = true;
  try {
    const result = await api<{ candidate_artifact_name: string; review_manifest_artifact_name: string }>(`/api/rag_eval/isolated/candidate-runs/${encodeURIComponent(candidateRun.value.run_id)}/rebind`, {
      method: "POST",
      body: JSON.stringify({ ingestion_run_id: ingestion.value.run_id, index_version: ingestion.value.index_version }),
    });
    candidateRun.value = { ...candidateRun.value, ...result };
    candidateMessage.value = `已重绑到 ${ingestionDisplayName(ingestion.value)}；所有候选题已回到待复审状态。`;
    candidateReviewedIds.value = new Set();
    candidateReviewPhase.value = "intro";
    candidateReviewIndex.value = 0;
    await loadCandidateDataset(candidateRun.value.run_id);
  } catch (error) {
    candidateActionError.value = error instanceof Error ? error.message : "候选题 locator 重绑失败";
  } finally { candidateLoading.value = false; }
}

async function freezeGoldDataset(replaceExisting = false) {
  if (!candidateRun.value || !ingestion.value?.index_version || !candidateBoundToSelectedIndex.value) {
    candidateActionError.value = "当前候选题仍绑定其他索引；请先重绑到所选 staged 索引并重新审核。";
    return;
  }
  candidateActionError.value = "";
  candidateMessage.value = "";
  candidateFreezeLoading.value = true;
  try {
    const result = await api<{ sample_count: number; archived_dataset_path?: string }>("/api/rag_eval/gold-v2/freeze", {
      method: "POST",
      body: JSON.stringify({
        candidate_run_id: candidateRun.value.run_id,
        ingestion_run_id: ingestion.value.run_id,
        index_version: ingestion.value.index_version,
        replace_existing: replaceExisting,
      }),
    });
    goldReplaceDialogOpen.value = false;
    candidateMessage.value = result.archived_dataset_path ? `已替换并冻结 ${result.sample_count} 题 Gold 基准集；旧基准已归档。` : `已冻结 ${result.sample_count} 题 Gold 基准集。`;
    await loadGoldDatasetStatus();
  } catch (error) {
    const message = error instanceof Error ? error.message : "冻结 Gold 基准集失败";
    if (!replaceExisting && /already exists|已存在/i.test(message)) goldReplaceDialogOpen.value = true;
    else candidateActionError.value = message;
  } finally { candidateFreezeLoading.value = false; }
}

async function bindEvaluationBaseline() {
  candidateActionError.value = "";
  candidateMessage.value = "";
  if (!canBindProductionBaseline.value) {
    candidateActionError.value = "正式生产基准只绑定已发布的 active 索引；请先完成当前 staged 索引的重绑、复审和冻结，或将该索引发布为正式索引。";
    return;
  }
  try {
    await api("/api/rag_eval/baseline-v2/bind", { method: "POST", body: "{}" });
    candidateMessage.value = "已绑定评测基准：当前 Gold 基准集与正式检索配置会作为后续对比基线。";
  } catch (error) {
    candidateActionError.value = error instanceof Error ? error.message : "绑定评测基准失败";
  }
}

async function startTuningDatasetRun() {
  tuningDatasetError.value = "";
  if (!ingestionReady.value || !ingestion.value) {
    tuningDatasetError.value = "请先在工作台完成知识源摄取并生成隔离索引。";
    return;
  }
  if (evaluationActive.value || tuningDatasetActive.value) return;
  tuningDatasetLoading.value = true;
  events.value = [];
  try {
    const state = await api<TuningDatasetRunState>("/api/rag_eval/isolated/tuning-dataset-runs", {
      method: "POST",
      body: JSON.stringify({
        ingestion_run_id: ingestion.value.run_id,
        index_version: ingestion.value.index_version,
      }),
    });
    tuningDatasetRun.value = state;
    localStorage.setItem("tuning_dataset_run_id", state.run_id);
    watchRun("tuning_dataset", state.run_id);
  } catch (error) {
    tuningDatasetLoading.value = false;
    tuningDatasetError.value = error instanceof Error ? error.message : "调参测试集治理启动失败";
  }
}

async function startEvaluation() {
  actionError.value = "";
  if (!ingestionReady.value || !ingestion.value) { actionError.value = "请先在工作台完成知识源摄取并生成隔离索引"; return; }
  const generatedDataset = generatedEvaluationDatasetReady.value ? candidateDataset.value : null;
  if (!generatedDataset) { actionError.value = "请先在评测集页面自动生成一批测试题"; return; }
  const datasetPayload = { dataset_source: "generated_candidate", eval_dataset: generatedDataset };
  const strategy = selectedStrategyProfile();
  if (!strategy) { actionError.value = "当前策略 profile 不存在，请重新加载"; return; }
  const steps = executeRagas.value
    ? ["validate_datasets", "retrieval_eval", "ragas_eval", "trace_export", "summary"]
    : ["validate_datasets", "retrieval_eval", "summary"];
  evaluationLoading.value = true; evaluationResult.value = null; events.value = [];
  try {
    if (parallelEvaluationEnabled.value) {
      const profiles = parallelProfileIds.value
        .map((profileId) => strategyProfiles.value.find((profile) => profile.profile_id === profileId))
        .filter((profile): profile is StrategyProfile => Boolean(profile));
      if (profiles.length < 2 || profiles.length > 4) throw new Error("并行实验请选择 2 到 4 个不同策略 profile");
      const batch = await api<EvaluationBatch>("/api/rag_eval/isolated/evaluation-batches", {
        method: "POST",
        body: JSON.stringify({
          ingestion_run_id: ingestion.value.run_id,
          index_version: ingestion.value.index_version,
          ...datasetPayload,
          experiments: profiles.map((profile) => ({
            strategy_profile: { profile_id: profile.profile_id, name: profile.name, kind: profile.kind },
            retrieval: {
              profile: profile.retrieval_profile,
              overrides: profile.profile_id === strategyProfileId.value ? { ...retrievalDraft.value } : { ...profile.retrieval },
            },
            ragas: {
              profile: profile.ragas_profile,
              ...(profile.profile_id === strategyProfileId.value ? evaluationRagasOptions() : profileRagasOptions(profile)),
              run: executeRagas.value,
              prepare_only: false,
            },
            steps,
          })),
        }),
      });
      stopWatching();
      evaluationBatchRuns.value = batch.runs;
      evaluationRun.value = batch.runs[0] || null;
      localStorage.setItem("evaluation_batch_run_ids", JSON.stringify(batch.runs.map((run) => run.run_id)));
      if (evaluationRun.value) localStorage.setItem("evaluation_run_id", evaluationRun.value.run_id);
      startBatchPolling();
      evaluationSection.value = "events";
      return;
    }
    stopBatchPolling();
    evaluationBatchRuns.value = [];
    localStorage.removeItem("evaluation_batch_run_ids");
    const state = await api<RunState>("/api/rag_eval/isolated/evaluation-runs", {
      method: "POST",
      body: JSON.stringify({
        ingestion_run_id: ingestion.value.run_id,
        index_version: ingestion.value.index_version,
        ...datasetPayload,
        strategy_profile: { profile_id: strategy.profile_id, name: strategy.name, kind: strategy.kind },
        retrieval: { profile: retrievalProfile.value, overrides: { ...retrievalDraft.value } },
        ragas: { profile: ragasProfile.value, ...evaluationRagasOptions(), run: executeRagas.value, prepare_only: false },
        steps,
      }),
    });
    evaluationRun.value = state;
    evaluationAwaitingCompletion.value = state.run_id;
    localStorage.setItem("evaluation_run_id", state.run_id);
    watchRun("evaluation", state.run_id);
    evaluationSection.value = "events";
  } catch (error) { evaluationLoading.value = false; actionError.value = error instanceof Error ? error.message : "评测任务启动失败"; }
}

async function cancelEvaluation() {
  if (parallelEvaluationActive.value) {
    try {
      await Promise.all(evaluationBatchRuns.value
        .filter((run) => !terminalStatuses.includes(run.status))
        .map((run) => api<RunState>(`/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(run.run_id)}/cancel`, { method: "POST", body: "{}" })));
      await refreshEvaluationBatch();
    } catch (error) { actionError.value = error instanceof Error ? error.message : "取消并行评测失败"; }
    return;
  }
  if (!evaluationRun.value) return;
  try {
    evaluationRun.value = await api<RunState>(`/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(evaluationRun.value.run_id)}/cancel`, { method: "POST", body: "{}" });
    notifyEvaluationFinished(evaluationRun.value);
  }
  catch (error) { actionError.value = error instanceof Error ? error.message : "取消评测失败"; }
}

async function restoreIngestionRun(preferredId: string | null): Promise<boolean> {
  try {
    const history = await api<IngestionHistory>("/api/rag_eval/isolated/ingestion-runs?page=1&page_size=50");
    ingestionHistory.value = history.items.filter((run) => run.status === "staged");
    const preferredRun = history.items.find((item) => item.run_id === preferredId)
      || history.items.find((item) => ["created", "queued", "running", "cancelling"].includes(item.status))
      || history.items.find((item) => item.status === "staged");
    if (preferredRun) {
      ingestion.value = preferredRun;
      selectedIngestionRunId.value = preferredRun.run_id;
      localStorage.setItem("ingestion_run_id", preferredRun.run_id);
      if (!terminalStatuses.includes(preferredRun.status)) watchRun("ingestion", preferredRun.run_id);
      sourceNotice.value = (terminalStatuses.includes(preferredRun.status) ? "已恢复最新完成的摄取任务：" : "已恢复当前运行中的摄取任务：") + preferredRun.run_id;
      return true;
    }
  } catch {
    // 保持旧 run 恢复路径，避免历史接口临时不可用时页面无法恢复。
  }

  if (preferredId) {
    try {
      const state = await refreshRun("ingestion", preferredId);
      selectedIngestionRunId.value = state.run_id;
      if (!terminalStatuses.includes(state.status)) watchRun("ingestion", preferredId);
      return true;
    } catch {
      localStorage.removeItem("ingestion_run_id");
      localStorage.removeItem("rag_eval_ingestion_run_id");
    }
  }

  try {
    const history = await api<IngestionHistory>("/api/rag_eval/isolated/ingestion-runs?page=1&page_size=50");
    return false;
  } catch {
    return false;
  }
}

async function refreshWorkspace() {
  actionError.value = "";
  await loadCatalog();
  await loadIngestionHistory();
  const ingestionId = localStorage.getItem("ingestion_run_id") || localStorage.getItem("rag_eval_ingestion_run_id");
  await restoreIngestionRun(ingestionId);
  await loadGoldDatasetStatus();
}

async function restoreRuns() {
  const ingestionId = localStorage.getItem("ingestion_run_id") || localStorage.getItem("rag_eval_ingestion_run_id");
  const candidateId = localStorage.getItem("candidate_run_id");
  const evaluationId = localStorage.getItem("evaluation_run_id") || localStorage.getItem("rag_eval_evaluation_run_id");
  const storedBatchIds = (() => {
    try {
      const value = JSON.parse(localStorage.getItem("evaluation_batch_run_ids") || "[]");
      return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
    } catch { return []; }
  })();
  await restoreIngestionRun(ingestionId);
  if (candidateId) {
    try {
      const state = await refreshRun("candidate", candidateId);
      if (!terminalStatuses.includes(state.status)) watchRun("candidate", candidateId);
      else if (state.status === "succeeded" && candidateBoundToSelectedIndex.value) await loadCandidateDataset(candidateId);
      else if (state.status === "succeeded") {
        candidateRun.value = null;
        localStorage.removeItem("candidate_run_id");
        candidateMessage.value = "已忽略与当前工作索引不一致的历史候选题；请基于当前索引生成新的候选集。";
      }
    } catch { localStorage.removeItem("candidate_run_id"); }
  }
  const tuningDatasetId = localStorage.getItem("tuning_dataset_run_id");
  if (tuningDatasetId) {
    try {
      const state = await refreshRun("tuning_dataset", tuningDatasetId);
      if (!terminalStatuses.includes(state.status)) watchRun("tuning_dataset", tuningDatasetId);
    } catch { localStorage.removeItem("tuning_dataset_run_id"); }
  }
  if (storedBatchIds.length >= 2) {
    try {
      evaluationBatchRuns.value = await Promise.all(storedBatchIds.map((runId) =>
        api<RunState>(`/api/rag_eval/isolated/evaluation-runs/${encodeURIComponent(runId)}`),
      ));
      parallelEvaluationEnabled.value = true;
      const batchProfileIds = evaluationBatchRuns.value
        .map((run) => String(run.strategy_profile?.profile_id || ""))
        .filter(Boolean);
      if (batchProfileIds.length) parallelProfileIds.value = batchProfileIds;
      evaluationRun.value = evaluationBatchRuns.value.find((run) => run.run_id === evaluationId) || evaluationBatchRuns.value[0];
      events.value = evaluationRun.value?.events || [];
      if (parallelEvaluationActive.value) startBatchPolling();
      else evaluationLoading.value = false;
      return;
    } catch {
      evaluationBatchRuns.value = [];
      localStorage.removeItem("evaluation_batch_run_ids");
    }
  }
  if (evaluationId) {
    try {
      const state = await refreshRun("evaluation", evaluationId);
      if (!terminalStatuses.includes(state.status)) watchRun("evaluation", evaluationId);
    } catch {
      localStorage.removeItem("evaluation_run_id");
      localStorage.removeItem("rag_eval_evaluation_run_id");
    }
  }
}

watch([historyRange, historySource], () => { if (activeNav.value === "reports" || evaluationSection.value === "comparison") void refreshComparison(); });
watch(comparisonMode, () => { if (evaluationSection.value === "comparison") void refreshComparison(); });
watch(strategyProfileId, (profileId, previousProfileId) => {
  if (profileId !== previousProfileId && strategyProfiles.value.length) {
    localStorage.setItem(strategyProfileStorageKey, profileId);
    applyStrategyProfile(profileId);
  }
});

onMounted(async () => {
  document.addEventListener("visibilitychange", refreshVisibleRun);
  sidebarCollapsed.value = localStorage.getItem("sidebar_collapsed") === "true" || localStorage.getItem("rag_eval_sidebar_collapsed") === "true";
  await loadCsrfToken();
  await loadCatalog();
  await loadConfig();
  await loadGoldDatasetStatus();
  await restoreRuns();
});
onUnmounted(() => {
  document.removeEventListener("visibilitychange", refreshVisibleRun);
  stopWatching();
  stopBatchPolling();
  dismissEvaluationToast();
});

function refreshVisibleRun() {
  if (document.hidden) return;
  if (tuningDatasetRun.value && !terminalStatuses.includes(tuningDatasetRun.value.status)) {
    void refreshRun("tuning_dataset", tuningDatasetRun.value.run_id).catch(() => undefined);
  } else if (evaluationRun.value && !terminalStatuses.includes(evaluationRun.value.status)) {
    void refreshRun("evaluation", evaluationRun.value.run_id).catch(() => undefined);
  } else if (candidateRun.value && !terminalStatuses.includes(candidateRun.value.status)) {
    void refreshRun("candidate", candidateRun.value.run_id).catch(() => undefined);
  } else if (ingestion.value && !terminalStatuses.includes(ingestion.value.status)) {
    void refreshRun("ingestion", ingestion.value.run_id).catch(() => undefined);
  }
}
</script>

<template>
  <div v-if="evaluationToast" class="evaluation-toast" role="status" aria-live="polite">
    <Check :size="17" />
    <span>{{ evaluationToast }}</span>
    <button type="button" aria-label="关闭提示" @click="dismissEvaluationToast">×</button>
  </div>
  <div class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="app-sidebar">
      <div class="brand-row">
        <div class="brand-mark"><span class="brand-icon"><img :src="brandMarkUrl" alt="" width="36" height="36" /></span><span class="brand-label">因果知识台</span></div>
        <button type="button" class="sidebar-toggle" :title="sidebarCollapsed ? '展开导航栏' : '收起导航栏'" :aria-label="sidebarCollapsed ? '展开导航栏' : '收起导航栏'" @click="toggleSidebar"><PanelLeftOpen v-if="sidebarCollapsed" :size="17" /><PanelLeftClose v-else :size="17" /></button>
      </div>
      <div class="sidebar-caption">CAUSAL AGENT</div>
      <nav class="side-nav" aria-label="主导航">
        <CaButton variant="quiet" class="nav-item" :class="{ active: activeNav === 'workspace' }" title="工作台" aria-label="工作台" :aria-current="activeNav === 'workspace' ? 'page' : undefined" @click="selectNav('workspace')"><template #icon><LayoutDashboard :size="17" /></template><span>工作台</span></CaButton>
        <CaButton variant="quiet" class="nav-item" :class="{ active: activeNav === 'candidates' }" title="自动生成评测集" aria-label="自动生成评测集" :aria-current="activeNav === 'candidates' ? 'page' : undefined" @click="selectNav('candidates')"><template #icon><FileText :size="17" /></template><span>自动生成评测集</span></CaButton>
        <CaButton variant="quiet" class="nav-item" :class="{ active: activeNav === 'evaluation' }" title="评测中心" aria-label="评测中心" :aria-current="activeNav === 'evaluation' ? 'page' : undefined" @click="selectNav('evaluation')"><template #icon><Gauge :size="17" /></template><span>评测中心</span></CaButton>
        <div v-if="activeNav === 'evaluation'" class="nav-submenu" aria-label="评测中心导航">
          <CaButton variant="quiet" size="sm" class="nav-subitem" :class="{ active: evaluationSection === 'config' }" :aria-current="evaluationSection === 'config' ? 'page' : undefined" @click="selectEvaluationSection('config')">评测配置</CaButton>
          <CaButton variant="quiet" size="sm" class="nav-subitem" :class="{ active: evaluationSection === 'events' }" :aria-current="evaluationSection === 'events' ? 'page' : undefined" @click="selectEvaluationSection('events')">评测流程事件</CaButton>
          <CaButton variant="quiet" size="sm" class="nav-subitem" :class="{ active: evaluationSection === 'comparison' }" :aria-current="evaluationSection === 'comparison' ? 'page' : undefined" @click="selectEvaluationSection('comparison')">对比分析</CaButton>
        </div>
        <CaButton variant="quiet" class="nav-item" :class="{ active: activeNav === 'release' }" title="正式发布" aria-label="正式发布" :aria-current="activeNav === 'release' ? 'page' : undefined" @click="selectNav('release')"><template #icon><ShieldCheck :size="17" /></template><span>正式发布</span></CaButton>
        <CaButton variant="quiet" class="nav-item" :class="{ active: activeNav === 'reports' }" title="报告编辑" aria-label="报告编辑" :aria-current="activeNav === 'reports' ? 'page' : undefined" @click="selectNav('reports')"><template #icon><FileChartColumn :size="17" /></template><span>报告编辑</span></CaButton>
      </nav>
      <div class="sidebar-footer"><span class="sidebar-status-dot"></span><div><strong>隔离环境</strong><small>隔离索引专用</small></div></div>
    </aside>

    <section class="app-main" :class="{ 'comparison-main': activeNav === 'evaluation' && evaluationSection === 'comparison' }">
      <header class="topbar">
        <CaPageHeader
          :level="1"
          size="md"
          :title="activeNav === 'workspace' ? '隔离知识源工作台' : activeNav === 'candidates' ? '自动生成评测集' : activeNav === 'release' ? '正式发布控制台' : activeNav === 'reports' ? '报告编辑' : evaluationSection === 'config' ? '评测配置' : evaluationSection === 'events' ? '评测流程事件' : '对比分析'"
          description="知识检索与评测"
        >
          <template #actions>
            <div class="topbar-meta">
              <CaBadge tone="muted"><span class="live-dot"></span><span>后端接口已连接</span></CaBadge>
              <CaButton
                variant="secondary"
                size="sm"
                title="刷新当前数据"
                aria-label="刷新当前数据"
                @click="activeNav === 'reports' ? loadEvaluationHistory() : activeNav === 'release' ? loadReleaseStatus() : refreshWorkspace()"
              >
                <template #icon><RefreshCw :size="16" :class="{ spinning: sourceLoading || historyLoading || releaseLoading }" /></template>
                <span class="visually-hidden">刷新当前数据</span>
              </CaButton>
            </div>
          </template>
        </CaPageHeader>
      </header>

      <main class="content" :class="{ 'comparison-content': activeNav === 'evaluation' && evaluationSection === 'comparison' }">
        <template v-if="activeNav === 'workspace'">
          <div class="stage-line" aria-label="运行阶段"><div class="stage-marker active"><span>01</span><strong>知识源摄取</strong><small>多模态解析 · 标准化 · Chroma</small></div><div class="stage-connector"></div><div class="stage-marker" :class="{ active: ingestionReady }"><span>02</span><strong>评测中心</strong><small>retrieval · Ragas · 报告</small></div><div class="stage-connector"></div><div class="stage-marker" :class="{ active: releasePublishable }"><span>03</span><strong>正式发布</strong><small>门禁 · active pointer</small></div></div>
          <div v-if="actionError || catalogError" class="design-error-state"><CaErrorState title="工作台暂时无法继续" :description="actionError || catalogError" /></div>
          <div v-if="sourceNotice" class="alert success-alert"><Check :size="17" /><span>{{ sourceNotice }}</span></div>
          <section class="workspace-grid workbench-layout">
            <article class="panel source-panel">
              <div class="panel-header"><div class="panel-title"><span class="icon-badge coral"><Database :size="18" /></span><div><h2>选择知识源</h2></div></div><div class="source-panel-actions"><span class="count-label">{{ selectedSources.length }} / {{ sources.length }}</span><input ref="uploadInput" class="visually-hidden" type="file" :accept="supportedUploadExtensions.join(',')" @change="uploadSource" /><button type="button" class="secondary-button upload-button" :disabled="busy" @click="openUploadDialog"><Upload :size="15" />{{ uploadLoading ? '上传中' : '上传知识源' }}</button></div></div>
              <div class="source-capability-note"><strong>多模态解析</strong><span>支持 {{ supportedUploadLabel }}；文本、表格、图片以及 PDF 中的版面、公式、表格和图片会统一解析为可追溯的知识单元。</span></div>
              <div class="index-selector workspace-index-selector"><span>全局工作索引</span><div class="index-selector-row"><select v-model="selectedIngestionRunId" @change="selectIngestionRun(selectedIngestionRunId)"><option v-for="run in ingestionHistory" :key="run.run_id" :value="run.run_id">{{ ingestionDisplayName(run) }}</option></select><button v-if="ingestion && terminalStatuses.includes(ingestion.status)" type="button" class="secondary-button danger index-delete-button" :disabled="busy" :aria-label="ingestionDeleteLoading ? '正在删除索引' : '删除索引'" @click="deleteSelectedIngestion"><Trash2 :size="15" /></button></div></div>
              <div v-if="sourceLoading && !sources.length" class="empty-line"><CaLoadingState variant="line" label="加载来源目录" /></div>
              <div v-else-if="!sources.length" class="empty-line"><CaEmptyState align="start" description="暂无可选来源"><template #icon><FileText :size="18" /></template></CaEmptyState></div>
              <div v-else class="source-list"><div v-for="source in sources" :key="source.source_id" class="source-row" :class="{ selected: selectedSourceIds.includes(source.source_id) }"><label class="source-select"><input type="checkbox" :checked="selectedSourceIds.includes(source.source_id)" @change="toggleSource(source.source_id)" /><span class="source-check"><Check :size="14" /></span><span class="source-copy"><strong>{{ source.display_name || source.name }}</strong><small>{{ source.source_kind === 'uploaded' ? '用户上传 · 仅隔离评测' : '固定来源 · 可申请正式发布' }} · {{ source.page_count ? `${source.page_count} 页` : '页数待读取' }} · {{ formatBytes(source.size_bytes) }} · {{ source.content_sha256.slice(0, 12) }}</small></span></label><label v-if="selectedSourceIds.includes(source.source_id)" class="source-vlm-consent"><input type="checkbox" :checked="remoteAuthorizedSourceIds.includes(source.source_id)" @change="toggleRemoteAuthorization(source.source_id)" /><span>允许 VLM</span></label><span class="source-actions"><button type="button" class="source-rename-button" :disabled="busy" @click="renameSource(source)">改名</button><button v-if="source.source_kind === 'uploaded'" type="button" class="source-delete-button" :disabled="busy || sourceDeleteLoading === source.source_id" :title="`删除 ${source.name}`" :aria-label="`删除 ${source.name}`" @click="deleteSource(source)"><Trash2 :size="15" /></button></span></div></div>
              <div class="run-options"><label>运行范围<select v-model="pageLimit"><option value="4">快速联调 · 4 页</option><option value="12">Smoke · 12 页</option><option value="all">全部来源页</option><option value="custom">自定义页码范围</option></select></label><span>{{ pageLimit === 'custom' ? '按来源分别执行物理页范围' : '快速模式按选中来源顺序累计页数' }}；每次创建新的隔离索引</span></div>
              <div class="vlm-consent-summary"><ShieldCheck :size="15" /><span>远程 VLM 默认关闭；本次已授权 {{ selectedRemoteSources.length }} / {{ selectedSources.length }} 个来源。</span></div>
              <div v-if="pageLimit === 'custom'" class="custom-range-panel"><div class="custom-range-heading"><strong>按来源设置物理页码</strong><small>页码从 1 开始，首尾包含；本次共 {{ customPageTotal }} 页</small></div><div v-for="source in selectedSources" :key="`range-${source.source_id}`" class="custom-range-row"><span>{{ source.name }}</span><input v-model="pageRanges[source.source_id].start" type="number" min="1" aria-label="开始页" /><span>至</span><input v-model="pageRanges[source.source_id].end" type="number" min="1" aria-label="结束页" /><small>页</small></div></div>
              <div class="panel-footer"><button class="primary-button" :disabled="busy || !selectedSourceIds.length" @click="startIngestion"><Play :size="16" />{{ ingestionReady ? '重新摄取' : '开始摄取' }}</button><button v-if="ingestion && ['created','running','cancelling'].includes(ingestion.status)" class="secondary-button danger" :disabled="ingestion.status === 'cancelling'" @click="cancelIngestion">取消</button></div>
              <div v-if="ingestion" class="run-summary"><div class="summary-line"><span>当前索引</span><strong>{{ ingestionDisplayName(ingestion) }}</strong><span class="status-pill" :class="statusTone(ingestion.status)">{{ statusLabel(ingestion.status) }}</span></div><div class="progress-track"><span :style="{ width: ingestion.status === 'staged' ? '100%' : ingestion.status === 'running' ? '48%' : '0%' }"></span></div><div class="summary-metrics"><span>units <b>{{ ingestion.unit_count ?? '--' }}</b></span><span>vectors <b>{{ ingestion.vector_count ?? '--' }}</b></span><span title="索引版本 ID">版本 <code>{{ ingestion.index_version || '--' }}</code></span></div></div>
            </article>
              <aside class="panel workspace-guide"><div class="panel-header"><div class="panel-title"><span class="icon-badge teal"><SlidersHorizontal :size="18" /></span><div><h2>统一评测流程</h2></div></div></div><div class="guide-body"><div class="guide-state" :class="{ ready: ingestionReady }"><Check :size="17" /><span>{{ ingestionReady ? '索引已就绪' : '等待索引就绪' }}</span></div><p>索引就绪后，先指定数量自动生成评测集，再运行 retrieval、Ragas judge、事件和报告；不要求手工 Gold。</p><button class="primary-button" :disabled="!ingestionReady" @click="selectNav('candidates')"><FileText :size="16" />生成自动评测集</button><button class="secondary-button" :disabled="!ingestionReady" @click="selectNav('evaluation')"><Gauge :size="16" />前往评测中心</button></div></aside>
          </section>
          <CaCard as="section" variant="raised" padding="none" class="panel tuning-dataset-panel" aria-labelledby="tuning-dataset-title">
            <div class="panel-header">
              <div class="panel-title"><span class="icon-badge teal"><Target :size="18" /></span><div><p class="eyebrow">QUESTION GOVERNANCE</p><h2 id="tuning-dataset-title">低分题自动替换</h2></div></div>
              <span v-if="tuningDatasetRun" class="status-pill" :class="statusTone(tuningDatasetRun.status)">{{ statusLabel(tuningDatasetRun.status) }}</span>
            </div>
            <div class="tuning-dataset-body">
              <div class="tuning-dataset-copy"><strong>自动淘汰未达门槛的生成题，由 AI 复核的新替补题顶替，循环实测直到整组达标</strong><p>针对当前索引的 Gold 自动生成题做质量闭环：四项 Ragas 指标任一低于最低单题分即视为坏题淘汰。已通过题沿用同索引历史逐题证据，不再重复实测；替补题必须通过证据核验与 AI 审核才会顶替上场。基线优先继承本索引最近一次登记的调参集。全程隔离，不进入正式评测报告、对比分析或 Gold。</p></div>
              <div class="tuning-dataset-facts"><span><small>目标题数</small><b>48</b></span><span><small>最低单题分</small><b>0.20</b></span><span><small>当前索引</small><code>{{ ingestion?.index_version || '--' }}</code></span></div>
              <div v-if="tuningDatasetError" class="review-feedback error"><AlertCircle :size="15" /><span>{{ tuningDatasetError }}</span></div>
              <div class="tuning-dataset-actions"><button class="primary-button" :disabled="tuningDatasetLoading || tuningDatasetActive || !ingestionReady" @click="startTuningDatasetRun"><Target :size="16" />{{ tuningDatasetActive ? '治理进行中…' : tuningDatasetLoading ? '提交中…' : '开始自动治理' }}</button><button v-if="tuningDatasetActive" class="secondary-button danger" disabled>请等待当前轮完成</button><button v-if="tuningDatasetRun && terminalStatuses.includes(tuningDatasetRun.status)" class="secondary-button danger" :disabled="tuningDatasetDeleteLoading" @click="deleteTuningDatasetRun"><Trash2 :size="15" />{{ tuningDatasetDeleteLoading ? '删除中…' : '删除本次治理' }}</button></div>
              <div v-if="tuningDatasetRun" class="tuning-dataset-status">
                <span>运行 {{ tuningDatasetRun.run_id }}</span>
                <span v-if="tuningDatasetRun.round">第 {{ tuningDatasetRun.round }} 轮</span>
                <span v-if="tuningDatasetRun.current_stage">阶段：{{ tuningDatasetRun.current_stage }}</span>
                <span v-if="tuningDatasetRun.question_count">本轮评测题数：{{ tuningDatasetRun.question_count }}</span>
                <span v-if="tuningDatasetRun.missing_count">待补题数：{{ tuningDatasetRun.missing_count }}</span>
                <span v-if="typeof tuningDatasetRun.carried_evidence_count === 'number'">沿用历史证据：{{ tuningDatasetRun.carried_evidence_count }} 题</span>
                <span v-if="typeof tuningDatasetRun.fresh_evaluated_count === 'number'">本次实测：{{ tuningDatasetRun.fresh_evaluated_count }} 题</span>
                <span v-if="tuningDatasetRun.baseline_source">基线：{{ tuningDatasetRun.baseline_source === 'pearl_gold_v2' ? 'Gold 自动题' : tuningDatasetRun.baseline_source }}</span>
                <span v-if="tuningDatasetRun.reused_across_configs" title="沿用题的证据来自不同检索或 judge 配置，集合级指标为混合配置结果">混合配置证据</span>
              </div>
            </div>
          </CaCard>
        </template>

        <template v-else-if="activeNav === 'candidates'">
          <div class="candidate-advanced-toggle"><span>默认流程不依赖手工 Gold 题集；固定 Gold 仅供基准治理使用。</span><button type="button" class="secondary-button" @click="showGoldGovernance = !showGoldGovernance">{{ showGoldGovernance ? '隐藏固定 Gold 管理' : '高级：管理固定 Gold' }}</button></div>
          <div v-if="candidateActionError" class="alert danger-alert"><AlertCircle :size="17" /><span>{{ candidateActionError }}</span></div>
          <section v-if="showGoldGovernance" class="panel dataset-review-gate" aria-labelledby="dataset-review-gate-title">
            <div class="panel-header dataset-review-gate-header">
              <div class="panel-title"><span class="icon-badge slate"><ShieldCheck :size="18" /></span><div><p class="eyebrow">DATASET REVIEW &amp; RELEASE</p><h2 id="dataset-review-gate-title">题集审核与发布</h2></div></div>
              <span class="status-pill" :class="goldDataset.exists ? 'success' : 'muted'">{{ goldDataset.exists ? '当前 Gold 已冻结' : '等待审核发布' }}</span>
            </div>
            <div class="dataset-review-intro">
              <div><strong>逐题审核通过后，冻结你自己的正式基准集</strong><p>评测结果只生成只读诊断，不会直接改写当前 Gold。新的 revision 必须经过证据、检索合理性、分布平衡和逐题审核后，才能由你明确冻结。</p></div>
              <button type="button" class="secondary-button" @click="openEvaluationDiagnostics"><GitCompare :size="15" />查看评测诊断</button>
            </div>
            <div class="dataset-review-gates" role="list" aria-label="Gold 发布前自动门禁">
              <article v-for="gate in candidateGateSummary" :key="gate.key" class="dataset-review-gate-card" :class="gate.state" role="listitem">
                <div class="dataset-review-gate-card-top"><strong>{{ gate.label }}</strong><span class="gate-state-label">{{ gate.state === 'pass' ? '结构通过' : gate.state === 'review' ? '待复核' : '待检测' }}</span></div>
                <span>{{ gate.detail }}</span>
              </article>
            </div>
            <div class="dataset-review-meta" aria-live="polite">
              <span><small>当前 Gold revision</small><code>{{ goldDataset.dataset_revision || '尚未冻结' }}</code></span>
              <span><small>题目数量</small><strong>{{ goldDataset.sample_count || 0 }} 题</strong></span>
              <span><small>绑定索引</small><code>{{ goldDataset.bound_index_version || '尚未绑定' }}</code></span>
            </div>
            <div class="dataset-review-note"><AlertCircle :size="15" /><span>门禁未接入真实自动审查时保持“待检测”，不会因为结构字段存在就自动发布；旧 Gold 与历史 revision 始终保留。</span></div>
          </section>
          <section v-if="candidateGenerationConfigOpen" class="panel review-gate-panel candidate-generation-panel">
            <div class="panel-header review-gate-body"><div class="panel-title"><span class="icon-badge teal"><SlidersHorizontal :size="18" /></span><div><h2>配置自动评测集</h2></div></div><div class="review-gate-copy"><strong>确认后才会开始生成</strong><small>题目只基于当前工作索引生成；不依赖固定 Gold 手工题集。</small></div></div>
            <div class="candidate-generation-note"><strong>当前工作索引</strong><span>{{ ingestionDisplayName(ingestion) }}</span><code>{{ ingestion?.index_version || '--' }}</code></div>
            <div class="candidate-plan-summary"><span>预计生成 <b>{{ candidateRequestedCount }}</b> 道自动评测题</span><span>生成后可直接评测</span><small>当前索引包含 {{ candidateUnitLimit }} 个可用单元；每个单元默认生成 1 道题，重复、证据不完整或质量不足的题目会被筛掉。</small></div>
            <div class="run-options candidate-run-options"><CaInput label="测试题数量" type="number" :model-value="candidateQuestionCount" min="1" :max="candidateUnitLimit" @update:model-value="candidateQuestionCount = Number($event)" /><CaInput label="并行 worker" type="number" min="1" max="4" :model-value="candidateMaxWorkers" @update:model-value="candidateMaxWorkers = Number($event)" /><small>数量不能超过当前索引容量；如需更多题目，请先扩大索引。</small></div>
            <div class="panel-footer"><button class="secondary-button" :disabled="candidateLoading" @click="candidateGenerationConfigOpen = false">返回审核</button><button class="primary-button" :disabled="candidateLoading" @click="startCandidateGeneration"><Play :size="16" />{{ candidateLoading ? '提交中…' : '确认并开始生成' }}</button></div>
          </section>
          <section v-else-if="!candidateDataset" class="panel review-gate-panel">
            <div class="panel-header review-gate-body">
              <div class="panel-title"><span class="icon-badge teal"><FileText :size="18" /></span><div><h2>为当前索引生成评测集</h2></div></div>
              <div class="review-gate-copy"><strong>{{ candidateRun?.status === 'failed' ? '自动生成失败' : candidateActive ? '评测集生成中' : ingestionReady ? '可开始生成' : '等待索引就绪' }}</strong><small>本次将请求 {{ candidateRequestedCount }} 道自动评测题；生成完成后可直接用于当前索引评测，逐题复核是可选的。</small></div>
              <button v-if="!candidateActive" class="primary-button" :disabled="candidateLoading || !ingestionReady" @click="openCandidateGenerationConfig"><Play :size="16" />生成评测集</button>
              <button v-else class="secondary-button danger" :disabled="candidateRun?.status === 'cancelling'" @click="candidateRun && api(`/api/rag_eval/isolated/candidate-runs/${encodeURIComponent(candidateRun.run_id)}/cancel`, { method: 'POST', body: '{}' }).then(() => refreshRun('candidate', candidateRun!.run_id))">取消生成</button>
            </div>
            <div v-if="candidateRun?.status === 'failed'" class="review-feedback error"><AlertCircle :size="15" /><span>生成失败：{{ candidateRun.error || '请查看候选生成审计产物。' }}。修复模型 API 后，可重新打开配置并提交新任务。</span></div>
              <div v-if="candidateRun" class="panel-footer"><span class="status-pill" :class="statusTone(candidateRun.status)">{{ statusLabel(candidateRun.status) }}</span><code>{{ candidateRun.run_id }}</code><button v-if="terminalStatuses.includes(candidateRun.status)" type="button" class="secondary-button danger" :disabled="candidateDeleteLoading" @click="deleteCandidateRun"><Trash2 :size="15" />{{ candidateDeleteLoading ? '删除中…' : '删除本次运行' }}</button></div>
          </section>

          <section v-else class="panel candidate-review-panel">
            <template v-if="candidateReviewPhase === 'intro'">
              <div class="candidate-flow-hero candidate-review-start">
                <div class="candidate-flow-icon"><ShieldCheck :size="28" /></div>
                <p class="eyebrow">REVIEW WORKFLOW</p>
                <h2>自动评测集已生成</h2>
                <p class="candidate-flow-lead">当前评测集包含 {{ candidateReviewCounts.total }} 道题，已经绑定当前索引，可以直接开始评测；逐题复核是可选的质量检查。</p>
                <div class="candidate-start-facts">
                  <span><small>当前索引</small><strong>{{ ingestionDisplayName(ingestion) }}</strong></span>
                  <span><small>候选题数</small><strong>{{ candidateReviewCounts.total }} 题</strong></span>
                  <span><small>已审核</small><strong>{{ candidateReviewCounts.reviewed }} / {{ candidateReviewCounts.total }}</strong></span>
                </div>
                <div class="candidate-start-steps" aria-label="审核流程">
                  <span class="active"><b>01</b><strong>直接评测</strong><small>使用当前索引运行检索与 Ragas</small></span>
                  <span><b>02</b><strong>可选复核</strong><small>检查问题、答案与证据</small></span>
                  <span><b>03</b><strong>内部基准</strong><small>需要时再进入 Gold 治理</small></span>
                </div>
                <div class="candidate-flow-actions">
                  <button class="primary-button" @click="selectNav('evaluation')"><Play :size="16" />直接开始评测</button>
                  <button class="secondary-button" @click="startCandidateReview"><ShieldCheck :size="15" />进行质量复核</button>
                </div>
              </div>
            </template>
            <template v-else-if="candidateReviewPhase === 'complete'">
              <div class="candidate-flow-hero candidate-review-complete">
                <div class="candidate-flow-icon success"><Check :size="28" /></div>
                <p class="eyebrow">REVIEW COMPLETE</p>
                <h2>自动评测集已就绪</h2>
                <p class="candidate-flow-lead">{{ candidateReviewCounts.reviewed }} 道题均已记录复核结论。当前题集仍可直接用于本索引评测，固定 Gold 只在高级治理中使用。</p>
                <div class="review-complete-counts">
                  <span class="approved"><small>已通过</small><strong>{{ candidateReviewCounts.approved }}</strong></span>
                  <span class="needs-revision"><small>待修改</small><strong>{{ candidateReviewCounts.needsRevision }}</strong></span>
                  <span class="rejected"><small>已拒绝</small><strong>{{ candidateReviewCounts.rejected }}</strong></span>
                  <span><small>已审核</small><strong>{{ candidateReviewCounts.reviewed }} / {{ candidateReviewCounts.total }}</strong></span>
                </div>
                <div class="freeze-readiness ready">
                  <strong>当前评测集可直接使用</strong>
                  <span>题集绑定当前索引，不依赖 24 道手工题；逐题复核只影响质量标记。</span>
                </div>
                <div class="candidate-review-jump-panel">
                  <div><strong>需要修改题目？</strong><small>点击题号可直接回到对应题目。</small></div>
                  <div class="candidate-review-jump-grid" aria-label="选择要修改的题目">
                    <button v-for="(sample, index) in candidateDataset.samples" :key="sample.sample_id" type="button" :class="candidateDecisions[sample.sample_id] || 'needs_revision'" :aria-label="`修改第 ${index + 1} 题`" :title="sample.sample_id" @click="openCandidateSample(index)">{{ index + 1 }}</button>
                  </div>
                </div>
                <div class="candidate-flow-actions">
                  <button class="secondary-button" :disabled="candidateLoading" @click="saveCandidateReview"><Check :size="15" />保存复核结果</button>
                  <button v-if="showGoldGovernance" class="primary-button" :disabled="candidateFreezeLoading || candidateApprovalDelta !== 0 || candidateReviewCounts.pending !== 0 || !candidateBoundToSelectedIndex" @click="freezeGoldDataset()"><Database :size="15" />冻结内部 Gold</button>
                  <button class="primary-button" @click="selectNav('evaluation')"><Play :size="15" />开始评测</button>
                </div>
                <p v-if="candidateMessage" class="review-feedback success"><Check :size="15" />{{ candidateMessage }}</p>
                <p v-if="candidateActionError" class="review-feedback error"><AlertCircle :size="15" />{{ candidateActionError }}</p>
              </div>
            </template>
            <template v-else>
            <div class="panel-header candidate-review-header"><div class="panel-title"><span class="icon-badge teal"><FileText :size="18" /></span><div><p class="eyebrow">ONE QUESTION AT A TIME</p><h2>候选题逐题审核</h2></div></div><div class="candidate-position"><strong>第 {{ candidateReviewIndex + 1 }} / {{ candidateDataset.samples.length }} 题</strong><code>{{ currentCandidateSample?.sample_id }}</code></div></div>
            <div class="candidate-review-progress"><span :style="{ width: `${candidateReviewCounts.total ? (candidateReviewCounts.reviewed / candidateReviewCounts.total) * 100 : 0}%` }"></span></div>
            <div v-if="currentCandidateSample" class="candidate-review-layout">
              <article class="candidate-card"><div class="candidate-card-toolbar"><span class="review-status" :class="candidateDecisions[currentCandidateSample.sample_id] || 'needs_revision'">{{ candidateDecisions[currentCandidateSample.sample_id] === 'approved' ? '已通过' : candidateDecisions[currentCandidateSample.sample_id] === 'rejected' ? '已拒绝' : '待修改' }}</span><span>{{ candidateReviewedIds.has(currentCandidateSample.sample_id) ? '已记录结论' : '选择后自动进入下一题；最后一题进入完成页' }}</span></div>
                <div class="decision-buttons"><button type="button" :class="{ selected: candidateDecisions[currentCandidateSample.sample_id] === 'approved' }" @click="setCandidateDecision('approved')"><Check :size="16" />通过</button><button type="button" :class="{ selected: candidateDecisions[currentCandidateSample.sample_id] === 'needs_revision' }" @click="setCandidateDecision('needs_revision')"><RefreshCw :size="16" />待修改</button><button type="button" :class="{ selected: candidateDecisions[currentCandidateSample.sample_id] === 'rejected' }" @click="setCandidateDecision('rejected')"><Trash2 :size="16" />拒绝</button></div>
                <label>问题<textarea v-model="currentCandidateSample.question" rows="3"></textarea></label><label>参考答案<textarea v-model="currentCandidateSample.reference_answer" rows="4"></textarea></label><label>审核备注<textarea v-model="candidateNotes[currentCandidateSample.sample_id]" rows="2" placeholder="记录需要修改的原因或审核说明"></textarea></label>
                <details><summary>查看预期论点（{{ currentCandidateSample.expected_claims?.length || 0 }}）</summary><pre>{{ JSON.stringify(currentCandidateSample.expected_claims || [], null, 2) }}</pre></details><details><summary>查看证据定位（{{ currentCandidateSample.gold_evidence?.length || 0 }}）</summary><pre>{{ JSON.stringify(currentCandidateSample.gold_evidence || [], null, 2) }}</pre></details>
                <div class="candidate-navigation"><button class="secondary-button" :disabled="candidateReviewIndex === 0" @click="candidateReviewIndex -= 1"><ArrowLeft :size="15" />上一题</button><button class="secondary-button" :disabled="candidateReviewIndex >= candidateDataset.samples.length - 1" @click="candidateReviewIndex += 1">下一题<ArrowRight :size="15" /></button></div>
              </article>
              <aside class="candidate-review-summary"><h3>质量复核摘要</h3><div class="review-count-grid"><span><small>已通过</small><strong>{{ candidateReviewCounts.approved }}</strong></span><span><small>待修改</small><strong>{{ candidateReviewCounts.needsRevision }}</strong></span><span><small>已拒绝</small><strong>{{ candidateReviewCounts.rejected }}</strong></span><span><small>待审核</small><strong>{{ candidateReviewCounts.pending }}</strong></span><span><small>评测集总数</small><strong>{{ candidateReviewCounts.total }}</strong></span></div><div class="freeze-readiness ready"><strong>当前题集已绑定索引</strong><span>逐题复核是可选质量检查，不影响直接运行本次评测。</span></div><div class="generation-coverage"><strong>生成覆盖（只读）</strong><span>选中单元 {{ candidateAudit?.coverage?.selected_unit_count ?? '--' }} · 覆盖单元 {{ candidateAudit?.coverage?.covered_unit_count ?? '--' }} · 含证据题目 {{ candidateAudit?.coverage?.samples_with_evidence ?? '--' }}</span></div><label>审核人<input v-model="reviewerName" type="text" maxlength="120" placeholder="可选" /></label></aside>
            </div>
          <div class="review-action-bar"><div><strong>当前题集已绑定所选索引</strong><span>如切换工作索引，请重新生成评测集；历史运行和复核记录不会被删除。</span></div><div class="review-gate-actions"><button class="secondary-button" :disabled="candidateLoading || !ingestionReady" :title="`重绑到 ${ingestionDisplayName(ingestion)}`" @click="rebindCandidateToCurrentIndex"><RefreshCw :size="15" />重绑并复核</button><button class="secondary-button" :disabled="candidateLoading || !ingestionReady" :title="`配置并基于 ${ingestionDisplayName(ingestion)} 生成新的评测集`" @click="openCandidateGenerationConfig"><RefreshCw :size="15" />重新生成评测集</button><button class="secondary-button" :disabled="candidateLoading" @click="saveCandidateReview"><Check :size="15" />保存复核结果</button><button v-if="showGoldGovernance" class="primary-button" :disabled="candidateFreezeLoading || candidateApprovalDelta !== 0 || candidateReviewCounts.pending !== 0 || !candidateBoundToSelectedIndex" @click="freezeGoldDataset()"><Database :size="15" />冻结内部 Gold</button><button v-if="showGoldGovernance" class="secondary-button" :disabled="!canBindProductionBaseline" title="仅用于绑定已发布的正式 active 索引；不会绑定当前 staged 索引" @click="bindEvaluationBaseline"><Gauge :size="15" />绑定正式生产基准</button></div><p v-if="!candidateBoundToSelectedIndex" class="review-feedback error"><AlertCircle :size="15" />当前评测集绑定 <code>{{ candidateRun?.index_version || '--' }}</code>，而所选索引为 <code>{{ ingestion?.index_version || '--' }}</code>；请重新生成当前索引评测集。</p><p v-if="showGoldGovernance && goldDataset.exists && !canBindProductionBaseline" class="review-feedback"><AlertCircle :size="15" />正式 active 索引：{{ indexReferenceLabel(goldDataset.production_index_version) }}；当前已冻结 Gold：{{ indexReferenceLabel(goldDataset.bound_index_version) }}。</p><p v-if="candidateMessage" class="review-feedback success"><Check :size="15" />{{ candidateMessage }}</p><p v-if="candidateActionError" class="review-feedback error"><AlertCircle :size="15" />{{ candidateActionError }}</p></div>
            </template>
          </section>
          <div v-if="goldReplaceDialogOpen" class="gold-replace-backdrop" role="presentation"><section class="gold-replace-dialog" role="dialog" aria-modal="true" aria-labelledby="gold-replace-title"><p class="eyebrow">EXISTING GOLD DATASET</p><h2 id="gold-replace-title">替换当前 Gold 基准集？</h2><p>当前本地 Gold 已存在。确认替换后，旧基准会先归档到本地 history，再冻结当前审核结果；不会直接丢失。</p><div class="review-gate-actions"><button class="secondary-button" @click="goldReplaceDialogOpen = false">保留当前基准</button><button class="primary-button" :disabled="candidateFreezeLoading" @click="freezeGoldDataset(true)">确认替换并冻结</button></div></section></div>
        </template>

        <template v-else-if="activeNav === 'evaluation'">
          <section v-if="evaluationSection === 'config' && configData" class="panel strategy-profile-panel">
            <div class="strategy-profile-controls">
              <label>策略 profile<select v-model="strategyProfileId"><option v-for="profile in strategyProfiles" :key="profile.profile_id" :value="profile.profile_id">{{ profile.name }}{{ profile.kind === 'builtin' ? '（内置只读）' : '（自定义）' }}</option></select></label>
              <label>profile 名称<input v-model="profileNameDraft" type="text" maxlength="120" /></label>
              <div class="strategy-profile-actions">
                <button class="secondary-button" :disabled="configSaving" @click="saveAsProfile">另存为自定义</button>
                <button class="secondary-button" :disabled="configSaving || selectedStrategyProfile()?.editable !== true" @click="deleteProfile">删除</button>
              </div>
            </div>
            <small class="strategy-profile-hint">内置 profile 不可直接修改；保存自定义 profile 后，只有显式发布才会切换正式默认配置。当前正式：{{ strategyProfiles.find((profile) => profile.profile_id === publishedProfileId)?.name || 'active_current' }}</small>
            <label class="switch-row parallel-evaluation-switch"><input v-model="parallelEvaluationEnabled" type="checkbox" /><span class="switch-control"></span><span><strong>并行运行多个策略实验</strong><small>一次选择 2–4 个已保存 profile。后端会分别创建独立 run，并由评测 worker 的多个 slot 并行领取；每个 run 的题集、配置和报告仍完全隔离。</small></span></label>
            <div v-if="parallelEvaluationEnabled" class="parallel-profile-grid">
              <label v-for="profile in strategyProfiles" :key="`parallel-${profile.profile_id}`" :class="{ selected: parallelProfileIds.includes(profile.profile_id), disabled: !parallelProfileIds.includes(profile.profile_id) && parallelProfileIds.length >= 4 }"><input type="checkbox" :checked="parallelProfileIds.includes(profile.profile_id)" :disabled="!parallelProfileIds.includes(profile.profile_id) && parallelProfileIds.length >= 4" @change="toggleParallelProfile(profile.profile_id)" /><span><strong>{{ profile.name }}</strong><small>{{ profile.retrieval_profile }} · {{ profile.ragas_profile }}</small></span></label>
              <small class="parallel-profile-hint">已选择 {{ parallelProfileIds.length }}/4。当前正在编辑的 profile 会使用页面中的未保存草稿；其他 profile 使用各自已保存快照。</small>
            </div>
          </section>
          <div class="evaluation-intro"><div><p class="kicker">评测中心</p><h2>{{ evaluationSection === 'config' ? '配置并启动评测' : evaluationSection === 'events' ? '跟踪本次评测的每个阶段' : '比较不同时间、知识源与策略运行' }}</h2><p>每次运行绑定所选索引和题集快照，独立产出报告产物。</p></div><span v-if="evaluationRun" class="status-pill" :class="statusTone(evaluationRun.status)">{{ statusLabel(evaluationRun.status) }}</span></div>
          <div v-if="actionError || configError || comparisonError" class="alert danger-alert" role="alert"><AlertCircle :size="17" /><span>{{ actionError || configError || comparisonError }}</span></div>
          <div v-if="configMessage" class="alert success-alert"><Check :size="17" /><span>{{ configMessage }}</span></div>

          <template v-if="evaluationSection === 'config'">
            <div class="ai-waiting-notice"><AlertCircle :size="17" /><span>{{ executeRagas ? '本次流程包含回答生成和 Ragas judge，涉及大模型；开始后请耐心等待，完成时会弹窗提示。' : '当前为只运行 retrieval 模式，不调用回答生成模型或 Ragas judge。' }}</span></div>
            <section class="config-layout">
              <article class="panel evaluation-config-panel">
                <div class="panel-header"><div class="panel-title"><span class="icon-badge teal"><SlidersHorizontal :size="18" /></span><div><h2>当前评测配置</h2></div></div><button class="secondary-button" :disabled="configLoading" @click="loadConfig"><RefreshCw :size="15" :class="{ spinning: configLoading }" />重新加载</button></div>
                 <div v-if="configLoading && !configData" class="empty-line"><CaLoadingState variant="line" label="加载评测配置" /></div>
                <div v-else-if="configData" class="config-body">
                  <div class="config-selectors"><label>检索 profile<select v-model="retrievalProfile"><option v-for="profile in Object.keys(configData.retrieval_profiles)" :key="profile" :value="profile">{{ profile }}</option></select></label><label>Ragas profile<select v-model="ragasProfile"><option v-for="profile in configData.ragas.available_profiles" :key="profile" :value="profile">{{ profile }}</option></select></label></div>
                  <div class="config-section-heading"><strong>检索参数</strong><small>鼠标悬停参数说明可查看建议范围和硬限制</small></div>
                  <div class="config-fields"><label v-for="field in retrievalFieldKeys" :key="`retrieval-${field}`" class="config-field"><span class="field-title"><span>{{ configMeta(field).label }}</span><span class="config-help" :title="configTooltip(field)">?</span></span><input :value="draftDisplay(retrievalDraft, field)" type="number" :step="configMeta(field).integer ? 1 : 0.01" @input="updateDraft(retrievalDraft, field, $event)" /><small>建议 {{ formatRange(configMeta(field).recommended) }} · 硬限制 {{ formatRange(configMeta(field).allowed) }}</small></label><label class="config-field config-select-field"><span class="field-title"><span>正式回答 evidence 压缩</span><span class="config-help" title="正式回答和 Ragas judge 会复用压缩后的 evidence；page_dedupe 会按文档、物理页和内容类型去重。">?</span></span><select v-model="retrievalDraft.answer_context_compression"><option v-for="option in answerCompressionOptions" :key="option" :value="option">{{ option === 'none' ? '不压缩（P0）' : '按文档/页/内容类型去重（P2）' }}</option></select><small>该字段与“正式回答最大上下文数”共同控制正式回答 evidence；Ragas judge 会复用同一结果</small></label></div>
                  <div class="config-section-heading"><strong>Ragas judge 参数</strong><small>超出硬限制会被后端拒绝，建议范围外会提示复核</small></div>
                  <div class="config-fields"><label v-for="field in ragasFieldKeys" :key="`ragas-${field}`" class="config-field"><span class="field-title"><span>{{ configMeta(field).label }}</span><span class="config-help" :title="configTooltip(field)">?</span></span><input :value="draftDisplay(ragasDraft, field)" type="number" :step="configMeta(field).integer ? 1 : 0.01" @input="updateDraft(ragasDraft, field, $event)" /><small>建议 {{ formatRange(configMeta(field).recommended) }} · 硬限制 {{ formatRange(configMeta(field).allowed) }}</small></label></div>
                  <div class="metric-selector"><strong>Ragas 指标</strong><label v-for="metric in metricOptions" :key="metric"><input type="checkbox" :checked="isMetricSelected(metric)" @change="toggleMetric(metric)" />{{ metric }}</label></div>
                   <label class="switch-row"><input v-model="executeRagas" type="checkbox" /><span class="switch-control"></span><span><strong>执行完整 Ragas 评测</strong><small>{{ executeRagas ? '开启后会生成当前检索策略的回答和上下文，再调用 Ragas judge；涉及大模型，请耐心等待。' : '关闭后只运行 retrieval，不调用回答生成模型或 Ragas judge。' }}</small></span></label>
                  <div class="panel-footer"><button class="primary-button" :disabled="configSaving" @click="saveConfig"><Check :size="16" />保存评测配置</button><button class="secondary-button" :disabled="configSaving" @click="publishConfig"><FileChartColumn :size="16" />发布检索策略配置</button></div>
                </div>
              </article>
              <aside class="panel production-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge gold"><Database :size="18" /></span><div><h2>当前正式配置</h2></div></div><span class="status-pill" :class="productionConfig?.exists ? 'success' : 'muted'">{{ productionConfig?.exists ? '已存在' : '未发布' }}</span></div><div class="production-body"><p>这里展示正式 RAG 当前读取的检索参数。只有显式点击发布后才会更新正式配置文件。</p><pre v-if="productionConfig?.retrieval_config">{{ JSON.stringify(productionConfig.retrieval_config, null, 2) }}</pre><div v-else class="empty-line">暂无正式配置</div><div class="production-meta" v-if="productionConfig?.metadata"><span>最近发布</span><code>{{ String(productionConfig.metadata.published_at || '--') }}</code></div></div></aside>
            </section>
             <section class="panel dataset-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge coral"><Database :size="18" /></span><div><p class="eyebrow">INDEX-BOUND DATASET</p><h2>当前索引的自动评测集</h2></div></div><span class="status-pill" :class="evaluationDatasetReady ? 'success' : 'muted'">{{ evaluationDatasetReady ? '可开始评测' : '等待生成' }}</span></div><div class="dataset-body"><p>评测题集由当前索引自动生成，默认不依赖手工 Gold；切换索引后需要重新生成，以保证题目、证据定位和索引版本一致。</p><div class="gold-dataset-summary" :class="{ empty: !evaluationDatasetReady }"><template v-if="evaluationDatasetReady"><span><small>数据集</small><strong>{{ evaluationDatasetLabel }}</strong><code>{{ candidateDataset?.dataset_id || 'generated_candidate' }}</code></span><span><small>绑定索引</small><strong>{{ indexReferenceLabel(ingestion?.index_version || '') }}</strong><code>{{ ingestion?.index_version || '--' }}</code></span><span><small>样本数</small><strong>{{ candidateDataset?.samples.length }}</strong></span></template><span v-else>请先在“自动生成评测集”中指定题目数量并生成题集。</span></div></div><div class="bound-index" :class="{ incompatible: ingestionReady && !evaluationDatasetReady }"><span>当前工作索引</span><strong>{{ ingestionDisplayName(ingestion) }}</strong><code>{{ ingestion?.index_version || '--' }}</code><span class="bound-check"><Check v-if="evaluationDatasetReady" :size="13" /><AlertCircle v-else :size="13" />{{ evaluationDatasetReady ? '题集已绑定当前索引' : '请先生成当前索引的自动评测集' }}</span></div><div class="panel-footer"><button class="primary-button" :disabled="evaluationLoading || !ingestionReady || !evaluationDatasetReady || (parallelEvaluationEnabled && (parallelProfileIds.length < 2 || parallelProfileIds.length > 4))" @click="startEvaluation"><Play :size="16" />{{ parallelEvaluationEnabled ? `并行启动 ${parallelProfileIds.length} 个实验` : executeRagas ? '开始完整评测' : '开始只运行检索' }}</button><button v-if="parallelEvaluationActive || (evaluationRun && ['created','queued','running','cancelling'].includes(evaluationRun.status))" class="secondary-button danger" :disabled="evaluationRun?.status === 'cancelling'" @click="cancelEvaluation">{{ parallelEvaluationActive ? '取消全部并行实验' : '取消' }}</button></div></section>
          </template>

           <template v-else-if="evaluationSection === 'events'"><div v-if="evaluationActive || parallelEvaluationActive" class="ai-waiting-notice"><CaLoadingState variant="line" label="评测进行中" /><span>{{ parallelEvaluationActive ? `并行实验进行中，已完成 ${parallelCompletedCount}/${evaluationBatchRuns.length}；各 run 会独立生成报告。` : evaluationWaitingMessage }}</span></div><section v-if="evaluationBatchRuns.length" class="panel parallel-run-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge teal"><GitCompare :size="18" /></span><div><p class="eyebrow">PARALLEL EXPERIMENTS</p><h2>并行实验状态</h2></div></div><span class="count-label">{{ parallelCompletedCount }} / {{ evaluationBatchRuns.length }} 完成</span></div><div class="parallel-run-grid"><button v-for="run in evaluationBatchRuns" :key="run.run_id" :class="{ selected: evaluationRun?.run_id === run.run_id }" @click="focusBatchRun(run)"><span><strong>{{ profileName(run.strategy_profile?.profile_id) }}</strong><small>{{ run.run_id }}</small></span><span class="status-pill" :class="statusTone(run.status)">{{ statusLabel(run.status) }}</span></button></div></section><section class="panel evaluation-progress-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge teal"><Gauge :size="18" /></span><div><h2>评测阶段进度</h2></div></div><span class="count-label">{{ evaluationCompletedStages }} / {{ evaluationProgressRows.length }} 阶段完成</span></div><div class="progress-stage-grid"><article v-for="(row, index) in evaluationProgressRows" :key="row.step" class="progress-stage-card" :class="row.status"><div class="progress-stage-heading"><span class="progress-stage-index">{{ String(index + 1).padStart(2, '0') }}</span><div><strong>{{ evaluationStageLabel(row.step) }}</strong><small>{{ evaluationProgressStatusLabel(row.status) }}</small></div></div><div class="progress-stage-track"><span :style="{ width: `${evaluationProgressPercent(row.current, row.total, row.status)}%` }"></span></div><div class="progress-stage-meta"><span v-if="row.phase">{{ evaluationPhaseLabel(row.phase) }}</span><strong v-if="row.current !== undefined && row.total !== undefined">{{ row.current }} / {{ row.total }}</strong><span v-else>{{ row.status === 'done' ? '阶段已完成' : row.status === 'pending' ? '等待前序阶段' : '等待事件更新' }}</span></div><div v-if="row.substeps?.length" class="progress-substeps"><span v-for="substep in row.substeps" :key="substep.phase"><small>{{ evaluationPhaseLabel(substep.phase) }}</small><b v-if="substep.current !== undefined && substep.total !== undefined">{{ substep.current }} / {{ substep.total }}</b></span></div></article></div></section><section class="panel evaluation-events-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge slate"><CircleDot :size="18" /></span><div><h2>评测流程事件</h2></div></div><span class="count-label">{{ displayEvents.length }} 条（重复进度已合并）</span></div><div class="event-list evaluation-event-list"><div v-if="!displayEvents.length" class="empty-line">等待评测中心启动任务</div><div v-for="(event, index) in displayEvents" :key="`${event.timestamp}-${index}`" class="event-row"><span class="event-time">{{ formatBeijingTime(event.timestamp) }}</span><span class="event-type">{{ event.type }}</span><span>{{ event.message }}</span></div></div></section><div class="event-actions"><button class="secondary-button" @click="selectEvaluationSection('config')"><SlidersHorizontal :size="15" />返回评测配置</button><button class="secondary-button" :disabled="!evaluationRun" @click="evaluationRun && openReport(evaluationRun.run_id)"><FileChartColumn :size="15" />编辑当前报告</button><button v-if="evaluationBatchRuns.length && !parallelEvaluationActive" class="primary-button" @click="selectEvaluationSection('comparison')"><GitCompare :size="15" />对比实验结果</button></div></template>

          <template v-else><section class="comparison-layout"><article class="panel comparison-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge teal"><GitCompare :size="18" /></span><div><h2>时间趋势与运行对比</h2></div></div><span class="count-label">隔离数据</span></div><div class="comparison-controls"><label>时间跨度<select v-model="historyRange"><option value="7d">近 7 天</option><option value="30d">近 30 天</option><option value="90d">近 90 天</option><option value="all">全部历史</option></select></label><label>知识源<select v-model="historySource"><option value="">全部知识源</option><option v-for="source in sourceFilterOptions" :key="source" :value="source">{{ source }}</option></select></label><label>粒度<select v-model="comparisonGranularity"><option value="day">按天</option><option value="run">按运行</option></select></label><button class="secondary-button" @click="refreshComparison"><RefreshCw :size="15" />刷新</button></div><div class="comparison-mode-grid"><button class="comparison-mode" :class="{ selected: comparisonMode === 'time_trend' }" @click="comparisonMode = 'time_trend'"><TrendingUp :size="18" /><span>时间趋势</span><small>观察指标随时间变化</small></button><button class="comparison-mode" :class="{ selected: comparisonMode === 'run_diff' }" @click="comparisonMode = 'run_diff'"><GitCompare :size="18" /><span>运行 A/B</span><small>比较基线与候选 run</small></button><button class="comparison-mode" :class="{ selected: comparisonMode === 'strategy_diff' }" @click="comparisonMode = 'strategy_diff'"><SlidersHorizontal :size="18" /><span>策略对比</span><small>比较检索与 Ragas 配置</small></button></div><div v-if="historyLoading || diffLoading" class="comparison-empty"><LoaderCircle class="spin" :size="24" />正在读取隔离评测</div><template v-else-if="comparisonMode === 'time_trend'"><div v-if="!trendRows.length" class="comparison-empty"><TrendingUp :size="24" /><strong>暂无可比较的评测</strong><p>完成一次完整评测后，这里会展示真实历史趋势。</p></div><div v-else class="comparison-table-wrap"><table class="comparison-table"><thead><tr><th>时间</th><th>运行数</th><th v-for="metric in historyMetricKeys" :key="metric">{{ metricLabel(metric) }}</th></tr></thead><tbody><tr v-for="row in trendRows" :key="row.label"><td class="mono">{{ row.label }}</td><td>{{ row.count }}</td><td v-for="metric in historyMetricKeys" :key="metric">{{ formatMetric(row.metrics[metric]) }}</td></tr></tbody></table></div></template><template v-else><div class="diff-controls"><label>基线<select v-model="diffBaseRunId"><option v-for="run in historyRuns" :key="`base-${run.run_id}`" :value="run.run_id">{{ historyLabel(run) }}</option></select></label><label>候选<select v-model="diffCandidateRunId"><option v-for="run in historyRuns" :key="`candidate-${run.run_id}`" :value="run.run_id">{{ historyLabel(run) }}</option></select></label><button class="secondary-button" @click="loadEvaluationDiff"><GitCompare :size="15" />加载对比</button></div><div v-if="!diffResult" class="comparison-empty"><GitCompare :size="24" /><strong>选择两个隔离评测运行</strong><p>运行 A/B 与策略对比要求题集 identity 一致；跨题集比较会被后端拒绝。</p></div><template v-else><div class="comparison-table-wrap metric-comparison-wrap"><table class="comparison-table metric-comparison-table"><thead><tr><th>指标</th><th>基线</th><th>候选</th><th>变化</th></tr></thead><tbody><tr v-for="metric in diffResult.metric_deltas" :key="metric.metric"><th scope="row">{{ metricLabel(metric.metric) }}</th><td>{{ formatMetric(metric.base) }}</td><td>{{ formatMetric(metric.candidate) }}</td><td><span class="metric-change" :class="metricDeltaTone(metric.delta)" :title="metricDeltaLabel(metric.delta)"><ArrowUp v-if="metric.delta !== null && metric.delta > 0" :size="15" aria-hidden="true" /><ArrowDown v-else-if="metric.delta !== null && metric.delta < 0" :size="15" aria-hidden="true" /><Minus v-else :size="15" aria-hidden="true" /><span>{{ formatMetric(metric.delta) }}</span></span></td></tr></tbody></table></div><div v-if="diffResult.config_deltas.length" class="config-diff-section"><div class="diff-section-heading"><div><h3>配置差异</h3><p>以下差异来自两次 run 的独立配置快照。</p></div><span>{{ diffResult.config_deltas.length }} 项</span></div><div class="comparison-table-wrap config-diff-wrap"><table class="comparison-table config-diff-table"><thead><tr><th>配置项</th><th>基线</th><th>候选</th></tr></thead><tbody><tr v-for="item in diffResult.config_deltas" :key="item.field"><th scope="row">{{ configFieldLabel(item.field) }}<small class="config-field-path">{{ item.field }}</small></th><td><code>{{ formatConfigValue(item.base) }}</code></td><td><code>{{ formatConfigValue(item.candidate) }}</code></td></tr></tbody></table></div></div><div class="diff-summary"><span>样本 {{ diffResult.summary.sample_count }}</span><span>整体改善 {{ diffResult.summary.improved_count }}</span><span>整体退化 {{ diffResult.summary.regressed_count }}</span><span title="持续坏例可与整体改善、整体退化和指标分歧重叠">持续坏例 {{ diffResult.summary.persistent_bad_case_count }} <small>可重叠</small></span></div><div class="comparison-legend" aria-label="指标变化图例"><span class="positive"><ArrowUp :size="14" />改善</span><span class="negative"><ArrowDown :size="14" />退化</span><span class="flat"><Minus :size="14" />持平</span><span class="unscored">未评分</span><small>数值越高越好；持续坏例可与样本结论重叠</small></div><div class="sample-diff-toolbar"><label>样本筛选<select v-model="sampleFilter"><option value="all">全部样本</option><option value="regressed">整体退化</option><option value="mixed">指标分歧</option><option value="improved">整体改善</option><option value="unchanged">基本持平</option><option value="unscored">未评分</option></select></label><label>排序<select v-model="sampleSort"><option value="default">按样本编号</option><option value="classification">退化优先</option><option value="largest_drop">最大下降优先</option></select></label><span class="sample-count-label">显示 {{ visibleSampleDeltas.length }} / {{ diffResult.sample_deltas.length }} 条</span></div><div class="comparison-table-wrap sample-diff-wrap"><table class="comparison-table sample-diff-table"><thead><tr><th>样本编号</th><th>对比结论</th><th v-for="metric in sampleMetricKeys" :key="metric">{{ metricLabel(metric) }}<small>基线 / 候选 / 变化</small></th></tr></thead><tbody><tr v-for="sample in visibleSampleDeltas" :key="sample.sample_id"><td class="mono" :title="sample.question">{{ sample.sample_id }}</td><td><span class="classification-badge" :class="sampleClassificationTone(sample.classification)">{{ sampleClassificationLabel(sample.classification) }}</span></td><td v-for="metric in sampleMetricKeys" :key="metric" class="sample-metric-cell"><template v-if="sampleMetric(sample, metric)"><div class="sample-metric"><div class="sample-metric-line"><small>基线</small><span>{{ formatMetric(sampleMetric(sample, metric)?.base) }}</span></div><div class="sample-metric-line"><small>候选</small><span :class="['sample-metric-candidate', metricDeltaTone(sampleMetric(sample, metric)?.delta ?? null)]">{{ formatMetric(sampleMetric(sample, metric)?.candidate) }} <span class="sample-metric-delta"><ArrowUp v-if="(sampleMetric(sample, metric)?.delta ?? 0) > 0" :size="13" /><ArrowDown v-else-if="(sampleMetric(sample, metric)?.delta ?? 0) < 0" :size="13" /><Minus v-else :size="13" />{{ formatMetric(sampleMetric(sample, metric)?.delta ?? null) }}</span></span></div></div></template><span v-else class="unscored">未评分</span></td></tr><tr v-if="!visibleSampleDeltas.length"><td class="sample-empty" :colspan="sampleMetricKeys.length + 2">没有符合条件的样本</td></tr></tbody></table></div></template></template></article><aside class="panel quick-actions-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge gold"><Gauge :size="18" /></span><div><p class="eyebrow">快捷操作流程</p><h2>快速处理本次对比</h2></div></div></div><div class="quick-action-list"><button class="quick-action" @click="selectEvaluationSection('config')"><span class="quick-step">01</span><span><strong>加载并修改配置</strong><small>先确认当前检索与 Ragas 参数</small></span><ChevronDown :size="16" /></button><button class="quick-action" @click="selectEvaluationSection('events')"><span class="quick-step">02</span><span><strong>核对执行事件</strong><small>确认评测没有被取消或卡住</small></span><ChevronDown :size="16" /></button><button class="quick-action" :disabled="!evaluationRun" @click="evaluationRun && openReport(evaluationRun.run_id)"><span class="quick-step">03</span><span><strong>编辑本次报告</strong><small>在报告编辑中切换三类 Markdown</small></span><ChevronDown :size="16" /></button><button class="quick-action" @click="selectNav('workspace')"><span class="quick-step">04</span><span><strong>更换知识源</strong><small>回到工作台创建新的隔离索引</small></span><ChevronDown :size="16" /></button></div></aside></section></template>
        </template>

        <template v-else-if="activeNav === 'release'">
          <div class="release-intro"><div><h2>正式 active pointer 发布</h2><p>只发布经过来源身份、索引完整性、自动 Ragas 和正式检索门禁的候选版本；不会发布检索策略，也不会自动删除旧索引。</p></div><span class="status-pill" :class="releasePublishable ? 'success' : releaseStatus?.state === 'blocked' ? 'danger' : 'muted'">{{ releasePublishable ? '可发布' : releaseStatus?.state === 'blocked' ? '门禁阻断' : '待检查' }}</span></div>
          <div v-if="releaseError" class="alert danger-alert"><AlertCircle :size="17" /><span>{{ releaseError }}</span></div>
          <div v-if="releaseNotice" class="alert success-alert"><Check :size="17" /><span>{{ releaseNotice }}</span></div>
          <section class="release-summary-grid">
            <article class="panel release-candidate-card"><div class="panel-header"><div class="panel-title"><span class="icon-badge teal"><ShieldCheck :size="18" /></span><div><h2>{{ releaseStatus?.release?.index_version || ingestion?.index_version || '未选择候选' }}</h2></div></div><span class="status-pill" :class="ingestionReady ? 'active' : 'muted'">{{ ingestionReady ? '隔离 staged' : '未就绪' }}</span></div><div class="release-facts"><span><small>摄取运行</small><code>{{ releaseStatus?.release?.ingestion_run_id || ingestion?.run_id || '--' }}</code></span><span><small>manifest SHA-256</small><code>{{ releaseStatus?.release?.manifest_sha256?.slice(0, 16) || '--' }}{{ releaseStatus?.release?.manifest_sha256 ? '…' : '' }}</code></span><span><small>自动评测</small><code>{{ evaluationRun?.run_id || '--' }}</code></span></div></article>
            <article class="panel release-pointer-card"><div class="panel-header"><div class="panel-title"><span class="icon-badge gold"><Database :size="18" /></span><div><h2>正式运行版本</h2></div></div><button v-if="releaseStatus?.previous?.index_version" type="button" class="secondary-button" :disabled="releaseRollbackLoading" @click="rollbackRelease"><RefreshCw :size="15" />{{ releaseRollbackLoading ? '回滚中…' : '回滚 previous' }}</button></div><div class="pointer-facts"><span><small>active</small><strong>{{ String(releaseStatus?.active?.index_version || '--') }}</strong></span><span><small>previous</small><strong>{{ String(releaseStatus?.previous?.index_version || '--') }}</strong></span><span><small>生效方式</small><strong>worker 重启</strong></span></div></article>
          </section>
          <section class="release-layout">
            <article class="panel release-gate-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge coral"><ShieldCheck :size="18" /></span><div><h2>正式发布门禁</h2></div></div><button class="secondary-button" :disabled="releaseLoading || !ingestionReady" @click="checkReleaseGate"><RefreshCw :size="15" :class="{ spinning: releaseLoading }" />{{ releaseLoading ? '检查中…' : '重新检查门禁' }}</button></div><div v-if="!releaseStatus?.checks?.length" class="release-empty"><ShieldCheck :size="24" /><strong>尚未执行发布门禁</strong><span>选择已完成的 staged 索引并完成自动 Ragas 评测后，点击重新检查。</span></div><div v-else class="release-check-list" role="list" aria-label="正式发布门禁清单"><article v-for="check in releaseStatus.checks" :key="check.key" class="release-check-row" :class="check.status" role="listitem"><span class="release-check-icon"><Check v-if="check.status === 'pass'" :size="15" /><AlertCircle v-else :size="15" /></span><div><strong>{{ check.label }}</strong><small>{{ check.detail }}</small></div><span class="status-pill" :class="check.status === 'pass' ? 'success' : 'danger'">{{ check.status === 'pass' ? '通过' : '阻断' }}</span></article></div></article>
            <aside class="panel release-source-panel"><div class="panel-header"><div class="panel-title"><span class="icon-badge slate"><FileText :size="18" /></span><div><h2>正式来源快照</h2></div></div><span class="count-label">{{ releaseStatus?.release?.source_count || 0 }} 个</span></div><div v-if="!releaseStatus?.release?.sources?.length" class="empty-line">执行门禁后显示来源身份</div><div v-else class="release-source-list"><div v-for="source in releaseStatus.release.sources" :key="`${source.source_id}-${source.document_id}`" class="release-source-row"><strong>{{ source.relative_path || source.source_id }}</strong><small>{{ source.source_id }} · {{ source.document_id }}</small><code>{{ source.content_hash?.slice(0, 16) || '--' }}…</code></div></div><p class="release-source-note">正式发布要求哈希在受控 source 目录内唯一命中；显示名不会参与正式身份判断。</p></aside>
          </section>
          <div class="release-action-bar"><div><strong>{{ releasePublishable ? '所有必需门禁已通过' : '发布按钮保持锁定' }}</strong><span>{{ releasePublishable ? '点击后后端仍会重新检查全部门禁，并原子切换 active pointer。' : '门禁通过前不能发布；Gold 当前不是必要条件。' }}</span></div><div class="release-action-buttons"><button class="secondary-button" @click="loadReleaseStatus"><RefreshCw :size="15" />刷新状态</button><button class="primary-button" :disabled="!releasePublishable || releasePublishing" @click="openReleaseConfirmation"><ArrowUp :size="16" />{{ releasePublishing ? '发布中…' : '发布正式 active pointer' }}</button></div></div>
          <div v-if="releaseConfirmOpen" class="gold-replace-backdrop" role="presentation"><section class="gold-replace-dialog release-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="release-confirm-title"><p class="eyebrow">EXPLICIT POINTER PUBLISH</p><h2 id="release-confirm-title">确认发布正式 active pointer？</h2><p>将把 active 从 <code>{{ String(releaseStatus?.active?.index_version || '--') }}</code> 切换到 <code>{{ releaseStatus?.release?.index_version }}</code>。当前 active 会保存为 previous，不会自动删除旧索引；运行中的 worker 需要 drain/restart 后才会生效。</p><div class="review-gate-actions"><button class="secondary-button" :disabled="releasePublishing" @click="releaseConfirmOpen = false">取消</button><button class="primary-button" :disabled="releasePublishing" @click="publishRelease"><ArrowUp :size="15" />{{ releasePublishing ? '发布中…' : '确认发布' }}</button></div></section></div>
        </template>

        <template v-else>
           <div class="evaluation-intro"><div><h2>按时间、知识源和运行编辑 Markdown 报告</h2><p>报告始终来自选定的隔离 evaluation run，流程、检索和 Ragas 报告在同一页面切换；删除后历史与对比同步移除。</p></div><span v-if="selectedReportRun" class="status-pill" :class="statusTone(selectedReportRun.status)">{{ statusLabel(selectedReportRun.status) }}</span></div>
           <div v-if="reportError || comparisonError" class="design-error-state"><CaErrorState title="报告或对比读取失败" :description="reportError || comparisonError" /></div>
           <div v-if="reportNotice" class="alert success-alert"><Check :size="17" /><span>{{ reportNotice }}</span></div>
           <section class="report-layout"><aside class="panel report-sidebar"><div class="panel-header"><div class="panel-title"><span class="icon-badge gold"><FileChartColumn :size="18" /></span><div><h2>报告运行</h2></div></div></div><div class="report-filters"><label>时间跨度<select v-model="historyRange"><option value="7d">近 7 天</option><option value="30d">近 30 天</option><option value="90d">近 90 天</option><option value="all">全部历史</option></select></label><label>知识源<select v-model="historySource"><option value="">全部知识源</option><option v-for="source in sourceFilterOptions" :key="source" :value="source">{{ source }}</option></select></label><CaButton variant="secondary" @click="loadEvaluationHistory"><template #icon><RefreshCw :size="15" /></template>刷新历史</CaButton></div><div class="report-run-list"><CaButton v-for="run in historyRuns" :key="run.run_id" variant="quiet" class="report-run-item" :class="{ selected: reportRunId === run.run_id }" :aria-pressed="reportRunId === run.run_id" @click="reportRunId = run.run_id; loadReportArtifact()"><span><strong>{{ runProfileName(run) }}</strong><small>{{ runDatasetSubtitle(run) }} · {{ run.question_count || 0 }} 题</small><small>{{ run.source_label || '知识源未记录' }}</small><small v-if="run.stale" class="stale-hint">长时间无进展，可确认后删除</small></span><span class="status-pill" :class="statusTone(run.status)">{{ statusLabel(run.status) }}</span></CaButton><div v-if="!historyRuns.length" class="empty-line"><CaEmptyState description="暂无符合筛选条件的评测运行"><template #icon><FileText :size="22" /></template></CaEmptyState></div></div></aside><article class="panel report-viewer"><div class="panel-header"><div><p class="eyebrow">{{ currentReportRunLabel }}</p><h2>{{ reportTab === 'pipeline' ? '流程报告' : reportTab === 'retrieval' ? '检索报告' : 'Ragas 报告' }}</h2></div><div class="report-header-actions"><span class="count-label">{{ selectedReportRun?.index_version || '--' }}</span><button class="secondary-button danger" :disabled="!reportDeleteCanProceed || reportDeleteLoading" @click="deleteReport"><Trash2 :size="15" />{{ reportDeleteLoading ? '删除中…' : selectedReportRun?.stale ? '删除卡住任务' : selectedReportRun && ['created', 'running', 'cancelling'].includes(selectedReportRun.status) ? '运行中不可删' : '删除报告' }}</button></div></div><CaTabs class="report-tabs" :model-value="reportTab" :items="reportTabItems" aria-label="报告类型" @update:model-value="selectReportTab($event as ReportTab)" /><div v-if="reportLoading" class="report-empty"><CaLoadingState variant="line" label="正在加载 Markdown 报告" /></div><div v-else-if="!reportMarkdown" class="report-empty"><CaEmptyState title="请选择一个评测运行" description="完成一次隔离评测后，报告会出现在这里。"><template #icon><FileText :size="25" /></template></CaEmptyState></div><div v-else class="markdown-preview" v-html="renderMarkdown(reportMarkdown)"></div></article></section>
        </template>
        <footer class="page-footer"><span>每次摄取生成新的隔离运行目录</span><span>全程隔离，不触碰生产知识库</span><span>索引：{{ ingestion?.index_version || '--' }}</span></footer>
      </main>
    </section>
  </div>
</template>
