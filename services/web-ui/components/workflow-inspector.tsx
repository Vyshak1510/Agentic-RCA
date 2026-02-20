"use client";

import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";

import { AgentToolTrace, StepAttempt, StepExecutionStatus, WorkflowRunDetail, WorkflowStageId } from "@/lib/types";
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
