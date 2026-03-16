"use client";

import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";

import {
  AgentToolTrace,
  AliasDecisionTrace,
  ArtifactState,
  ContextReference,
  MissionChecklistResult,
  RerunDirective,
  RerunLedgerEntry,
  ResolvedTelemetryAlias,
  StageEvalRecord,
  StepAttempt,
  StepExecutionStatus,
  TeamExecutionSummary,
  TeamRcaDraft,
  WorkflowRunDetail,
  WorkflowStageId,
} from "@/lib/types";
import { WORKFLOW_STAGES, msToHuman } from "@/lib/workflow";

type Props = {
  run: WorkflowRunDetail | null;
  selectedStageId: WorkflowStageId | null;
};

const stageDetails = Object.fromEntries(
  WORKFLOW_STAGES.map((stage) => [stage.id, { label: stage.label, summary: stage.summary }])
) as Record<WorkflowStageId, { label: string; summary: string }>;

const statusStyles: Record<StepExecutionStatus, string> = {
  queued: "border-amber-200 bg-amber-50 text-amber-700",
  running: "border-sky-200 bg-sky-50 text-sky-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  skipped: "border-violet-200 bg-violet-50 text-violet-700"
};

function sectionTimestamp(iso: string | null): string {
  if (!iso) {
    return "-";
  }
  return new Date(iso).toLocaleString();
}

function selectDefaultAttempt(attempts: StepAttempt[]): number | null {
  return attempts.length ? attempts[attempts.length - 1]?.attempt ?? null : null;
}

function extractToolTraces(attempt: StepAttempt): AgentToolTrace[] {
  if (Array.isArray(attempt.tool_traces)) {
    return attempt.tool_traces;
  }
  const metadataTraces = (attempt.metadata?.tool_traces as AgentToolTrace[] | undefined) ?? [];
  return Array.isArray(metadataTraces) ? metadataTraces : [];
}

function extractTeamReports(attempt: StepAttempt): TeamRcaDraft[] {
  if (Array.isArray(attempt.team_reports)) {
    return attempt.team_reports;
  }
  const inline = attempt.metadata?.team_reports;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is TeamRcaDraft => typeof item === "object" && item !== null) as TeamRcaDraft[];
  }
  return [];
}

function extractTeamExecution(attempt: StepAttempt): TeamExecutionSummary[] {
  if (Array.isArray(attempt.team_execution)) {
    return attempt.team_execution;
  }
  const inline = attempt.metadata?.team_execution;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is TeamExecutionSummary => typeof item === "object" && item !== null) as TeamExecutionSummary[];
  }
  return [];
}

function extractArbitrationConflicts(attempt: StepAttempt): string[] {
  if (Array.isArray(attempt.arbitration_conflicts)) {
    return attempt.arbitration_conflicts;
  }
  const inline = attempt.metadata?.arbitration_conflicts;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is string => typeof item === "string");
  }
  return [];
}

function extractArbitrationDecisionTrace(attempt: StepAttempt): string | null {
  if (typeof attempt.arbitration_decision_trace === "string") {
    return attempt.arbitration_decision_trace;
  }
  const inline = attempt.metadata?.arbitration_decision_trace;
  return typeof inline === "string" ? inline : null;
}

function extractMissionChecklist(attempt: StepAttempt): MissionChecklistResult | null {
  if (attempt.mission_checklist && typeof attempt.mission_checklist === "object") {
    return attempt.mission_checklist;
  }
  const inline = attempt.metadata?.mission_checklist;
  if (inline && typeof inline === "object") {
    return inline as MissionChecklistResult;
  }
  return null;
}

function extractContextRefs(attempt: StepAttempt): ContextReference[] {
  if (Array.isArray(attempt.context_refs)) {
    return attempt.context_refs;
  }
  const inline = attempt.metadata?.context_refs;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is ContextReference => typeof item === "object" && item !== null) as ContextReference[];
  }
  return [];
}

function extractArtifactState(attempt: StepAttempt): ArtifactState | null {
  if (attempt.artifact_state && typeof attempt.artifact_state === "object") {
    return attempt.artifact_state;
  }
  const inline = attempt.metadata?.artifact_state;
  if (inline && typeof inline === "object") {
    return inline as ArtifactState;
  }
  return null;
}

