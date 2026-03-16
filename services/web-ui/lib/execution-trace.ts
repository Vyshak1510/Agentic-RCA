import { createMockExecutionTrace } from "@/lib/mock-execution-trace";
import {
  AgentToolTrace,
  InvestigationStatus,
  StepAttempt,
  StepExecutionStatus,
  WorkflowRunDetail,
  WorkflowStageId,
} from "@/lib/types";
import { WORKFLOW_STAGES, latestAttempt } from "@/lib/workflow";

export type ExecutionStatus =
  | "idle"
  | "queued"
  | "running"
  | "success"
  | "warning"
  | "failed"
  | "skipped"
  | "retrying"
  | "waiting";

export type ExecutionNodeKind =
  | "trigger"
  | "stage"
  | "tool"
  | "model"
  | "retrieval"
  | "memory"
  | "human"
  | "branch"
  | "evaluation";

export type ExecutionEventKind =
  | "stage"
  | "tool_call"
  | "model_call"
  | "retrieval"
  | "memory_read"
  | "memory_write"
  | "branch"
  | "transform"
  | "retry"
  | "handoff"
  | "wait"
  | "stream"
  | "log"
  | "approval"
  | "publish";

export type ExecutionLogLevel = "debug" | "info" | "warn" | "error";

export type ExecutionMetrics = {
  queueMs?: number;
  executionMs?: number;
  networkMs?: number;
  toolMs?: number;
  modelMs?: number;
  memoryMs?: number;
  tokenInput?: number;
  tokenOutput?: number;
  costUsd?: number;
  cpuPct?: number;
  memoryMb?: number;
  itemCount?: number;
  latencySeries?: number[];
};

export type ExecutionError = {
  summary: string;
  message: string;
  stack?: string[];
  rootCauseHints: string[];
  relatedEventIds?: string[];
};

export type ExecutionLog = {
  id: string;
  timestamp: string;
  level: ExecutionLogLevel;
  message: string;
  source?: string;
};

export type ExecutionGroup = {
  id: string;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  tone: "neutral" | "accent" | "danger";
};

export type ExecutionNode = {
  id: string;
  label: string;
  kind: ExecutionNodeKind;
  state: ExecutionStatus;
  x: number;
  y: number;
  durationMs?: number;
  tokenCount?: number;
  costUsd?: number;
  retryCount?: number;
  itemCount?: number;
  summary: string;
  inputSummary: string;
  outputSummary: string;
  eventIds: string[];
  upstream: string[];
  downstream: string[];
  model?: string;
  tool?: string;
};

export type ExecutionEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
  relation?: "primary" | "fanout" | "model" | "retry" | "publish";
  state: "idle" | "active" | "failed" | "skipped" | "waiting";
  animated?: boolean;
};

export type ExecutionStreamChunk = {
  id: string;
  timestamp: string;
  text: string;
};

export type ExecutionEvent = {
  id: string;
  nodeId: string;
  parentEventId?: string;
  childEventIds: string[];
  title: string;
  description: string;
  kind: ExecutionEventKind;
  status: ExecutionStatus;
  startedAt: string;
  endedAt?: string;
  durationMs: number;
  input?: unknown;
  output?: unknown;
  raw: unknown;
  logs: ExecutionLog[];
  metrics: ExecutionMetrics;
  tags: string[];
  error?: ExecutionError;
  stream?: {
    preview: string;
    truncated?: boolean;
    chunks: ExecutionStreamChunk[];
  };
  model?: string;
  tool?: string;
  retryIndex?: number;
  rootEventId: string;
};

export type ExecutionRunTrace = {
  runId: string;
  previousRunId?: string;
  title: string;
  workflowTitle: string;
  environment: string;
  status: ExecutionStatus;
  startedAt: string;
  endedAt?: string;
  durationMs: number;
  nodes: ExecutionNode[];
  edges: ExecutionEdge[];
  groups: ExecutionGroup[];
  events: ExecutionEvent[];
  currentEventId?: string;
  compare?: {
    durationDeltaMs: number;
    costDeltaUsd: number;
    tokenDelta: number;
    regressionNodeIds: string[];
  };
};

