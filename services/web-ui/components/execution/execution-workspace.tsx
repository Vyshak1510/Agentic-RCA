"use client";

import clsx from "clsx";
import {
  ChevronLeft,
  ChevronRight,
  Expand,
  Pause,
  Play,
  RefreshCcw,
  Share2,
  SplitSquareVertical,
} from "lucide-react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { startTransition, useEffect, useMemo, useState } from "react";

import { EventInspector } from "@/components/execution/event-inspector";
import { ExecutionMapper } from "@/components/execution/execution-mapper";
import {
  EmptyState,
  FilterChip,
  SearchBar,
  SkeletonBlock,
  StatusPill,
  ToolbarButton,
} from "@/components/execution/execution-primitives";
import { RunTimeline } from "@/components/execution/run-timeline";
import {
  ExecutionRunTrace,
  TimelineFilter,
  TimelineMode,
  expandAllEventIds,
  formatCount,
  formatCurrency,
  formatDuration,
  getFailureEventIds,
  getSelectedEvent,
  safeStringify,
} from "@/lib/execution-trace";
import { RcaReport, WorkflowRunSummary } from "@/lib/types";

type StreamState = "connected" | "reconnecting" | "disconnected";
type TimeRange = "all" | "failure_window" | "delivery_window";

type Props = {
  trace: ExecutionRunTrace | null;
  loading: boolean;
  error: string | null;
  report: RcaReport | null;
  runs: WorkflowRunSummary[];
  selectedRunId: string | null;
  streamState: StreamState;
  autoRefresh: boolean;
  incidentKey: string;
  investigationId: string;
  rerunLoading: boolean;
  rerunMessage: string | null;
  onSelectRunId: (runId: string) => void;
  onToggleAutoRefresh: () => void;
  onRerun: () => void;
};