function extractResolvedAliases(attempt: StepAttempt): ResolvedTelemetryAlias[] {
  if (Array.isArray(attempt.resolved_aliases)) {
    return attempt.resolved_aliases;
  }
  const inline = attempt.metadata?.resolved_aliases;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is ResolvedTelemetryAlias => typeof item === "object" && item !== null) as ResolvedTelemetryAlias[];
  }
  return [];
}

function extractBlockedTools(attempt: StepAttempt): Record<string, unknown>[] {
  if (Array.isArray(attempt.blocked_tools)) {
    return attempt.blocked_tools;
  }
  const inline = attempt.metadata?.blocked_tools;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null) as Record<string, unknown>[];
  }
  return [];
}

function extractInvocableTools(attempt: StepAttempt): string[] {
  if (Array.isArray(attempt.invocable_tools)) {
    return attempt.invocable_tools;
  }
  const inline = attempt.metadata?.invocable_tools;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is string => typeof item === "string");
  }
  return [];
}

function extractAliasDecisionTrace(attempt: StepAttempt): AliasDecisionTrace | null {
  if (attempt.alias_decision_trace && typeof attempt.alias_decision_trace === "object") {
    return attempt.alias_decision_trace;
  }
  const inline = attempt.metadata?.alias_decision_trace;
  if (inline && typeof inline === "object") {
    return inline as AliasDecisionTrace;
  }
  return extractArtifactState(attempt)?.alias_decision_trace ?? null;
}

function extractRerunDirectives(attempt: StepAttempt): RerunDirective[] {
  if (Array.isArray(attempt.rerun_directives)) {
    return attempt.rerun_directives;
  }
  const inline = attempt.metadata?.rerun_directives;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is RerunDirective => typeof item === "object" && item !== null) as RerunDirective[];
  }
  return [];
}

function extractStageEvalRecords(attempt: StepAttempt): StageEvalRecord[] {
  if (Array.isArray(attempt.stage_eval_records)) {
    return attempt.stage_eval_records;
  }
  const inline = attempt.metadata?.stage_eval_records;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is StageEvalRecord => typeof item === "object" && item !== null) as StageEvalRecord[];
  }
  return [];
}

function extractEffectivePromptSnapshot(attempt: StepAttempt): Record<string, unknown> | null {
  const snapshot = attempt.effective_prompt_snapshot ?? attempt.metadata?.effective_prompt_snapshot;
  return snapshot && typeof snapshot === "object" ? (snapshot as Record<string, unknown>) : null;
}

function extractEffectiveMissionSnapshot(attempt: StepAttempt): Record<string, unknown> | null {
  const snapshot = attempt.effective_mission_snapshot ?? attempt.metadata?.effective_mission_snapshot;
  return snapshot && typeof snapshot === "object" ? (snapshot as Record<string, unknown>) : null;
}

function extractEffectiveTeamMissionSnapshots(attempt: StepAttempt): Record<string, unknown> | null {
  const snapshot = attempt.effective_team_mission_snapshots ?? attempt.metadata?.effective_team_mission_snapshots;
  return snapshot && typeof snapshot === "object" ? (snapshot as Record<string, unknown>) : null;
}

function extractEffectiveToolCatalogSummary(attempt: StepAttempt): Record<string, unknown> | null {
  const snapshot = attempt.effective_tool_catalog_summary ?? attempt.metadata?.effective_tool_catalog_summary;
  return snapshot && typeof snapshot === "object" ? (snapshot as Record<string, unknown>) : null;
}

function extractRerunLedger(attempt: StepAttempt): RerunLedgerEntry[] {
  const inline = attempt.metadata?.rerun_ledger;
  if (Array.isArray(inline)) {
    return inline.filter((item): item is RerunLedgerEntry => typeof item === "object" && item !== null) as RerunLedgerEntry[];
  }
  return [];
}