export type TimelineFilter =
  | "errors"
  | "model_calls"
  | "tool_calls"
  | "user_visible"
  | "long_running"
  | "bookmarked";

export type TimelineMode = "grouped" | "flat";

export type TimelineRow = {
  id: string;
  eventId: string;
  depth: number;
  event: ExecutionEvent;
  parentEventId?: string;
  hasChildren: boolean;
  isExpanded: boolean;
  node: ExecutionNode | null;
};

const stageToNodeId: Record<WorkflowStageId, string> = {
  resolve_service_identity: "resolve_service_identity",
  build_investigation_plan: "build_investigation_plan",
  collect_evidence: "collect_evidence",
  synthesize_rca_report: "synthesize_rca_report",
  publish_report: "publish_report",
  emit_eval_event: "emit_eval_event",
};

const stageToEventId: Record<WorkflowStageId, string> = {
  resolve_service_identity: "evt-resolve",
  build_investigation_plan: "evt-plan",
  collect_evidence: "evt-collect",
  synthesize_rca_report: "evt-synthesize",
  publish_report: "evt-publish",
  emit_eval_event: "evt-emit-eval",
};

function mapRunStatus(status: InvestigationStatus): ExecutionStatus {
  switch (status) {
    case "pending":
      return "queued";
    case "running":
      return "running";
    case "completed":
      return "success";
    case "failed":
      return "failed";
    default:
      return "idle";
  }
}

function mapStepStatus(status: StepExecutionStatus): ExecutionStatus {
  switch (status) {
    case "queued":
      return "queued";
    case "running":
      return "running";
    case "completed":
      return "success";
    case "failed":
      return "failed";
    case "skipped":
      return "skipped";
    default:
      return "idle";
  }
}

function collectToolTraces(run: WorkflowRunDetail): AgentToolTrace[] {
  const traces: AgentToolTrace[] = [];
  for (const stage of WORKFLOW_STAGES) {
    const attempts = run.stage_attempts[stage.id] ?? [];
    for (const attempt of attempts) {
      if (Array.isArray(attempt.tool_traces)) {
        traces.push(...attempt.tool_traces);
      }
      const metadataTraces = attempt.metadata?.tool_traces;
      if (Array.isArray(metadataTraces)) {
        traces.push(...(metadataTraces as AgentToolTrace[]));
      }
    }
  }
  return traces;
}

function matchTraceNodeId(trace: AgentToolTrace): string | null {
  const haystack = `${trace.tool_name} ${trace.source}`.toLowerCase();
  if (haystack.includes("prometheus") || haystack.includes("metric")) {
    return "prometheus_query";
  }
  if (haystack.includes("tempo") || haystack.includes("trace")) {
    return "tempo_trace_query";
  }
  if (haystack.includes("context") || haystack.includes("vector") || haystack.includes("retriev")) {
    return "context_retrieval";
  }
  if (haystack.includes("slack")) {
    return "slack_publish";
  }
  if (haystack.includes("jira")) {
    return "jira_publish";
  }
  return null;
}

function summarizeAttempt(attempt: StepAttempt): string {
  if (attempt.stage_reasoning_summary) {
    return attempt.stage_reasoning_summary;
  }
  if (typeof attempt.metadata?.stage_reasoning_summary === "string") {
    return attempt.metadata.stage_reasoning_summary;
  }
  if (attempt.message) {
    return attempt.message;
  }
  if (attempt.error) {
    return attempt.error;
  }
  return "Execution details captured for this stage.";
}

