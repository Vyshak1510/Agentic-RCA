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

export type AliasCandidateScore = {
  term: string;
  term_source: string;
  candidate: string;
  score: number;
};

export type AliasDecisionTrace = {
  strategy: string;
  selected_candidate: string | null;
  matched_term: string | null;
  matched_term_source: string | null;
  confidence: number;
  ambiguous_candidates: string[];
  top_candidates: AliasCandidateScore[];
  unresolved_reason: string | null;
};

export type RerunDirective = {
  target_stage: WorkflowStageId;
  reason: string;
  additional_objective: string;
  expected_evidence: string;
  tool_focus: string[];
};

export type RerunLedgerEntry = {
  sequence: number;
  requested_by_stage: WorkflowStageId;
  target_stage: WorkflowStageId;
  reason: string;
  additional_objective: string;
  expected_evidence: string;
  tool_focus: string[];
  accepted: boolean;
  outcome: string;
  requested_at: string;
  completed_at: string | null;
};

export type EvidenceRequirement = {
  evidence_class: string;
  description: string;
  tool_patterns: string[];
  query_scope: string;
  required_symptoms: string[];
};

export type StageEvalRecord = {
  stage_id: WorkflowStageId;
  record_id: string;
  status: string;
  summary: string;
  score: number | null;
  findings: string[];
  details: Record<string, unknown>;
};

export type TeamRcaDraft = {
  team_id: string;
  status: string;
  summary: string;
  mission_id?: string | null;
  mission_checklist?: MissionChecklistResult | null;
  context_refs?: ContextReference[];
  unknown_not_available_reasons?: string[];
  relevance_weights?: Record<string, number>;
  completeness_status?: string;
  hypotheses: Hypothesis[];
  confidence: number;
  supporting_citations: string[];
  unknowns: string[];
  tool_traces: AgentToolTrace[];
  skipped_tools: Record<string, unknown>[];
  artifact_state?: ArtifactState | null;
  resolved_aliases?: ResolvedTelemetryAlias[];
  blocked_tools?: Record<string, unknown>[];
  invocable_tools?: string[];
  alias_decision_trace?: AliasDecisionTrace | null;
};

export type TeamExecutionSummary = {
  team_id: string;
  status: string;
  mission_id?: string | null;
  mission_checklist?: MissionChecklistResult | null;
  context_refs?: ContextReference[];
  unknown_not_available_reasons?: string[];
  relevance_weights?: Record<string, number>;
  selected_tools: string[];
  executed_tool_count: number;
  failed_tool_count: number;
  evidence_count: number;
  duration_ms: number;
  citations: string[];
  error: string | null;
  artifact_state?: ArtifactState | null;
  resolved_aliases?: ResolvedTelemetryAlias[];
  blocked_tools?: Record<string, unknown>[];
  invocable_tools?: string[];
  alias_decision_trace?: AliasDecisionTrace | null;
};