export function WorkflowInspector({ run, selectedStageId }: Props) {
  const [selectedAttempt, setSelectedAttempt] = useState<number | null>(null);

  const attempts = useMemo(() => {
    if (!run || !selectedStageId) {
      return [];
    }
    return run.stage_attempts[selectedStageId] ?? [];
  }, [run, selectedStageId]);

  useEffect(() => {
    setSelectedAttempt((current) => {
      if (!attempts.length) {
        return null;
      }
      if (current !== null && attempts.some((attempt) => attempt.attempt === current)) {
        return current;
      }
      return selectDefaultAttempt(attempts);
    });
  }, [attempts]);

  if (!run) {
    return (
      <aside className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-[0_8px_32px_rgba(23,23,23,0.08)]">
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-[0.08em] text-neutral-600">Step Inspector</h3>
        <p className="text-sm text-neutral-500">Select a run to inspect execution details.</p>
      </aside>
    );
  }

  if (!selectedStageId) {
    return (
      <aside className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-[0_8px_32px_rgba(23,23,23,0.08)]">
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-[0.08em] text-neutral-600">Step Inspector</h3>
        <p className="text-sm text-neutral-500">Click a stage node to view attempts, logs, and citations.</p>
      </aside>
    );
  }

  const selectedStage = stageDetails[selectedStageId];

  return (
    <aside className="h-full rounded-2xl border border-neutral-200 bg-white shadow-[0_8px_32px_rgba(23,23,23,0.08)]">
      <div className="border-b border-neutral-200 p-5">
        <h3 className="text-sm font-semibold uppercase tracking-[0.08em] text-neutral-600">Step Inspector</h3>
        <p className="mt-2 text-[15px] font-semibold tracking-[-0.01em] text-[#171717]">{selectedStage.label}</p>
        <p className="mt-1 text-[13px] text-[#676767]">{selectedStage.summary}</p>
        <div className="mt-3 flex items-center gap-2 text-xs">
          <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2.5 py-1 font-semibold text-neutral-700">
            Attempts: {attempts.length}
          </span>
          <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2.5 py-1 font-semibold text-neutral-700">
            Run events: {run.events_count}
          </span>
        </div>
      </div>

      <div className="max-h-[680px] space-y-3 overflow-y-auto p-4">
        {attempts
          .slice()
          .reverse()
          .map((attempt) => {
            const expanded = selectedAttempt === attempt.attempt;
            return (
              <div key={`attempt-${attempt.attempt}`} className="overflow-hidden rounded-xl border border-neutral-200 bg-[#fcfcfc]">
                <button
                  type="button"
                  onClick={() => setSelectedAttempt((current) => (current === attempt.attempt ? null : attempt.attempt))}
                  className={clsx("w-full p-3 text-left transition", expanded ? "bg-[#fff8f2]" : "hover:bg-neutral-50")}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-[#171717]">Attempt {attempt.attempt}</p>
                      <p className="mt-1 text-xs text-[#676767]">
                        {sectionTimestamp(attempt.started_at)} • {msToHuman(attempt.duration_ms)}
                      </p>
                    </div>
                    <span className={clsx("rounded-full border px-2 py-1 text-[11px] font-semibold capitalize", statusStyles[attempt.status])}>
                      {attempt.status}
                    </span>
                  </div>
                </button>

                {expanded ? (
                  <div className="space-y-3 border-t border-neutral-200 bg-white p-3">
                    {(attempt.stage_reasoning_summary || (attempt.metadata?.stage_reasoning_summary as string | undefined)) ? (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-sm text-amber-800">
                        {attempt.stage_reasoning_summary ?? (attempt.metadata?.stage_reasoning_summary as string)}
                      </div>
                    ) : null}

                    {extractMissionChecklist(attempt) ? (
                      <div className="rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
                        <p className="font-semibold text-slate-800">
                          mission: {(attempt.mission_id ?? (attempt.metadata?.mission_id as string | undefined)) ?? extractMissionChecklist(attempt)?.mission_id}
                        </p>
                        <p className="mt-1">
                          passed={String(Boolean(extractMissionChecklist(attempt)?.passed))} · completed=
                          {extractMissionChecklist(attempt)?.completed.length ?? 0} · failed=
                          {extractMissionChecklist(attempt)?.failed.length ?? 0} · unavailable=
                          {extractMissionChecklist(attempt)?.unavailable.length ?? 0}
                        </p>
                        {extractMissionChecklist(attempt)?.failed.length ? (
                          <p className="mt-1 text-rose-700">
                            failed checks: {extractMissionChecklist(attempt)?.failed.join(" | ")}
                          </p>
                        ) : null}
                        {extractMissionChecklist(attempt)?.unavailable.length ? (
                          <p className="mt-1 text-amber-700">
                            unavailable checks: {extractMissionChecklist(attempt)?.unavailable.join(" | ")}
                          </p>
                        ) : null}
                      </div>
                    ) : null}

                    {extractContextRefs(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Context References</h4>
                        <div className="mt-1.5 space-y-1">
                          {extractContextRefs(attempt).map((ref) => (
                            <div key={ref.context_citation_id} className="rounded border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] text-[#171717]">
                              {ref.context_citation_id} · {ref.summary}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractResolvedAliases(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Resolved Aliases</h4>
                        <div className="mt-1.5 space-y-1">
                          {extractResolvedAliases(attempt).map((alias, idx) => (
                            <div key={`${alias.alert_term}-${alias.resolved_value}-${idx}`} className="rounded border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] text-[#171717]">
                              {alias.alert_term} {"->"} {alias.resolved_value} · source={alias.source} · confidence=
                              {alias.confidence.toFixed(2)}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractAliasDecisionTrace(attempt) ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Alias Decision</h4>
                        <div className="mt-1.5 rounded-lg border border-neutral-200 bg-neutral-50 p-2 text-[11px] text-[#171717]">
                          <p>
                            strategy={extractAliasDecisionTrace(attempt)?.strategy} · selected=
                            {extractAliasDecisionTrace(attempt)?.selected_candidate ?? "-"} · source=
                            {extractAliasDecisionTrace(attempt)?.matched_term_source ?? "-"} · confidence=
                            {extractAliasDecisionTrace(attempt)?.confidence.toFixed(2)}
                          </p>
                          {extractAliasDecisionTrace(attempt)?.unresolved_reason ? (
                            <p className="mt-1 text-amber-700">
                              unresolved_reason: {extractAliasDecisionTrace(attempt)?.unresolved_reason}
                            </p>
                          ) : null}
                          {extractAliasDecisionTrace(attempt)?.ambiguous_candidates.length ? (
                            <p className="mt-1 text-[#676767]">
                              ambiguous: {extractAliasDecisionTrace(attempt)?.ambiguous_candidates.join(" | ")}
                            </p>
                          ) : null}
                          {extractAliasDecisionTrace(attempt)?.top_candidates.length ? (
                            <div className="mt-2 space-y-1">
                              {extractAliasDecisionTrace(attempt)?.top_candidates.map((candidate, idx) => (
                                <div key={`${candidate.term}-${candidate.candidate}-${idx}`} className="rounded border border-neutral-200 bg-white px-2 py-1">
                                  {candidate.term_source}:{candidate.term} {"->"} {candidate.candidate} · score=
                                  {candidate.score.toFixed(2)}
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      </div>
                    ) : null}

                    {extractArtifactState(attempt) ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Artifact Blackboard</h4>
                        <pre className="mt-1.5 overflow-auto rounded border border-neutral-200 bg-neutral-50 p-2 text-[10px] text-[#171717]">
                          {JSON.stringify(extractArtifactState(attempt), null, 2)}
                        </pre>
                      </div>
                    ) : null}

                    {extractRerunDirectives(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Rerun Directives</h4>
                        <div className="mt-1.5 space-y-1.5">
                          {extractRerunDirectives(attempt).map((directive, idx) => (
                            <div key={`rerun-${idx}`} className="rounded-lg border border-violet-200 bg-violet-50 p-2 text-[11px] text-violet-800">
                              <p className="font-semibold">
                                target={directive.target_stage} · reason={directive.reason}
                              </p>
                              <p className="mt-1">{directive.additional_objective}</p>
                              <p className="mt-1 text-violet-700">expected={directive.expected_evidence}</p>
                              {directive.tool_focus.length ? (
                                <p className="mt-1 text-violet-700">tool_focus={directive.tool_focus.join(" | ")}</p>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractStageEvalRecords(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Stage Eval</h4>
                        <div className="mt-1.5 space-y-1.5">
                          {extractStageEvalRecords(attempt).map((record, idx) => (
                            <div key={`stage-eval-${idx}`} className="rounded-lg border border-neutral-200 bg-neutral-50 p-2 text-[11px] text-[#171717]">
                              <p className="font-semibold">
                                {record.record_id} · {record.status}
                                {typeof record.score === "number" ? ` · score=${record.score.toFixed(2)}` : ""}
                              </p>
                              <p className="mt-1 text-[#676767]">{record.summary}</p>
                              {record.findings.length ? (
                                <p className="mt-1 text-amber-700">findings: {record.findings.join(" | ")}</p>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractInvocableTools(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Invocable Tools</h4>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {extractInvocableTools(attempt).map((tool) => (
                            <span key={tool} className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-700">
                              {tool}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractEffectivePromptSnapshot(attempt) ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Effective Prompt</h4>
                        <pre className="mt-1.5 overflow-auto rounded border border-neutral-200 bg-neutral-50 p-2 text-[10px] text-[#171717]">
                          {JSON.stringify(extractEffectivePromptSnapshot(attempt), null, 2)}
                        </pre>
                      </div>
                    ) : null}

                    {extractEffectiveMissionSnapshot(attempt) ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Effective Mission</h4>
                        <pre className="mt-1.5 overflow-auto rounded border border-neutral-200 bg-neutral-50 p-2 text-[10px] text-[#171717]">
                          {JSON.stringify(extractEffectiveMissionSnapshot(attempt), null, 2)}
                        </pre>
                      </div>
                    ) : null}

                    {extractEffectiveTeamMissionSnapshots(attempt) ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Effective Team Missions</h4>
                        <pre className="mt-1.5 overflow-auto rounded border border-neutral-200 bg-neutral-50 p-2 text-[10px] text-[#171717]">
                          {JSON.stringify(extractEffectiveTeamMissionSnapshots(attempt), null, 2)}
                        </pre>
                      </div>
                    ) : null}

                    {extractEffectiveToolCatalogSummary(attempt) ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Effective Tool Catalog</h4>
                        <pre className="mt-1.5 overflow-auto rounded border border-neutral-200 bg-neutral-50 p-2 text-[10px] text-[#171717]">
                          {JSON.stringify(extractEffectiveToolCatalogSummary(attempt), null, 2)}
                        </pre>
                      </div>
                    ) : null}

                    {extractBlockedTools(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Blocked Tools</h4>
                        <div className="mt-1.5 space-y-1">
                          {extractBlockedTools(attempt).map((entry, idx) => (
                            <pre key={`blocked-${idx}`} className="overflow-auto rounded border border-amber-200 bg-amber-50 p-2 text-[10px] text-amber-800">
                              {JSON.stringify(entry, null, 2)}
                            </pre>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractRerunLedger(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Rerun Ledger</h4>
                        <div className="mt-1.5 space-y-1">
                          {extractRerunLedger(attempt).map((entry) => (
                            <div key={`rerun-ledger-${entry.sequence}`} className="rounded border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] text-[#171717]">
                              #{entry.sequence} {entry.requested_by_stage} {"->"} {entry.target_stage} · accepted=
                              {String(entry.accepted)} · outcome={entry.outcome}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {attempt.message ? (
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-2.5 text-sm text-[#171717]">{attempt.message}</div>
                    ) : null}

                    {attempt.error ? (
                      <div className="rounded-lg border border-rose-200 bg-rose-50 p-2.5 text-sm text-rose-700">{attempt.error}</div>
                    ) : null}

                    <div className="grid grid-cols-2 gap-2 text-xs text-[#676767]">
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-2 py-1.5">
                        Started: {sectionTimestamp(attempt.started_at)}
                      </div>
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-2 py-1.5">
                        Ended: {sectionTimestamp(attempt.ended_at)}
                      </div>
                    </div>

                    {attempt.citations.length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Citations</h4>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {attempt.citations.map((citation) => (
                            <span key={citation} className="rounded-full border border-neutral-200 bg-neutral-50 px-2 py-1 text-[11px] text-[#171717]">
                              {citation}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {attempt.logs.length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Logs</h4>
                        <div className="mt-1.5 max-h-48 space-y-1 overflow-y-auto rounded-lg border border-neutral-200 bg-[#171717] p-2 font-mono text-[11px] text-neutral-100">
                          {attempt.logs.slice(-14).map((log, idx) => (
                            <p key={`${log.timestamp}-${idx}`} className="whitespace-pre-wrap">
                              [{new Date(log.timestamp).toLocaleTimeString()}] {log.message}
                            </p>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractToolTraces(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Tool Calls</h4>
                        <div className="mt-1.5 space-y-2">
                          {extractToolTraces(attempt).map((trace, idx) => (
                            <div key={`${trace.tool_name}-${idx}`} className="rounded-lg border border-neutral-200 bg-neutral-50 p-2">
                              <div className="flex items-center justify-between gap-2">
                                <div className="text-xs font-semibold text-[#171717]">{trace.tool_name}</div>
                                <span
                                  className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                                    trace.success
                                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                                      : "border-rose-200 bg-rose-50 text-rose-700"
                                  }`}
                                >
                                  {trace.success ? "ok" : "error"}
                                </span>
                              </div>
                              <p className="mt-1 text-[11px] text-[#676767]">
                                source={trace.source} · read_only={String(trace.read_only)} · duration={trace.duration_ms}ms
                              </p>
                              <div className="mt-1 grid gap-2 md:grid-cols-2">
                                <pre className="overflow-auto rounded border border-neutral-200 bg-white p-1.5 text-[10px] text-[#171717]">
                                  {JSON.stringify(trace.args_summary, null, 2)}
                                </pre>
                                <pre className="overflow-auto rounded border border-neutral-200 bg-white p-1.5 text-[10px] text-[#171717]">
                                  {JSON.stringify(trace.result_summary, null, 2)}
                                </pre>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractTeamExecution(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Team Execution</h4>
                        <div className="mt-1.5 space-y-1.5">
                          {extractTeamExecution(attempt).map((entry, idx) => (
                            <div key={`team-exec-${idx}`} className="rounded-lg border border-neutral-200 bg-neutral-50 p-2 text-xs text-[#171717]">
                              <p className="font-semibold">
                                {entry.team_id} · {entry.status}
                              </p>
                              <p className="mt-1 text-[11px] text-[#676767]">
                                tools={entry.executed_tool_count} · failed={entry.failed_tool_count} · evidence={entry.evidence_count} · duration=
                                {entry.duration_ms}ms
                              </p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractTeamReports(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Team Mini RCAs</h4>
                        <div className="mt-1.5 space-y-1.5">
                          {extractTeamReports(attempt).map((entry, idx) => (
                            <div key={`team-report-${idx}`} className="rounded-lg border border-neutral-200 bg-neutral-50 p-2 text-xs text-[#171717]">
                              <p className="font-semibold">
                                {entry.team_id} · {entry.status} · confidence={entry.confidence}
                              </p>
                              <p className="mt-1 text-[11px] text-[#676767]">{entry.summary}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {extractArbitrationDecisionTrace(attempt) || extractArbitrationConflicts(attempt).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Commander Arbitration</h4>
                        <div className="mt-1.5 space-y-1.5 rounded-lg border border-neutral-200 bg-neutral-50 p-2 text-xs text-[#171717]">
                          {extractArbitrationDecisionTrace(attempt) ? (
                            <p className="text-[11px] text-[#171717]">{extractArbitrationDecisionTrace(attempt)}</p>
                          ) : null}
                          {extractArbitrationConflicts(attempt).length ? (
                            <p className="text-[11px] text-[#676767]">
                              conflicts: {extractArbitrationConflicts(attempt).join(" | ")}
                            </p>
                          ) : null}
                        </div>
                      </div>
                    ) : null}

                    {Object.keys(attempt.metadata).length ? (
                      <div>
                        <h4 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-neutral-500">Metadata</h4>
                        <pre className="mt-1.5 max-h-48 overflow-auto rounded-lg border border-neutral-200 bg-neutral-50 p-2 text-[11px] leading-5 text-[#171717]">
                          {JSON.stringify(attempt.metadata, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}

        {!attempts.length ? (
          <p className="rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-500">
            This stage has not executed yet.
          </p>
        ) : null}
      </div>
    </aside>
  );
}