export function ExecutionWorkspace({
  trace,
  loading,
  error,
  report,
  runs,
  selectedRunId,
  streamState,
  autoRefresh,
  incidentKey,
  investigationId,
  rerunLoading,
  rerunMessage,
  onSelectRunId,
  onToggleAutoRefresh,
  onRerun,
}: Props) {
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [timelineMode, setTimelineMode] = useState<TimelineMode>("grouped");
  const [filters, setFilters] = useState<Set<TimelineFilter>>(new Set());
  const [expandedEventIds, setExpandedEventIds] = useState<Set<string>>(new Set());
  const [bookmarkedEventIds, setBookmarkedEventIds] = useState<Set<string>>(new Set());
  const [inspectorPinned, setInspectorPinned] = useState(true);
  const [compareOpen, setCompareOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [timeRange, setTimeRange] = useState<TimeRange>("all");
  const [environment, setEnvironment] = useState("prod-us-east-1");
  const [fitGraph, setFitGraph] = useState<(() => void) | null>(null);
  const [playing, setPlaying] = useState(false);

  const orderedEventIds = useMemo(
    () =>
      trace?.events
        .slice()
        .sort((left, right) => new Date(left.startedAt).getTime() - new Date(right.startedAt).getTime())
        .map((event) => event.id) ?? [],
    [trace]
  );

  const failureEventIds = useMemo(() => (trace ? getFailureEventIds(trace) : []), [trace]);
  const selectedEvent = trace ? getSelectedEvent(trace, selectedEventId) : null;

  useEffect(() => {
    if (!trace) {
      setSelectedEventId(null);
      setExpandedEventIds(new Set());
      return;
    }
    setEnvironment(trace.environment);
    setExpandedEventIds(expandAllEventIds(trace));
    setSelectedEventId((current) => {
      if (current && trace.events.some((event) => event.id === current)) {
        return current;
      }
      return trace.currentEventId ?? failureEventIds[0] ?? trace.events[0]?.id ?? null;
    });
  }, [failureEventIds, trace]);

  useEffect(() => {
    if (!playing || !trace || orderedEventIds.length === 0) {
      return;
    }
    const handle = window.setInterval(() => {
      setSelectedEventId((current) => {
        const currentIndex = current ? orderedEventIds.indexOf(current) : -1;
        const nextIndex = Math.min(orderedEventIds.length - 1, currentIndex + 1);
        if (nextIndex === orderedEventIds.length - 1) {
          setPlaying(false);
        }
        return orderedEventIds[nextIndex] ?? current;
      });
    }, 950);
    return () => window.clearInterval(handle);
  }, [orderedEventIds, playing, trace]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
        return;
      }
      if (!trace) {
        return;
      }
      if (event.key === "j") {
        event.preventDefault();
        jumpToNeighbor("next");
      }
      if (event.key === "k") {
        event.preventDefault();
        jumpToNeighbor("prev");
      }
      if (event.key === "[") {
        event.preventDefault();
        jumpToFailure("prev");
      }
      if (event.key === "]") {
        event.preventDefault();
        jumpToFailure("next");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const selectEvent = (eventId: string) => {
    startTransition(() => {
      setSelectedEventId(eventId);
    });
  };

  const toggleFilter = (filter: TimelineFilter) => {
    setFilters((current) => {
      const next = new Set(current);
      if (next.has(filter)) {
        next.delete(filter);
      } else {
        next.add(filter);
      }
      return next;
    });
  };

  const jumpToNeighbor = (direction: "prev" | "next") => {
    if (orderedEventIds.length === 0) {
      return;
    }
    const currentIndex = selectedEventId ? orderedEventIds.indexOf(selectedEventId) : 0;
    const nextIndex =
      direction === "next"
        ? Math.min(orderedEventIds.length - 1, currentIndex + 1)
        : Math.max(0, currentIndex - 1);
    selectEvent(orderedEventIds[nextIndex] ?? orderedEventIds[0]);
  };

  const jumpToFailure = (direction: "prev" | "next") => {
    if (failureEventIds.length === 0) {
      return;
    }
    const currentIndex = selectedEventId ? failureEventIds.indexOf(selectedEventId) : -1;
    const nextIndex =
      currentIndex === -1
        ? 0
        : direction === "next"
          ? (currentIndex + 1) % failureEventIds.length
          : (currentIndex - 1 + failureEventIds.length) % failureEventIds.length;
    selectEvent(failureEventIds[nextIndex] ?? failureEventIds[0]);
  };

  const toggleExpanded = (eventId: string) => {
    setExpandedEventIds((current) => {
      const next = new Set(current);
      if (next.has(eventId)) {
        next.delete(eventId);
      } else {
        next.add(eventId);
      }
      return next;
    });
  };

  const toggleBookmark = (eventId: string) => {
    setBookmarkedEventIds((current) => {
      const next = new Set(current);
      if (next.has(eventId)) {
        next.delete(eventId);
      } else {
        next.add(eventId);
      }
      return next;
    });
  };

  const exportTrace = () => {
    if (!trace) {
      return;
    }
    const payload = safeStringify(trace);
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${trace.runId}-trace.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const jumpToNode = (nodeId: string) => {
    if (!trace) {
      return;
    }
    const eventId = trace.nodes.find((node) => node.id === nodeId)?.eventIds[0];
    if (eventId) {
      selectEvent(eventId);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <header className="overflow-hidden rounded-[24px] border border-slate-200 bg-white/88 shadow-[0_18px_48px_rgba(15,23,42,0.08)] backdrop-blur-sm">
        <div className="border-b border-slate-200 px-5 py-3.5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">Execution Workspace</p>
                {trace ? <StatusPill status={trace.status} /> : null}
                <span
                  className={clsx(
                    "rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em]",
                    streamState === "connected"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : streamState === "reconnecting"
                        ? "border-amber-200 bg-amber-50 text-amber-700"
                        : "border-slate-200 bg-slate-100 text-slate-600"
                  )}
                >
                  {streamState}
                </span>
              </div>
              <h2 className="mt-2 text-xl font-semibold tracking-[-0.03em] text-slate-900">{incidentKey}</h2>
              <p className="mt-1 text-sm text-slate-600">
                Workflow: {trace?.workflowTitle ?? "Agentic RCA Workflow"} · Investigation {investigationId}
              </p>
            </div>

            <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
              <label className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white/85 px-3 py-2 text-sm text-slate-600">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Run</span>
                <select
                  value={selectedRunId ?? ""}
                  onChange={(event) => onSelectRunId(event.target.value)}
                  className="bg-transparent text-sm text-slate-900 focus:outline-none"
                >
                  {runs.map((run) => (
                    <option key={run.run_id} value={run.run_id} className="bg-white text-slate-900">
                      {run.run_id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white/85 px-3 py-2 text-sm text-slate-600">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Env</span>
                <select
                  value={environment}
                  onChange={(event) => setEnvironment(event.target.value)}
                  className="bg-transparent text-sm text-slate-900 focus:outline-none"
                >
                  {["prod-us-east-1", "staging-us-east-1", "dev-local"].map((item) => (
                    <option key={item} value={item} className="bg-white text-slate-900">
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <SearchBar value={query} onChange={setQuery} placeholder="Search nodes, event IDs, logs" className="min-w-[220px] max-w-[320px] flex-1" />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
          <div className="flex flex-wrap items-center gap-2">
            {[
              ["errors", "Only errors"],
              ["model_calls", "Model calls"],
              ["tool_calls", "Tool calls"],
              ["user_visible", "User-visible"],
              ["long_running", "Long-running"],
            ].map(([value, label]) => (
              <FilterChip key={value} active={filters.has(value as TimelineFilter)} onClick={() => toggleFilter(value as TimelineFilter)}>
                {label}
              </FilterChip>
            ))}

            <label className="ml-2 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-3 py-1.5 text-xs text-slate-600">
              <span className="uppercase tracking-[0.16em] text-slate-500">Time</span>
              <select
                value={timeRange}
                onChange={(event) => setTimeRange(event.target.value as TimeRange)}
                className="bg-transparent text-xs text-slate-900 focus:outline-none"
              >
                <option value="all" className="bg-white">Full run</option>
                <option value="failure_window" className="bg-white">Failure window</option>
                <option value="delivery_window" className="bg-white">Delivery only</option>
              </select>
            </label>

            <ToolbarButton active={autoRefresh} onClick={onToggleAutoRefresh} className="px-3 py-2 text-xs">
              <RefreshCcw className="h-4 w-4" />
              Auto-refresh
            </ToolbarButton>
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2">
            <ToolbarButton onClick={() => fitGraph?.()} className="px-3 py-2 text-xs">
              <Expand className="h-4 w-4" />
              Fit graph
            </ToolbarButton>
            <ToolbarButton active={compareOpen} onClick={() => setCompareOpen((current) => !current)} className="px-3 py-2 text-xs">
              <SplitSquareVertical className="h-4 w-4" />
              Compare previous run
            </ToolbarButton>
            <ToolbarButton onClick={exportTrace} className="px-3 py-2 text-xs">
              <Share2 className="h-4 w-4" />
              Share / export
            </ToolbarButton>
            <ToolbarButton onClick={onRerun} disabled={rerunLoading} className="px-3 py-2 text-xs">
              <RefreshCcw className="h-4 w-4" />
              {rerunLoading ? "Re-running..." : "Replay / re-run"}
            </ToolbarButton>
            <ToolbarButton onClick={() => jumpToNeighbor("prev")} className="px-3 py-2 text-xs">
              <ChevronLeft className="h-4 w-4" />
            </ToolbarButton>
            <ToolbarButton onClick={() => setPlaying((current) => !current)} className="px-3 py-2 text-xs">
              {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              {playing ? "Pause" : "Play"}
            </ToolbarButton>
            <ToolbarButton onClick={() => jumpToNeighbor("next")} className="px-3 py-2 text-xs">
              <ChevronRight className="h-4 w-4" />
            </ToolbarButton>
            <div className="hidden items-center gap-3 rounded-full border border-slate-200 bg-white/70 px-3 py-1.5 text-[11px] text-slate-500 xl:flex">
              <span className="font-medium text-slate-700">Keyboard</span>
              <span>`j` next</span>
              <span>`k` prev</span>
              <span>`[` failure</span>
              <span>`]` next</span>
            </div>
          </div>
        </div>

        {compareOpen && trace?.compare ? (
          <div className="grid gap-3 border-t border-slate-200 bg-[#faf7f2] px-6 py-4 md:grid-cols-4">
            <CompareCard label="Duration regression" value={formatDuration(trace.compare.durationDeltaMs)} />
            <CompareCard label="Cost regression" value={formatCurrency(trace.compare.costDeltaUsd)} />
            <CompareCard label="Token delta" value={formatCount(trace.compare.tokenDelta)} />
            <CompareCard label="Regression nodes" value={trace.compare.regressionNodeIds.join(", ")} />
          </div>
        ) : null}

        {rerunMessage ? <div className="border-t border-slate-200 bg-orange-50/60 px-6 py-3 text-sm text-orange-700">{rerunMessage}</div> : null}
      </header>

      {!loading && !error && report ? (
        <section className="rounded-[24px] border border-orange-200 bg-[#fff8ef] px-5 py-4 shadow-[0_18px_40px_rgba(249,115,22,0.08)]">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-orange-700">Final RCA</p>
                <span className="rounded-full border border-orange-200 bg-white/80 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-orange-700">
                  Published Output
                </span>
              </div>
              <p className="mt-2 text-sm font-semibold text-slate-900">{report.likely_cause}</p>
              <p className="mt-1 text-sm text-slate-600">Blast radius: {report.blast_radius}</p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <ToolbarButton onClick={() => jumpToNode("synthesize_rca_report")} className="px-3 py-2 text-xs">
                Jump to draft
              </ToolbarButton>
              <ToolbarButton onClick={() => jumpToNode("publish_report")} className="px-3 py-2 text-xs">
                Jump to publish
              </ToolbarButton>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-3">
            {report.recommended_manual_actions.slice(0, 3).map((action) => (
              <div key={action} className="rounded-[18px] border border-orange-100 bg-white/75 px-4 py-3 text-sm text-slate-700">
                {action}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {loading ? <LoadingWorkspace /> : null}
      {!loading && error ? (
        <div className="rounded-[28px] border border-slate-200 bg-white/88 p-5 shadow-[0_18px_48px_rgba(15,23,42,0.08)]">
          <EmptyState title="Unable to load run" body={error} />
        </div>
      ) : null}

      {!loading && !error && trace ? (
        <div className="min-h-[780px] min-w-0 flex-1 overflow-hidden rounded-[28px] border border-slate-200 bg-white/70 shadow-[0_18px_52px_rgba(15,23,42,0.08)] backdrop-blur-sm">
          <Group orientation="horizontal" className="h-full min-h-0">
            <Panel defaultSize={66} minSize={48} className="min-h-0" style={{ overflow: "hidden" }}>
              <Group orientation="vertical" className="h-full min-h-0">
                <Panel defaultSize={50} minSize={44} className="min-h-0" style={{ overflow: "hidden" }}>
                  <div className="h-full min-h-0 p-3">
                    <ExecutionMapper trace={trace} selectedEventId={selectedEventId} onSelectEvent={selectEvent} onFitReady={setFitGraph} />
                  </div>
                </Panel>
                <ResizeHandle direction="horizontal" />
                <Panel defaultSize={50} minSize={36} className="min-h-0" style={{ overflow: "hidden" }}>
                  <div className="h-full min-h-0 p-3 pt-0">
                    <RunTimeline
                      trace={trace}
                      selectedEventId={selectedEventId}
                      currentEventId={trace.currentEventId}
                      timeRange={timeRange}
                      filters={filters}
                      search={query}
                      mode={timelineMode}
                      expandedEventIds={expandedEventIds}
                      bookmarkedEventIds={bookmarkedEventIds}
                      onSelectEvent={selectEvent}
                      onModeChange={setTimelineMode}
                      onToggleExpanded={toggleExpanded}
                      onExpandAll={() => setExpandedEventIds(expandAllEventIds(trace))}
                      onCollapseAll={() => setExpandedEventIds(new Set())}
                      onToggleBookmark={toggleBookmark}
                    />
                  </div>
                </Panel>
              </Group>
            </Panel>
            <ResizeHandle direction="vertical" />
            <Panel defaultSize={34} minSize={32} className="min-h-0" style={{ overflow: "hidden" }}>
              <div className="h-full min-h-0 p-3 pl-0">
                <EventInspector
                  trace={trace}
                  selectedEvent={selectedEvent}
                  pinned={inspectorPinned}
                  bookmarkedEventIds={bookmarkedEventIds}
                  onSelectEvent={selectEvent}
                  onTogglePinned={() => setInspectorPinned((current) => !current)}
                  onToggleBookmark={toggleBookmark}
                  onJumpFailure={jumpToFailure}
                />
              </div>
            </Panel>
          </Group>
        </div>
      ) : null}

      {!loading && !error && !trace ? (
        <div className="rounded-[28px] border border-slate-200 bg-white/88 p-5 shadow-[0_18px_48px_rgba(15,23,42,0.08)]">
          <EmptyState
            title="No execution data available"
            body="Select a run or trigger a new execution to populate the mapper, inspector, and timeline."
          />
        </div>
      ) : null}
    </div>
  );
}

function ResizeHandle({ direction }: { direction: "horizontal" | "vertical" }) {
  return (
    <Separator
      className={clsx(
        "group relative flex items-center justify-center bg-transparent",
        direction === "horizontal" ? "h-4" : "w-4"
      )}
    >
      <div
        className={clsx(
          "rounded-full bg-slate-300/80 transition group-hover:bg-orange-300",
          direction === "horizontal" ? "h-[2px] w-20" : "h-20 w-[2px]"
        )}
      />
    </Separator>
  );
}

function CompareCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[18px] border border-slate-200 bg-white/75 px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</p>
      <p className="mt-2 text-sm font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function LoadingWorkspace() {
  return (
    <div className="grid min-h-[780px] flex-1 gap-4 rounded-[30px] border border-slate-200 bg-white/70 p-4 shadow-[0_18px_48px_rgba(15,23,42,0.08)] md:grid-cols-[minmax(0,1fr)_420px]">
      <div className="grid min-h-0 gap-4">
        <SkeletonBlock className="min-h-[380px]" />
        <SkeletonBlock className="min-h-[320px]" />
      </div>
      <SkeletonBlock className="min-h-[640px]" />
    </div>
  );
}
