"use client";

import { useEffect, useMemo, useState } from "react";

import { StatusBadge } from "@/components/status-badge";
import { WorkflowInspector } from "@/components/workflow-inspector";
import { WorkflowMapper } from "@/components/workflow-mapper";
import { fetchInvestigationRun, fetchInvestigationRuns, getRunEventsUrl } from "@/lib/api";
import { WorkflowRunDetail, WorkflowRunEvent, WorkflowRunSummary, WorkflowStageId } from "@/lib/types";

type StreamState = "connected" | "reconnecting" | "disconnected";

type Props = {
  investigationId: string;
  incidentKey: string;
  initialRunId?: string | null;
};

export function LiveFlow({ investigationId, incidentKey, initialRunId = null }: Props) {
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(initialRunId);
  const [selectedStageId, setSelectedStageId] = useState<WorkflowStageId | null>(null);
  const [run, setRun] = useState<WorkflowRunDetail | null>(null);
  const [streamState, setStreamState] = useState<StreamState>("disconnected");
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    async function loadRuns() {
      try {
        const data = await fetchInvestigationRuns(investigationId);
        if (!alive) {
          return;
        }
        setRuns(data);
        const preferred = initialRunId && data.find((item) => item.run_id === initialRunId) ? initialRunId : data[0]?.run_id ?? null;
        setSelectedRunId(preferred);
        setFeedback(null);
      } catch (error) {
        if (alive) {
          setFeedback(error instanceof Error ? error.message : "Failed to load runs");
          setRuns([]);
          setSelectedRunId(null);
        }
      }
    }

    void loadRuns();

    return () => {
      alive = false;
    };
  }, [investigationId, initialRunId]);

  useEffect(() => {
    let alive = true;
    if (!selectedRunId) {
      setRun(null);
      return;
    }
    const runId = selectedRunId;

    async function loadRun() {
      try {
        const data = await fetchInvestigationRun(investigationId, runId);
        if (!alive) {
          return;
        }
        setRun(data);
        setSelectedStageId((current) => current ?? data.current_stage ?? null);
      } catch (error) {
        if (alive) {
          setFeedback(error instanceof Error ? error.message : "Failed to load run details");
        }
      }
    }

    void loadRun();

    return () => {
      alive = false;
    };
  }, [investigationId, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) {
      setStreamState("disconnected");
      return;
    }
    const runId = selectedRunId;

    const url = getRunEventsUrl(investigationId, runId);
    const source = new EventSource(url);
    setStreamState("reconnecting");

    source.addEventListener("run_event", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as WorkflowRunEvent;
      if (payload.stage_id) {
        setSelectedStageId((current) => current ?? payload.stage_id);
      }
      setStreamState("connected");
      void fetchInvestigationRun(investigationId, runId)
        .then((data) => {
          setRun(data);
          setRuns((previous) => {
            const updated = previous.map((item) => (item.run_id === data.run_id ? data : item));
            return updated;
          });
        })
        .catch(() => {
          // Best-effort refresh while stream is active.
        });
    });

    source.addEventListener("heartbeat", () => {
      setStreamState("connected");
    });

    source.onerror = () => {
      setStreamState("reconnecting");
    };

    return () => {
      source.close();
      setStreamState("disconnected");
    };
  }, [investigationId, selectedRunId]);

  const selectedRunSummary = useMemo(
    () => runs.find((item) => item.run_id === selectedRunId) ?? null,
    [runs, selectedRunId]
  );

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-neutral-200 bg-white shadow-[0_10px_28px_rgba(23,23,23,0.08)]">
        <div className="border-b border-neutral-200 px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold tracking-[-0.02em] text-[#171717]">Workflow Execution: {incidentKey}</h3>
              <p className="text-xs text-[#676767]">Investigation {investigationId}</p>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${
                  streamState === "connected"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : streamState === "reconnecting"
                      ? "border-amber-200 bg-amber-50 text-amber-700"
                      : "border-neutral-200 bg-neutral-50 text-neutral-600"
                }`}
              >
                Stream: {streamState}
              </span>
              {selectedRunSummary ? <StatusBadge status={selectedRunSummary.status} /> : null}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-5 py-3">
          <label htmlFor="run-id" className="text-xs font-semibold uppercase tracking-[0.08em] text-neutral-600">
            Run
          </label>
          <select
            id="run-id"
            value={selectedRunId ?? ""}
            onChange={(event) => setSelectedRunId(event.target.value || null)}
            className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-[#171717] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#ff6000]"
          >
            {runs.map((item) => (
              <option key={item.run_id} value={item.run_id}>
                {item.run_id} · {item.status}
              </option>
            ))}
          </select>
          <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2 py-1 text-xs text-neutral-600">
            SSE updates enabled
          </span>
        </div>

        {feedback ? <p className="border-t border-neutral-200 px-5 py-3 text-sm text-rose-700">{feedback}</p> : null}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <WorkflowMapper run={run} selectedStageId={selectedStageId} onSelectStage={setSelectedStageId} />
        <WorkflowInspector run={run} selectedStageId={selectedStageId} />
      </div>

      <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-[0_10px_28px_rgba(23,23,23,0.08)]">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-[0.08em] text-neutral-600">Run Timeline</h3>
            <p className="text-xs text-[#676767]">Execution events in arrival order.</p>
          </div>
          <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2 py-1 text-xs font-semibold text-neutral-700">
            {run?.timeline.length ?? 0} events
          </span>
        </div>

        <ul className="space-y-2 text-sm text-[#171717]">
          {(run?.timeline ?? []).map((message, idx) => (
            <li key={`${message}-${idx}`} className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
              {message}
            </li>
          ))}
        </ul>
        {run?.timeline.length === 0 ? <p className="text-sm text-neutral-500">No timeline events yet.</p> : null}
      </div>
    </div>
  );
}
