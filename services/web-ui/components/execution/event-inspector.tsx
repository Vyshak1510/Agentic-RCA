"use client";

import clsx from "clsx";
import {
  ArrowDownUp,
  ArrowLeft,
  ArrowRight,
  Bookmark,
  Check,
  ChevronRight,
  Clock3,
  Copy,
  Cpu,
  Link2,
  Network,
  PanelRight,
  Pin,
  ScrollText,
  TriangleAlert,
} from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";

import { JsonViewer } from "@/components/execution/json-viewer";
import { LogViewer } from "@/components/execution/log-viewer";
import {
  EmptyState,
  MetadataRow,
  PanelFrame,
  StatusPill,
  Tabs,
  ToolbarButton,
  getEventKindIcon,
} from "@/components/execution/execution-primitives";
import {
  ExecutionEvent,
  ExecutionRunTrace,
  formatCount,
  formatCurrency,
  formatDateTime,
  formatDuration,
  getChildEvents,
  getEventChain,
  getFailureEventIds,
  getNodeMap,
  getRcaRoleForNodeId,
} from "@/lib/execution-trace";

type InspectorTab = "overview" | "input" | "output" | "events" | "logs" | "metrics" | "raw";

type Props = {
  trace: ExecutionRunTrace;
  selectedEvent: ExecutionEvent | null;
  pinned: boolean;
  bookmarkedEventIds: Set<string>;
  onSelectEvent: (eventId: string) => void;
  onTogglePinned: () => void;
  onToggleBookmark: (eventId: string) => void;
  onJumpFailure: (direction: "prev" | "next") => void;
};

function toneForMetric(value: number | undefined, max = 1): string {
  if (!value) {
    return "bg-slate-200";
  }
  const ratio = Math.min(1, value / max);
  if (ratio > 0.7) {
    return "bg-rose-400";
  }
  if (ratio > 0.4) {
    return "bg-amber-400";
  }
  return "bg-orange-400";
}

