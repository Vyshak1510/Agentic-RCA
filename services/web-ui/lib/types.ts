export type InvestigationStatus = "pending" | "running" | "completed" | "failed";
export type StepExecutionStatus = "queued" | "running" | "completed" | "failed" | "skipped";

export type WorkflowStageId =
  | "resolve_service_identity"
  | "build_investigation_plan"
  | "collect_evidence"
  | "synthesize_rca_report"
  | "publish_report"
  | "emit_eval_event";

export type Hypothesis = {
  statement: string;
  confidence: number;
  supporting_citations: string[];
  counter_evidence_citations: string[];
};

export type RcaReport = {
  top_hypotheses: Hypothesis[];
  likely_cause: string;
  blast_radius: string;
  recommended_manual_actions: string[];
  confidence: number;
};

export type StepLogEntry = {
  timestamp: string;
  level: string;
  message: string;
};

export type AgentToolTrace = {
  tool_name: string;
  source: string;
  read_only: boolean;
  started_at: string;
  ended_at: string;
  duration_ms: number;
  success: boolean;
  args_summary: Record<string, unknown>;
  result_summary: Record<string, unknown>;
  error: string | null;
  citations: string[];
};

export type StepAttempt = {
  attempt: number;
  status: StepExecutionStatus;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  message: string | null;
  error: string | null;
  logs: StepLogEntry[];
  citations: string[];
  metadata: Record<string, unknown>;
  stage_reasoning_summary?: string | null;
  tool_traces?: AgentToolTrace[];
};

export type WorkflowRunSummary = {
  run_id: string;
  investigation_id: string;
  workflow_id: string | null;
  status: InvestigationStatus;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  current_stage: WorkflowStageId | null;
  started_by: string;
};

export type WorkflowRunDetail = WorkflowRunSummary & {
  stage_attempts: Partial<Record<WorkflowStageId, StepAttempt[]>>;
  timeline: string[];
  events_count: number;
};

export type WorkflowRunEvent = {
  event_index: number | null;
  run_id: string;
  investigation_id: string;
  workflow_id: string | null;
  stage_id: WorkflowStageId | null;
  stage_status: StepExecutionStatus;
  run_status: InvestigationStatus | null;
  attempt: number;
  timestamp: string;
  message: string;
  error: string | null;
  citations: string[];
  metadata: Record<string, unknown>;
  logs: StepLogEntry[];
  stage_reasoning_summary?: string | null;
  tool_traces?: AgentToolTrace[];
};

export type InvestigationRecord = {
  id: string;
  status: InvestigationStatus;
  created_at: string;
  updated_at: string;
  active_run_id: string | null;
  latest_run_status: InvestigationStatus | null;
  alert: {
    source: string;
    severity: string;
    incident_key: string;
    entity_ids: string[];
    timestamps: Record<string, string>;
  };
  timeline: string[];
  report?: RcaReport | null;
};

export type InvestigationListResponse = {
  items: InvestigationRecord[];
  total: number;
  page: number;
  page_size: number;
};

export type UserContext = {
  user_id: string;
  role: string;
  tenant: string;
};

export type ConnectorCredentialView = {
  provider: string;
  tenant: string;
  environment: string;
  mode: "secret_ref" | "raw_key";
  secret_ref_name: string | null;
  secret_ref_key: string | null;
  key_last4: string | null;
  updated_at: string;
  updated_by: string;
};

export type LlmRoute = {
  tenant: string;
  environment: string;
  primary_model: string;
  fallback_model: string;
  key_ref: string;
};

export type McpServerConfig = {
  server_id: string;
  tenant: string;
  environment: string;
  transport: "http_sse";
  base_url: string;
  secret_ref_name: string | null;
  secret_ref_key: string | null;
  timeout_seconds: number;
  enabled: boolean;
  updated_at: string;
  updated_by: string;
};

export type McpToolDescriptor = {
  server_id: string;
  tool_name: string;
  description: string | null;
  capabilities: string[];
  read_only: boolean;
  light_probe: boolean;
  arg_keys: string[];
  required_args: string[];
};

export type AgentPromptProfile = {
  tenant: string;
  environment: string;
  stage_id: WorkflowStageId;
  system_prompt: string;
  objective_template: string;
  max_turns: number;
  max_tool_calls: number;
  tool_allowlist: string[];
  updated_at: string;
  updated_by: string;
};

export type AgentRolloutConfig = {
  tenant: string;
  environment: string;
  mode: "compare" | "active";
  updated_at: string;
  updated_by: string;
};

export type WorkflowLayoutNode = {
  id: string;
  x: number;
  y: number;
};

export type WorkflowViewport = {
  x: number;
  y: number;
  zoom: number;
};

export type WorkflowLayoutState = {
  workflow_key: string;
  tenant: string;
  user_id: string;
  nodes: WorkflowLayoutNode[];
  viewport: WorkflowViewport;
  updated_at: string;
};
