"use client";

import clsx from "clsx";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Bookmark,
  ChevronDown,
  ChevronRight,
  Clock3,
  ListTree,
  Rows3,
  ScanSearch,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { PanelFrame, StatusPill, ToolbarButton, getEventKindIcon } from "@/components/execution/execution-primitives";
import {
  ExecutionRunTrace,
  TimelineFilter,
  TimelineMode,
  buildTimelineRows,
  formatDuration,
  formatTimestamp,
  getRcaRoleForNodeId,
} from "@/lib/execution-trace";

type Props = {
  trace: ExecutionRunTrace;
  selectedEventId: string | null;
  currentEventId?: string;
  timeRange: "all" | "failure_window" | "delivery_window";
  filters: Set<TimelineFilter>;
  search: string;
  mode: TimelineMode;
  expandedEventIds: Set<string>;
  bookmarkedEventIds: Set<string>;
  onSelectEvent: (eventId: string) => void;
  onModeChange: (mode: TimelineMode) => void;
  onToggleExpanded: (eventId: string) => void;
  onExpandAll: () => void;
  onCollapseAll: () => void;
  onToggleBookmark: (eventId: string) => void;
};

export function RunTimeline({
  trace,
  selectedEventId,
  currentEventId,
  timeRange,
  filters,
  search,
  mode,
  expandedEventIds,
  bookmarkedEventIds,
  onSelectEvent,
  onModeChange,
  onToggleExpanded,
  onExpandAll,
  onCollapseAll,
  onToggleBookmark,
}: Props) {
  const [pinBookmarkedFirst, setPinBookmarkedFirst] = useState(false);
  const parentRef = useRef<HTMLDivElement | null>(null);

  const rows = useMemo(
    () => {
      const baseRows = buildTimelineRows(trace, mode, expandedEventIds, filters, search, bookmarkedEventIds, pinBookmarkedFirst);
      if (timeRange === "all") {
        return baseRows;
      }
      if (timeRange === "failure_window") {
        const firstFailure = trace.events.find((event) => event.status === "failed");
        if (!firstFailure) {
          return baseRows;
        }
        const cutoff = new Date(firstFailure.startedAt).getTime() - 2500;
        return baseRows.filter((row) => new Date(row.event.startedAt).getTime() >= cutoff);
      }
      return baseRows.filter((row) =>
        ["publish_report", "slack_publish", "jira_publish", "emit_eval_event"].includes(row.event.nodeId)
      );
    },
    [bookmarkedEventIds, expandedEventIds, filters, mode, pinBookmarkedFirst, search, timeRange, trace]
  );

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => (mode === "grouped" ? 84 : 72),
    measureElement: (element) => element?.getBoundingClientRect().height ?? 0,
    overscan: 8,
  });

  return (
    <PanelFrame
      title="Run Timeline"
      subtitle="Chronological run trace"
      className="h-full"
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <ToolbarButton active={mode === "grouped"} onClick={() => onModeChange("grouped")} className="px-2.5 py-2 text-xs">
            <ListTree className="h-4 w-4" />
            Grouped
          </ToolbarButton>
          <ToolbarButton active={mode === "flat"} onClick={() => onModeChange("flat")} className="px-2.5 py-2 text-xs">
            <Rows3 className="h-4 w-4" />
            Flat
          </ToolbarButton>
          <ToolbarButton onClick={onExpandAll} className="px-2.5 py-2 text-xs">
            Expand all
          </ToolbarButton>
          <ToolbarButton onClick={onCollapseAll} className="px-2.5 py-2 text-xs">
            Collapse all
          </ToolbarButton>
          <ToolbarButton active={pinBookmarkedFirst} onClick={() => setPinBookmarkedFirst((current) => !current)} className="px-2.5 py-2 text-xs">
            <Bookmark className="h-4 w-4" />
            Pinned first
          </ToolbarButton>
        </div>
      }
    >
      <div ref={parentRef} className="h-full min-h-0 overflow-auto px-3 py-3">
        {rows.length === 0 ? (
          <div className="flex h-full min-h-[180px] flex-col items-center justify-center rounded-[20px] border border-dashed border-slate-200 bg-[#fcfbf8] text-center">
            <ScanSearch className="h-6 w-6 text-slate-500" />
            <p className="mt-3 text-sm font-semibold text-slate-900">No events match the current filters.</p>
            <p className="mt-1 text-sm text-slate-500">Try clearing filters or switching from grouped to flat mode.</p>
          </div>
        ) : (
          <div className="relative" style={{ height: `${virtualizer.getTotalSize()}px` }}>
            {virtualizer.getVirtualItems().map((virtualItem) => {
              const row = rows[virtualItem.index];
              const Icon = getEventKindIcon(row.event.kind);
              const selected = selectedEventId === row.eventId;
              const live = currentEventId === row.eventId;
              const rcaRole = getRcaRoleForNodeId(row.event.nodeId);
              return (
                <div
                  key={row.id}
                  ref={virtualizer.measureElement}
                  data-index={virtualItem.index}
                  className="absolute left-0 top-0 w-full px-1 py-1"
                  style={{ transform: `translateY(${virtualItem.start}px)` }}
                >
                  <div
                    className={clsx(
                      "group relative rounded-[16px] border px-2.5 py-2 transition",
                      selected
                        ? "border-orange-300 bg-orange-50 shadow-[0_0_0_1px_rgba(249,115,22,0.12)]"
                        : "border-slate-200 bg-white/80 hover:border-slate-300 hover:bg-white"
                    )}
                  >
                    {live ? <span className="absolute bottom-3 left-3 top-3 w-[2px] rounded-full bg-orange-400" /> : null}
                    <div className="ml-2 flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-start gap-3" style={{ paddingLeft: row.depth * 20 }}>
                        {row.hasChildren && mode === "grouped" ? (
                          <button
                            type="button"
                            onClick={() => onToggleExpanded(row.eventId)}
                            className="mt-0.5 rounded-full border border-slate-200 bg-white/80 p-1 text-slate-500 hover:text-slate-900"
                          >
                            {row.isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                          </button>
                        ) : (
                          <span className="mt-1.5 h-2 w-2 rounded-full bg-slate-700" />
                        )}
                        <button type="button" onClick={() => onSelectEvent(row.eventId)} className="flex min-w-0 items-start gap-3 text-left">
                          <div className="rounded-xl border border-orange-100 bg-orange-50 p-1.5 text-orange-700">
                            <Icon className="h-3.5 w-3.5" />
                          </div>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="text-[13px] font-semibold leading-5 text-slate-900">{row.node?.label ?? row.event.nodeId}</p>
                              <span className="text-xs uppercase tracking-[0.16em] text-slate-500">{row.event.kind.replaceAll("_", " ")}</span>
                              {rcaRole ? (
                                <span className="rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-orange-700">
                                  {rcaRole === "final" ? "Final RCA" : "RCA Draft"}
                                </span>
                              ) : null}
                              {live ? <span className="rounded-full bg-orange-100 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-orange-700">playhead</span> : null}
                            </div>
                            <p className="mt-0.5 line-clamp-1 pr-2 text-[12px] leading-[18px] text-slate-600">{row.event.description}</p>
                            <div className="mt-1 flex flex-wrap items-center gap-3 text-[10px] text-slate-500">
                              <span>{formatTimestamp(row.event.startedAt)}</span>
                              <span className="inline-flex items-center gap-1">
                                <Clock3 className="h-3.5 w-3.5" />
                                {formatDuration(row.event.durationMs)}
                              </span>
                              <span className={clsx("transition-opacity", selected ? "opacity-100" : "opacity-0 group-hover:opacity-100")}>
                                {row.event.id}
                              </span>
                            </div>
                          </div>
                        </button>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => onToggleBookmark(row.eventId)}
                          className={clsx(
                            "rounded-full border p-1.5 transition",
                            bookmarkedEventIds.has(row.eventId)
                              ? "border-orange-200 bg-orange-50 text-orange-700"
                              : "border-slate-200 bg-white/80 text-slate-500 hover:text-slate-900"
                          )}
                        >
                          <Bookmark className="h-3.5 w-3.5" />
                        </button>
                        <StatusPill status={row.event.status} className="px-2 py-0.5 text-[10px]" />
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </PanelFrame>
  );
}