export function EventInspector({
  trace,
  selectedEvent,
  pinned,
  bookmarkedEventIds,
  onSelectEvent,
  onTogglePinned,
  onToggleBookmark,
  onJumpFailure,
}: Props) {
  const [tab, setTab] = useState<InspectorTab>("overview");
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const nodeMap = useMemo(() => getNodeMap(trace), [trace]);
  const event = selectedEvent;
  const childEvents = event ? getChildEvents(trace, event.id) : [];
  const breadcrumbs = event ? getEventChain(trace, event.id) : [];
  const node = event ? nodeMap.get(event.nodeId) ?? null : null;
  const failureCount = getFailureEventIds(trace).length;
  const rcaRole = node ? getRcaRoleForNodeId(node.id) : null;

  if (!event || !node) {
    return (
      <PanelFrame title="Event Inspector" subtitle="Select a node or timeline row to inspect payloads, logs, and metrics." className="h-full min-h-0">
        <div className="p-5">
          <EmptyState title="No event selected" body="Pick a node from the mapper or a row from the timeline to inspect its trace." />
        </div>
      </PanelFrame>
    );
  }

  const Icon = getEventKindIcon(event.kind);

  async function handleCopy(key: string, value: string) {
    await navigator.clipboard.writeText(value);
    setCopiedField(key);
    window.setTimeout(() => {
      setCopiedField((current) => (current === key ? null : current));
    }, 1200);
  }

  return (
    <PanelFrame
      title="Event Inspector"
      subtitle="Detailed trace view for the selected event"
      className="h-full min-h-0"
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <ToolbarButton onClick={() => onJumpFailure("prev")} className="px-2.5 py-2 text-xs">
            <ArrowLeft className="h-4 w-4" />
            Prev failure
          </ToolbarButton>
          <ToolbarButton onClick={() => onJumpFailure("next")} className="px-2.5 py-2 text-xs">
            Next failure
            <ArrowRight className="h-4 w-4" />
          </ToolbarButton>
          <ToolbarButton active={pinned} onClick={onTogglePinned} className="px-2.5 py-2 text-xs">
            <Pin className="h-4 w-4" />
            {pinned ? "Pinned" : "Pin"}
          </ToolbarButton>
        </div>
      }
    >
      <div className="flex h-full min-h-0 flex-col overflow-auto">
        <div className="border-b border-slate-200 px-5 py-3.5">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            {breadcrumbs.map((crumb, index) => (
              <button
                key={crumb.id}
                type="button"
                onClick={() => onSelectEvent(crumb.id)}
                className="inline-flex max-w-full items-center gap-2 rounded-full border border-slate-200 bg-white/80 px-2.5 py-1 hover:border-slate-300 hover:text-slate-900"
              >
                {index > 0 ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-600" /> : null}
                <span className="truncate">{crumb.title}</span>
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
              <div className="min-w-0">
                <div className="flex items-start gap-3">
                  <div className="rounded-2xl border border-orange-100 bg-orange-50 p-2 text-orange-700">
                    <Icon className="h-5 w-5" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="truncate text-xl font-semibold text-slate-900">{event.title}</h3>
                    <p className="mt-1 line-clamp-2 text-sm text-slate-600">{event.description}</p>
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 xl:justify-end">
                {rcaRole ? (
                  <span className="rounded-full border border-orange-200 bg-orange-50 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-orange-700">
                    {rcaRole === "final" ? "Final RCA" : "RCA Draft"}
                  </span>
                ) : null}
                <StatusPill status={event.status} />
                <ToolbarButton active={bookmarkedEventIds.has(event.id)} onClick={() => onToggleBookmark(event.id)} className="px-2.5 py-2 text-xs">
                  <Bookmark className="h-4 w-4" />
                  {bookmarkedEventIds.has(event.id) ? "Bookmarked" : "Bookmark"}
                </ToolbarButton>
                <span className="rounded-full border border-slate-200 bg-white/80 px-2.5 py-1 text-[11px] font-medium text-slate-600">
                  {failureCount} failure{failureCount === 1 ? "" : "s"}
                </span>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <CompactMetaCell label="Node" value={node.label} />
              <CompactMetaCell label="Type" value={event.kind.replaceAll("_", " ")} />
              <CompactMetaCell label="Duration" value={formatDuration(event.durationMs)} />
              <CompactMetaCell
                label="Run ID"
                value={trace.runId}
                valueClassName="font-mono text-[12px] [overflow-wrap:anywhere]"
                copyState={copiedField === "run-id"}
                onCopy={() => void handleCopy("run-id", trace.runId)}
              />
              <CompactMetaCell
                label="Event ID"
                value={event.id}
                valueClassName="font-mono text-[12px] [overflow-wrap:anywhere]"
                copyState={copiedField === "event-id"}
                onCopy={() => void handleCopy("event-id", event.id)}
              />
            </div>
          </div>
        </div>

        <div className="border-b border-slate-200 px-5 py-3">
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { value: "overview", label: "Overview" },
              { value: "input", label: "Input" },
              { value: "output", label: "Output" },
              { value: "events", label: "Events", count: childEvents.length },
              { value: "logs", label: "Logs", count: event.logs.length },
              { value: "metrics", label: "Metrics" },
              { value: "raw", label: "Raw JSON" },
            ]}
          />
        </div>

        <div className="min-h-0 flex-1">
          {tab === "overview" ? (
            <div className="px-5 py-4">
              <OverviewTab
                trace={trace}
                event={event}
                node={node}
                childEvents={childEvents}
                onSelectEvent={onSelectEvent}
              />
            </div>
          ) : null}

          {tab === "input" ? (
            <div className="px-5 py-4">
              <JsonViewer value={event.input} title="input-payload" scrollMode="outer" />
            </div>
          ) : null}

          {tab === "output" ? (
            <div className="px-5 py-4">
              <JsonViewer
                value={event.output}
                compareValue={event.input}
                truncated={event.stream?.truncated}
                title="output-payload"
                scrollMode="outer"
              />
            </div>
          ) : null}

          {tab === "events" ? (
            <div className="px-5 py-4">
              <ChildEventsPanel trace={trace} event={event} onSelectEvent={onSelectEvent} />
            </div>
          ) : null}

          {tab === "logs" ? (
            <div className="px-5 py-4">
              <LogViewer logs={event.logs} scrollMode="outer" />
            </div>
          ) : null}

          {tab === "metrics" ? (
            <div className="px-5 py-4">
              <MetricsPanel event={event} />
            </div>
          ) : null}

          {tab === "raw" ? (
            <div className="px-5 py-4">
              <JsonViewer value={event.raw} allowDownload title="raw-trace" scrollMode="outer" />
            </div>
          ) : null}
        </div>
      </div>
    </PanelFrame>
  );
}

function CompactMetaCell({
  label,
  value,
  onCopy,
  copyState = false,
  valueClassName,
}: {
  label: string;
  value: string;
  onCopy?: () => void;
  copyState?: boolean;
  valueClassName?: string;
}) {
  return (
    <div className="min-w-0 rounded-[16px] border border-slate-200 bg-white/80 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</p>
        {onCopy ? (
          <button
            type="button"
            onClick={onCopy}
            className="rounded-md border border-slate-200 bg-white px-1.5 py-1 text-slate-500 hover:text-slate-900"
          >
            {copyState ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
        ) : null}
      </div>
      <p
        className={clsx("mt-1 min-w-0 whitespace-normal break-words text-sm font-medium leading-5 text-slate-900", valueClassName)}
        title={value}
      >
        {value}
      </p>
    </div>
  );
}

function OverviewTab({
  trace,
  event,
  node,
  childEvents,
  onSelectEvent,
}: {
  trace: ExecutionRunTrace;
  event: ExecutionEvent;
  node: ReturnType<typeof getNodeMap> extends Map<string, infer T> ? T : never;
  childEvents: ExecutionEvent[];
  onSelectEvent: (eventId: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="rounded-[20px] border border-slate-200 bg-[#fcfbf8] p-4">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
          <ScrollText className="h-4 w-4" />
          Execution summary
        </div>
        <p className="text-sm leading-6 text-slate-700">{event.description}</p>
        <div className="mt-4 grid gap-3">
          <dl className="rounded-[18px] border border-slate-200 bg-white/75 px-4 py-3">
            <MetadataRow label="Node" value={node.label} emphasis />
            <MetadataRow label="Status" value={<StatusPill status={event.status} />} />
            <MetadataRow label="Type" value={event.kind.replaceAll("_", " ")} />
            <MetadataRow label="Model / tool" value={event.model ?? event.tool ?? node.model ?? node.tool ?? "-"} />
          </dl>
          <dl className="rounded-[18px] border border-slate-200 bg-white/75 px-4 py-3">
            <MetadataRow label="Run ID" value={trace.runId} />
            <MetadataRow label="Event ID" value={event.id} />
            <MetadataRow label="Start" value={formatDateTime(event.startedAt)} />
            <MetadataRow label="End" value={formatDateTime(event.endedAt)} />
          </dl>
        </div>

        {event.error ? (
          <div className="mt-4 rounded-[18px] border border-rose-200 bg-rose-50 p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-rose-700">
              <TriangleAlert className="h-4 w-4" />
              {event.error.summary}
            </div>
            <p className="mt-2 text-sm text-rose-700/90">{event.error.message}</p>
            {event.error.rootCauseHints.length > 0 ? (
              <ul className="mt-3 space-y-2 text-sm text-rose-800/90">
                {event.error.rootCauseHints.map((hint) => (
                  <li key={hint} className="rounded-2xl border border-rose-200 bg-white/70 px-3 py-2">
                    {hint}
                  </li>
                ))}
              </ul>
            ) : null}
            {event.error.stack?.length ? (
              <pre className="mt-3 overflow-auto rounded-2xl border border-rose-200 bg-white/75 px-3 py-3 text-xs leading-6 text-rose-800/90">
                {event.error.stack.join("\n")}
              </pre>
            ) : null}
            {event.error.relatedEventIds?.length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {event.error.relatedEventIds.map((relatedId) => (
                  <ToolbarButton key={relatedId} onClick={() => onSelectEvent(relatedId)} className="px-2.5 py-1.5 text-xs">
                    Related: {relatedId}
                  </ToolbarButton>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="grid gap-4">
        <div className="rounded-[20px] border border-slate-200 bg-[#fcfbf8] p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
            <Clock3 className="h-4 w-4" />
            Latency breakdown
          </div>
          <LatencyBreakdown event={event} />
        </div>
        <div className="rounded-[20px] border border-slate-200 bg-[#fcfbf8] p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
            <ArrowDownUp className="h-4 w-4" />
            Run relationships
          </div>
          <div className="space-y-2 text-sm text-slate-700">
            <LinkedNodeRow label="Upstream" nodeIds={node.upstream} trace={trace} onSelectEvent={onSelectEvent} />
            <LinkedNodeRow label="Downstream" nodeIds={node.downstream} trace={trace} onSelectEvent={onSelectEvent} />
            <LinkedNodeRow label="Child events" eventIds={childEvents.map((item) => item.id)} trace={trace} onSelectEvent={onSelectEvent} />
          </div>
        </div>
      </div>

      <div className="grid gap-4">
        <MetricCard icon={<Clock3 className="h-4 w-4" />} label="Queue time" value={formatDuration(event.metrics.queueMs)} />
        <MetricCard icon={<Cpu className="h-4 w-4" />} label="Execution" value={formatDuration(event.metrics.executionMs ?? event.durationMs)} />
        <MetricCard icon={<Network className="h-4 w-4" />} label="Network / Tool" value={formatDuration((event.metrics.networkMs ?? 0) + (event.metrics.toolMs ?? 0))} />
        <MetricCard icon={<Link2 className="h-4 w-4" />} label="Tokens in / out" value={`${formatCount(event.metrics.tokenInput)} / ${formatCount(event.metrics.tokenOutput)}`} />
        <MetricCard icon={<PanelRight className="h-4 w-4" />} label="Cost" value={formatCurrency(event.metrics.costUsd)} />
        <MetricCard icon={<Bookmark className="h-4 w-4" />} label="Child steps" value={String(childEvents.length)} />
      </div>
    </div>
  );
}

function LinkedNodeRow({
  label,
  nodeIds,
  eventIds,
  trace,
  onSelectEvent,
}: {
  label: string;
  nodeIds?: string[];
  eventIds?: string[];
  trace: ExecutionRunTrace;
  onSelectEvent: (eventId: string) => void;
}) {
  const nodeMap = getNodeMap(trace);
  const items =
    nodeIds?.map((nodeId) => {
      const node = nodeMap.get(nodeId);
      return node ? { id: nodeId, label: node.label, eventId: node.eventIds[0] } : null;
    }).filter((item): item is { id: string; label: string; eventId: string } => Boolean(item)) ??
    eventIds?.map((eventId) => ({ id: eventId, label: eventId, eventId })) ??
    [];

  return (
    <div>
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</p>
      <div className="flex flex-wrap gap-2">
        {items.length === 0 ? (
          <span className="rounded-full border border-slate-200 bg-white/80 px-2.5 py-1 text-xs text-slate-500">None</span>
        ) : (
          items.map((item) => (
            <ToolbarButton key={item.id} onClick={() => onSelectEvent(item.eventId)} className="max-w-full px-2.5 py-1.5 text-xs">
              <span className="truncate">{item.label}</span>
            </ToolbarButton>
          ))
        )}
      </div>
    </div>
  );
}

function MetricCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-[18px] border border-slate-200 bg-[#fcfbf8] p-4">
      <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-slate-500">
        {icon}
        {label}
      </div>
      <p className="mt-2 text-sm font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function LatencyBreakdown({ event }: { event: ExecutionEvent }) {
  const parts = [
    { label: "Queue", value: event.metrics.queueMs ?? 0 },
    { label: "Execution", value: event.metrics.executionMs ?? event.durationMs },
    { label: "Network", value: event.metrics.networkMs ?? 0 },
    { label: "Tool", value: event.metrics.toolMs ?? 0 },
    { label: "Model", value: event.metrics.modelMs ?? 0 },
  ].filter((part) => part.value > 0);
  const max = Math.max(...parts.map((part) => part.value), 1);

  return (
    <div className="space-y-3">
      {parts.map((part) => (
        <div key={part.label}>
          <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
            <span>{part.label}</span>
            <span>{formatDuration(part.value)}</span>
          </div>
          <div className="h-2 rounded-full bg-slate-200">
            <div className={clsx("h-2 rounded-full", toneForMetric(part.value, max))} style={{ width: `${Math.max(8, (part.value / max) * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function ChildEventsPanel({
  trace,
  event,
  onSelectEvent,
}: {
  trace: ExecutionRunTrace;
  event: ExecutionEvent;
  onSelectEvent: (eventId: string) => void;
}) {
  const children = getChildEvents(trace, event.id);

  if (children.length === 0) {
    return <EmptyState title="No child events" body="This event does not have nested tool calls, model calls, or sub-steps." />;
  }

  return (
    <div className="space-y-3">
      {children.map((child) => {
        const Icon = getEventKindIcon(child.kind);
        return (
          <button
            key={child.id}
            type="button"
            onClick={() => onSelectEvent(child.id)}
            className="w-full rounded-[20px] border border-slate-200 bg-white/80 px-4 py-4 text-left transition hover:border-slate-300 hover:bg-white"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-3">
                <div className="rounded-2xl border border-orange-100 bg-orange-50 p-2 text-orange-700">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-900">{child.title}</p>
                  <p className="mt-1 line-clamp-2 text-sm text-slate-600">{child.description}</p>
                  <p className="mt-2 text-xs text-slate-500">
                    {formatDateTime(child.startedAt)} · {formatDuration(child.durationMs)}
                  </p>
                </div>
              </div>
              <StatusPill status={child.status} />
            </div>
          </button>
        );
      })}
    </div>
  );
}

function MetricsPanel({ event }: { event: ExecutionEvent }) {
  const values = [
    { label: "Queue time", value: formatDuration(event.metrics.queueMs) },
    { label: "Execution time", value: formatDuration(event.metrics.executionMs ?? event.durationMs) },
    { label: "Tool latency", value: formatDuration(event.metrics.toolMs) },
    { label: "Model latency", value: formatDuration(event.metrics.modelMs) },
    { label: "Network latency", value: formatDuration(event.metrics.networkMs) },
    { label: "Tokens in", value: formatCount(event.metrics.tokenInput) },
    { label: "Tokens out", value: formatCount(event.metrics.tokenOutput) },
    { label: "Cost", value: formatCurrency(event.metrics.costUsd) },
    { label: "Item count", value: formatCount(event.metrics.itemCount) },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        {values.map((item) => (
          <div key={item.label} className="rounded-[18px] border border-slate-200 bg-[#fcfbf8] px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">{item.label}</p>
            <p className="mt-2 text-sm font-semibold text-slate-900">{item.value}</p>
          </div>
        ))}
      </div>

      {event.metrics.latencySeries?.length ? (
        <div className="rounded-[20px] border border-slate-200 bg-[#fcfbf8] p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
            <Network className="h-4 w-4" />
            Latency sparkline
          </div>
          <div className="flex h-24 items-end gap-2">
            {event.metrics.latencySeries.map((value, index) => (
              <div
                key={`${event.id}-latency-${index}`}
                className={clsx("flex-1 rounded-t-[10px]", toneForMetric(value, Math.max(...(event.metrics.latencySeries ?? [1]))))}
                style={{ height: `${Math.max(12, (value / Math.max(...(event.metrics.latencySeries ?? [1]))) * 100)}%` }}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