function cloneTrace(trace: ExecutionRunTrace): ExecutionRunTrace {
  return {
    ...trace,
    compare: trace.compare ? { ...trace.compare } : undefined,
    groups: trace.groups.map((group) => ({ ...group })),
    edges: trace.edges.map((edge) => ({ ...edge })),
    nodes: trace.nodes.map((node) => ({ ...node, upstream: [...node.upstream], downstream: [...node.downstream], eventIds: [...node.eventIds] })),
    events: trace.events.map((event) => ({
      ...event,
      childEventIds: [...event.childEventIds],
      logs: event.logs.map((entry) => ({ ...entry })),
      metrics: { ...event.metrics, latencySeries: event.metrics.latencySeries ? [...event.metrics.latencySeries] : undefined },
      error: event.error
        ? {
            ...event.error,
            stack: event.error.stack ? [...event.error.stack] : undefined,
            rootCauseHints: [...event.error.rootCauseHints],
            relatedEventIds: event.error.relatedEventIds ? [...event.error.relatedEventIds] : undefined,
          }
        : undefined,
      stream: event.stream
        ? {
            ...event.stream,
            chunks: event.stream.chunks.map((chunk) => ({ ...chunk })),
          }
        : undefined,
    })),
  };
}

export function buildExecutionTrace(run: WorkflowRunDetail | null, incidentKey: string, investigationId: string): ExecutionRunTrace {
  const baseTrace = cloneTrace(
    createMockExecutionTrace({
      incidentKey,
      investigationId,
      runId: run?.run_id ?? undefined,
    })
  );

  const publishNode = baseTrace.nodes.find((node) => node.id === "publish_report");
  if (publishNode) {
    publishNode.label = "Publish Final RCA";
    publishNode.summary = "Deliver the final RCA to downstream channels and incident surfaces.";
  }
  const publishEvent = baseTrace.events.find((event) => event.id === "evt-publish");
  if (publishEvent) {
    publishEvent.title = "Publish Final RCA";
  }

  if (!run) {
    return baseTrace;
  }

  baseTrace.runId = run.run_id;
  baseTrace.title = `${incidentKey} · ${run.run_id}`;
  baseTrace.status = mapRunStatus(run.status);
  baseTrace.startedAt = run.started_at;
  baseTrace.endedAt = run.ended_at ?? baseTrace.endedAt;
  baseTrace.durationMs = run.duration_ms ?? baseTrace.durationMs;
  baseTrace.currentEventId = run.current_stage ? stageToEventId[run.current_stage] : baseTrace.currentEventId;

  const nodeById = new Map(baseTrace.nodes.map((node) => [node.id, node]));
  const eventById = new Map(baseTrace.events.map((event) => [event.id, event]));

  for (const stage of WORKFLOW_STAGES) {
    const attempt = latestAttempt(run, stage.id);
    const node = nodeById.get(stageToNodeId[stage.id]);
    const event = eventById.get(stageToEventId[stage.id]);
    const attempts = run.stage_attempts[stage.id] ?? [];

    if (!node || !event) {
      continue;
    }

    if (!attempt) {
      if (run.current_stage === stage.id && run.status === "running") {
        node.state = "queued";
        event.status = "queued";
      }
      continue;
    }

    node.state = mapStepStatus(attempt.status);
    node.durationMs = attempt.duration_ms ?? node.durationMs;
    node.retryCount = Math.max(0, attempts.length - 1);
    node.itemCount = Math.max(attempt.logs.length, attempt.citations.length);
    node.summary = summarizeAttempt(attempt);
    node.inputSummary =
      typeof attempt.metadata?.input_summary === "string"
        ? attempt.metadata.input_summary
        : attempt.message ?? node.inputSummary;
    node.outputSummary =
      typeof attempt.metadata?.output_summary === "string"
        ? attempt.metadata.output_summary
        : attempt.error ?? attempt.message ?? node.outputSummary;

    event.status = mapStepStatus(attempt.status);
    event.startedAt = attempt.started_at ?? event.startedAt;
    event.endedAt = attempt.ended_at ?? event.endedAt;
    event.durationMs = attempt.duration_ms ?? event.durationMs;
    event.description = summarizeAttempt(attempt);
    event.input = attempt.metadata?.input ?? attempt.metadata ?? event.input;
    event.output =
      attempt.metadata?.output ??
      (attempt.message || attempt.citations.length > 0 ? { message: attempt.message, citations: attempt.citations } : event.output);
    event.raw = attempt;
    event.logs =
      attempt.logs.length > 0
        ? attempt.logs.map((entry, index) => ({
            id: `${event.id}-log-${index}`,
            timestamp: entry.timestamp,
            level: (entry.level.toLowerCase() as ExecutionLogLevel) || "info",
            message: entry.message,
            source: "runtime",
          }))
        : event.logs;
    event.metrics = {
      ...event.metrics,
      executionMs: attempt.duration_ms ?? event.metrics.executionMs,
      itemCount: Math.max(attempt.logs.length, attempt.citations.length),
    };

    if (attempt.error) {
      event.error = {
        summary: "Stage execution failed",
        message: attempt.error,
        rootCauseHints: [
          "Inspect upstream tool or model failures linked to this stage.",
          "Compare this stage against the previous successful run for latency and payload regressions.",
        ],
      };
    }
  }

  for (const trace of collectToolTraces(run)) {
    const nodeId = matchTraceNodeId(trace);
    if (!nodeId) {
      continue;
    }
    const node = nodeById.get(nodeId);
    if (!node) {
      continue;
    }
    node.state = trace.success ? "success" : "failed";
    node.durationMs = trace.duration_ms;
    node.retryCount = trace.success ? node.retryCount ?? 0 : Math.max(node.retryCount ?? 0, 1);
    node.itemCount = trace.citations.length;
    node.summary = trace.success
      ? `${trace.tool_name} completed in ${formatDuration(trace.duration_ms)}.`
      : trace.error ?? node.summary;
    node.tool = trace.tool_name;
  }

  if (!run.current_stage) {
    const firstFailure = baseTrace.events.find((event) => event.status === "failed");
    baseTrace.currentEventId = firstFailure?.id ?? baseTrace.currentEventId;
  }

  return baseTrace;
}

