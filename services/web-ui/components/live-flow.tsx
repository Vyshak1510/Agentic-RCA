"use client";

import { useEffect, useMemo, useState } from "react";

import { ExecutionWorkspace } from "@/components/execution/execution-workspace";
import { buildExecutionTrace } from "@/lib/execution-trace";
import { fetchInvestigation, fetchInvestigationRun, fetchInvestigationRuns, getRunEventsUrl, rerunInvestigation } from "@/lib/api";
import { RcaReport, WorkflowRunEvent, WorkflowRunSummary } from "@/lib/types";

type StreamState = "connected" | "reconnecting" | "disconnected";

type Props = {
  investigationId: string;
  incidentKey: string;
  initialRunId?: string | null;
  initialReport?: RcaReport | null;
};

export function LiveFlow({ investigationId, incidentKey, initialRunId = null, initialReport = null }: Props) {
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(initialRunId);
  const [run, setRun] = useState<Awaited<ReturnType<typeof fetchInvestigationRun>> | null>(null);
  const [report, setReport] = useState<RcaReport | null>(initialReport);
  const [streamState, setStreamState] = useState<StreamState>("disconnected");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [loadingRun, setLoadingRun] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [rerunLoading, setRerunLoading] = useState(false);
  const [rerunMessage, setRerunMessage] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    async function loadInvestigation() {
      try {
        const investigation = await fetchInvestigation(investigationId);
        if (!alive) {
          return;
        }
        setReport(investigation.report ?? null);
      } catch {
        if (alive) {
          setReport(null);
        }
      }
    }

    async function loadRuns() {
      setLoadingRuns(true);
      try {
        const [data] = await Promise.all([fetchInvestigationRuns(investigationId), loadInvestigation()]);
        if (!alive) {
          return;
        }
        setRuns(data);
        const preferred = initialRunId && data.find((item) => item.run_id === initialRunId) ? initialRunId : data[0]?.run_id ?? null;
        setSelectedRunId((current) => current ?? preferred);
        setFeedback(null);
      } catch (error) {
        if (alive) {
          setFeedback(error instanceof Error ? error.message : "Failed to load runs");
          setRuns([]);
          setSelectedRunId(null);
        }
      } finally {
        if (alive) {
          setLoadingRuns(false);
        }
      }
    }

    void loadRuns();
    return () => {
      alive = false;
    };
  }, [initialRunId, investigationId]);

  useEffect(() => {
    let alive = true;
    if (!selectedRunId) {
      setRun(null);
      return;
    }
    const runId = selectedRunId;

    async function loadRun() {
      setLoadingRun(true);
      try {
        const [data, investigation] = await Promise.all([
          fetchInvestigationRun(investigationId, runId),
          fetchInvestigation(investigationId),
        ]);
        if (!alive) {
          return;
        }
        setRun(data);
        setReport(investigation.report ?? null);
        setRuns((previous) => previous.map((item) => (item.run_id === data.run_id ? data : item)));
        setFeedback(null);
      } catch (error) {
        if (alive) {
          setFeedback(error instanceof Error ? error.message : "Failed to load run details");
        }
      } finally {
        if (alive) {
          setLoadingRun(false);
        }
      }
    }

    void loadRun();
    return () => {
      alive = false;
    };
  }, [investigationId, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId || !autoRefresh) {
      setStreamState("disconnected");
      return;
    }

    const runId = selectedRunId;
    const source = new EventSource(getRunEventsUrl(investigationId, runId));
    setStreamState("reconnecting");

    source.addEventListener("run_event", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as WorkflowRunEvent;
      setStreamState("connected");
      void Promise.all([fetchInvestigationRun(investigationId, runId), fetchInvestigation(investigationId)])
        .then(([data, investigation]) => {
          setRun(data);
          setReport(investigation.report ?? null);
          setRuns((previous) => previous.map((item) => (item.run_id === data.run_id ? data : item)));
          if (payload.run_status === "failed" || payload.run_status === "completed") {
            setStreamState("connected");
          }
        })
        .catch(() => {
          setStreamState("reconnecting");
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
  }, [autoRefresh, investigationId, selectedRunId]);

  const trace = useMemo(
    () => (run ? buildExecutionTrace(run, incidentKey, investigationId) : null),
    [incidentKey, investigationId, run]
  );

  async function handleRerun() {
    setRerunLoading(true);
    setRerunMessage(null);
    try {
      const response = await rerunInvestigation(investigationId);
      setRerunMessage(`Rerun requested for ${response.run_id}. Waiting for live updates.`);
      const refreshedRuns = await fetchInvestigationRuns(investigationId);
      setRuns(refreshedRuns);
      setSelectedRunId(response.run_id);
    } catch (error) {
      setRerunMessage(error instanceof Error ? error.message : "Rerun failed");
    } finally {
      setRerunLoading(false);
    }
  }

  return (
    <ExecutionWorkspace
      trace={trace}
      loading={loadingRuns || (selectedRunId !== null && loadingRun && run === null)}
      error={feedback}
      report={report}
      runs={runs}
      selectedRunId={selectedRunId}
      streamState={streamState}
      autoRefresh={autoRefresh}
      incidentKey={incidentKey}
      investigationId={investigationId}
      rerunLoading={rerunLoading}
      rerunMessage={rerunMessage}
      onSelectRunId={setSelectedRunId}
      onToggleAutoRefresh={() => setAutoRefresh((current) => !current)}
      onRerun={handleRerun}
    />
  );
}