export type CommanderArbitrationSummary = {
  selected_team_ids: string[];
  arbitration_conflicts: string[];
  arbitration_decision_trace: string;
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
  team_reports?: TeamRcaDraft[];
  team_execution?: TeamExecutionSummary[];
  arbitration_conflicts?: string[];
  arbitration_decision_trace?: string | null;
  mission_id?: string;
  mission_checklist?: MissionChecklistResult;
  context_refs?: ContextReference[];
  unknown_not_available_reasons?: string[];
  relevance_weights?: Record<string, number>;
  artifact_state?: ArtifactState;
  resolved_aliases?: ResolvedTelemetryAlias[];
  blocked_tools?: Record<string, unknown>[];
  invocable_tools?: string[];
  alias_decision_trace?: AliasDecisionTrace | null;
  rerun_directives?: RerunDirective[];
  stage_eval_records?: StageEvalRecord[];
  effective_prompt_snapshot?: Record<string, unknown> | null;
  effective_mission_snapshot?: StageMissionProfile | Record<string, unknown> | null;
  effective_team_mission_snapshots?: Record<string, TeamMissionProfile> | Record<string, unknown> | null;
  effective_tool_catalog_summary?: Record<string, unknown> | null;
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

export type MissionChecklistResult = {
  mission_id: string;
  completed: string[];
  failed: string[];
  unavailable: string[];
  passed: boolean;
};

export type ContextReference = {
  context_citation_id: string;
  pack_id: string;
  pack_version: number;
  artifact_id: string;
  chunk_id: string;
  stage_id: WorkflowStageId | null;
  team_id: string | null;
  summary: string;
  score: number;
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
  phase: "discover" | "resolve" | "inspect" | "drilldown";
  scope_kind: "global" | "service" | "trace" | "datasource" | "dashboard" | "metric";
  requires_artifacts: string[];
  produces_artifacts: string[];
  default_priority: number;
  result_adapter: string | null;
};

export type ResolvedTelemetryAlias = {
  alert_term: string;
  resolved_value: string;
  source: string;
  confidence: number;
  candidates: string[];
};

export type ArtifactState = {
  alert_terms: string[];
  entity_terms: string[];
  explicit_service_terms: string[];
  title_terms: string[];
  summary_terms: string[];
  service_candidates: string[];
  resolved_service: string | null;
  service_aliases: ResolvedTelemetryAlias[];
  alias_decision_trace?: AliasDecisionTrace | null;
  operation_candidates: string[];
  resolved_operations: string[];
  trace_ids: string[];
  datasource_ids: string[];
  dashboard_uids: string[];
  annotation_tags: string[];
  metric_label_keys: string[];
  metric_service_candidates: string[];
  trace_summaries: Record<string, unknown>[];
  dependency_edges: string[];
  root_cause_signals: string[];
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

export type InvestigationTeamProfile = {
  team_id: string;
  tenant: string;
  environment: string;
  enabled: boolean;
  objective_prompt: string;
  tool_allowlist: string[];
  max_tool_calls: number;
  max_parallel_calls: number;
  timeout_seconds: number;
  updated_at: string;
  updated_by: string;
};

export type StageMissionProfile = {
  tenant: string;
  environment: string;
  stage_id: WorkflowStageId;
  mission_objective: string;
  required_checks: string[];
  allowed_tools: string[];
  completion_criteria: string[];
  unknown_not_available_rules: string[];
  relevance_weights: Record<string, number>;
  alias_priority_order: string[];
  alias_min_confidence: number;
  summary_tiebreak_only: boolean;
  updated_at: string;
  updated_by: string;
};

export type TeamMissionProfile = {
  team_id: string;
  tenant: string;
  environment: string;
  mission_objective: string;
  required_checks: string[];
  allowed_tools: string[];
  completion_criteria: string[];
  unknown_not_available_rules: string[];
  relevance_weights: Record<string, number>;
  evidence_requirements: EvidenceRequirement[];
  symptom_overrides: Record<string, string[]>;
  updated_at: string;
  updated_by: string;
};

export type ContextArtifact = {
  artifact_id: string;
  pack_id: string;
  filename: string;
  artifact_type: string;
  media_type: string | null;
  content: string;
  operator_notes: string | null;
  metadata: Record<string, unknown>;
  parsed_chunks: Array<{ chunk_id: string; text: string; metadata: Record<string, unknown> }>;
  created_at: string;
  created_by: string;
};

export type ContextPack = {
  pack_id: string;
  tenant: string;
  environment: string;
  name: string;
  description: string | null;
  version: number;
  status: string;
  stage_bindings: WorkflowStageId[];
  team_bindings: string[];
  service_tags: string[];
  infra_components: string[];
  dependencies: string[];
  validity_start: string | null;
  validity_end: string | null;
  artifacts: ContextArtifact[];
  active: boolean;
  created_at: string;
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