export function formatDuration(durationMs: number | undefined | null): string {
  if (!durationMs || durationMs <= 0) {
    return "-";
  }
  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }
  const seconds = Math.round((durationMs / 1000) * 10) / 10;
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round((seconds % 60) * 10) / 10;
  return `${minutes}m ${remaining}s`;
}

export function formatTimestamp(iso: string | undefined): string {
  if (!iso) {
    return "-";
  }
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDateTime(iso: string | undefined): string {
  if (!iso) {
    return "-";
  }
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatCurrency(value: number | undefined): string {
  if (!value) {
    return "$0.00";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value < 0.01 ? 4 : 2,
    maximumFractionDigits: value < 0.01 ? 4 : 2,
  }).format(value);
}

export function formatCount(value: number | undefined): string {
  if (!value && value !== 0) {
    return "-";
  }
  return new Intl.NumberFormat("en-US", { notation: value >= 1000 ? "compact" : "standard" }).format(value);
}

export function eventSearchText(event: ExecutionEvent, node: ExecutionNode | null): string {
  const logs = event.logs.map((entry) => `${entry.level} ${entry.message} ${entry.source ?? ""}`).join(" ");
  const raw = safeStringify(event.raw);
  const stream = event.stream?.chunks.map((chunk) => chunk.text).join(" ") ?? "";
  return [event.id, event.title, event.description, event.kind, event.status, node?.label ?? "", logs, raw, stream]
    .join(" ")
    .toLowerCase();
}

export function safeStringify(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function getEventMap(trace: ExecutionRunTrace): Map<string, ExecutionEvent> {
  return new Map(trace.events.map((event) => [event.id, event]));
}

export function getNodeMap(trace: ExecutionRunTrace): Map<string, ExecutionNode> {
  return new Map(trace.nodes.map((node) => [node.id, node]));
}

export function getSelectedEvent(trace: ExecutionRunTrace, eventId: string | null): ExecutionEvent | null {
  if (!eventId) {
    return trace.events[0] ?? null;
  }
  return getEventMap(trace).get(eventId) ?? trace.events[0] ?? null;
}

export function getEventChain(trace: ExecutionRunTrace, eventId: string): ExecutionEvent[] {
  const map = getEventMap(trace);
  const chain: ExecutionEvent[] = [];
  let current = map.get(eventId);
  while (current) {
    chain.unshift(current);
    current = current.parentEventId ? map.get(current.parentEventId) : undefined;
  }
  return chain;
}

export function getChildEvents(trace: ExecutionRunTrace, eventId: string): ExecutionEvent[] {
  const map = getEventMap(trace);
  return (map.get(eventId)?.childEventIds ?? [])
    .map((id) => map.get(id))
    .filter((item): item is ExecutionEvent => Boolean(item));
}

export function getFailureEventIds(trace: ExecutionRunTrace): string[] {
  return trace.events.filter((event) => event.status === "failed").map((event) => event.id);
}

function flattenGroupedRows(
  trace: ExecutionRunTrace,
  event: ExecutionEvent,
  expanded: Set<string>,
  rows: TimelineRow[],
  depth: number
) {
  rows.push({
    id: `row-${event.id}`,
    eventId: event.id,
    depth,
    event,
    parentEventId: event.parentEventId,
    hasChildren: event.childEventIds.length > 0,
    isExpanded: expanded.has(event.id),
    node: getNodeMap(trace).get(event.nodeId) ?? null,
  });

  if (!expanded.has(event.id)) {
    return;
  }

  for (const child of getChildEvents(trace, event.id)) {
    flattenGroupedRows(trace, child, expanded, rows, depth + 1);
  }
}

export function buildTimelineRows(
  trace: ExecutionRunTrace,
  mode: TimelineMode,
  expanded: Set<string>,
  filters: Set<TimelineFilter>,
  search: string,
  bookmarkedEventIds: Set<string>,
  pinBookmarkedFirst: boolean
): TimelineRow[] {
  const nodeMap = getNodeMap(trace);
  const normalizedSearch = search.trim().toLowerCase();
  const longRunningThreshold = 2000;

  const matches = (event: ExecutionEvent): boolean => {
    if (filters.has("errors") && event.status !== "failed") {
      return false;
    }
    if (filters.has("model_calls") && event.kind !== "model_call") {
      return false;
    }
    if (filters.has("tool_calls") && event.kind !== "tool_call") {
      return false;
    }
    if (filters.has("user_visible") && !event.tags.includes("user-visible")) {
      return false;
    }
    if (filters.has("long_running") && event.durationMs < longRunningThreshold) {
      return false;
    }
    if (filters.has("bookmarked") && !bookmarkedEventIds.has(event.id)) {
      return false;
    }
    if (!normalizedSearch) {
      return true;
    }
    return eventSearchText(event, nodeMap.get(event.nodeId) ?? null).includes(normalizedSearch);
  };

  let rows: TimelineRow[] = [];
  if (mode === "grouped") {
    const roots = trace.events
      .filter((event) => !event.parentEventId)
      .sort((left, right) => new Date(left.startedAt).getTime() - new Date(right.startedAt).getTime());
    for (const root of roots) {
      flattenGroupedRows(trace, root, expanded, rows, 0);
    }
  } else {
    rows = trace.events
      .slice()
      .sort((left, right) => new Date(left.startedAt).getTime() - new Date(right.startedAt).getTime())
      .map((event) => ({
        id: `row-${event.id}`,
        eventId: event.id,
        depth: 0,
        event,
        parentEventId: event.parentEventId,
        hasChildren: event.childEventIds.length > 0,
        isExpanded: false,
        node: nodeMap.get(event.nodeId) ?? null,
      }));
  }

  rows = rows.filter((row) => matches(row.event));

  if (pinBookmarkedFirst) {
    rows = rows.slice().sort((left, right) => {
      const leftRank = bookmarkedEventIds.has(left.eventId) ? 0 : 1;
      const rightRank = bookmarkedEventIds.has(right.eventId) ? 0 : 1;
      if (leftRank !== rightRank) {
        return leftRank - rightRank;
      }
      return new Date(left.event.startedAt).getTime() - new Date(right.event.startedAt).getTime();
    });
  }

  return rows;
}

export function expandAllEventIds(trace: ExecutionRunTrace): Set<string> {
  return new Set(trace.events.filter((event) => event.childEventIds.length > 0).map((event) => event.id));
}

export function getRcaRoleForNodeId(nodeId: string): "draft" | "final" | null {
  if (nodeId === "publish_report") {
    return "final";
  }
  if (nodeId === "synthesize_rca_report") {
    return "draft";
  }
  return null;
}
