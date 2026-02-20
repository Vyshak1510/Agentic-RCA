import { StepAttempt, StepExecutionStatus, WorkflowRunDetail, WorkflowStageId } from "@/lib/types";

export type WorkflowStageDefinition = {
  id: WorkflowStageId;
  label: string;
  summary: string;
};

export const WORKFLOW_STAGES: WorkflowStageDefinition[] = [
  {
    id: "resolve_service_identity",
    label: "Resolve Service Identity",
    summary: "Map provider entities to canonical service, owner, and environment."
  },
  {
    id: "build_investigation_plan",
    label: "Build Investigation Plan",
    summary: "Select bounded connector queries from policy and capability metadata."
  },
  {
    id: "collect_evidence",
    label: "Collect Evidence",
    summary: "Fetch read-only telemetry, normalize payloads, and attach citations."
  },
  {
    id: "synthesize_rca_report",
    label: "Synthesize RCA Report",
    summary: "Rank top hypotheses with contradiction checks and confidence scoring."
  },
  {
    id: "publish_report",
    label: "Publish Report",
    summary: "Send RCA summary to Slack and Jira when publication is allowed."
  },
  {
    id: "emit_eval_event",
    label: "Emit Eval Event",
    summary: "Record prediction metadata for adjudication and rollout gates."
  }
];

export type StageVisualStatus = StepExecutionStatus | "idle";

export function latestAttempt(run: WorkflowRunDetail | null, stageId: WorkflowStageId): StepAttempt | null {
  if (!run) {
    return null;
  }
  const attempts = run.stage_attempts[stageId];
  if (!attempts || attempts.length === 0) {
    return null;
  }
  return attempts[attempts.length - 1] ?? null;
}

export function stageStatus(run: WorkflowRunDetail | null, stageId: WorkflowStageId): StageVisualStatus {
  const attempt = latestAttempt(run, stageId);
  if (!attempt) {
    return "idle";
  }
  return attempt.status;
}

export function msToHuman(durationMs: number | null): string {
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
