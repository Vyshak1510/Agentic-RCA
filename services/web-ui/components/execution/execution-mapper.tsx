"use client";

import clsx from "clsx";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  Edge,
  EdgeProps,
  EdgeTypes,
  Handle,
  MarkerType,
  MiniMap,
  Node,
  NodeProps,
  NodeTypes,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getSmoothStepPath,
  useNodesInitialized,
  useReactFlow,
} from "reactflow";
import { KeyboardEvent, useEffect, useMemo, useRef } from "react";

import { PanelFrame, ToolbarButton, getNodeKindIcon, statusStyles } from "@/components/execution/execution-primitives";
import {
  PositionedExecutionGroup,
  PositionedExecutionNode,
  buildExecutionGraphLayout,
} from "@/lib/execution-graph-layout";
import {
  ExecutionEdge,
  ExecutionRunTrace,
  formatDuration,
  getRcaRoleForNodeId,
  getSelectedEvent,
} from "@/lib/execution-trace";

type Props = {
  trace: ExecutionRunTrace;
  selectedEventId: string | null;
  onSelectEvent: (eventId: string) => void;
  onFitReady?: (fit: () => void) => void;
};

type ExecutionNodeData = {
  entry: PositionedExecutionNode;
  selected: boolean;
  current: boolean;
  emphasized: boolean;
  onSelect: (eventId: string) => void;
};

type GroupNodeData = {
  group: PositionedExecutionGroup;
};

type EdgeData = {
  edge: ExecutionEdge;
  emphasized: boolean;
};

const groupToneStyles: Record<PositionedExecutionGroup["tone"], string> = {
  neutral: "border-slate-200 bg-white/70",
  accent: "border-orange-200 bg-orange-50/70",
  danger: "border-rose-200 bg-rose-50/65",
};

const edgeToneStyles: Record<ExecutionEdge["state"], { stroke: string; glow: string; fill: string; label: string }> = {
  idle: { stroke: "#94a3b8", glow: "rgba(148,163,184,0.22)", fill: "#ffffff", label: "#64748b" },
  active: { stroke: "#f97316", glow: "rgba(249,115,22,0.24)", fill: "#fff7ed", label: "#c2410c" },
  failed: { stroke: "#e11d48", glow: "rgba(225,29,72,0.18)", fill: "#fff1f2", label: "#be123c" },
  skipped: { stroke: "#8b5cf6", glow: "rgba(139,92,246,0.16)", fill: "#f5f3ff", label: "#6d28d9" },
  waiting: { stroke: "#6366f1", glow: "rgba(99,102,241,0.16)", fill: "#eef2ff", label: "#4338ca" },
};

const mapperStatusTone: Record<ExecutionEdge["state"] | PositionedExecutionNode["node"]["state"], string> = {
  idle: "bg-slate-400",
  queued: "bg-amber-400",
  running: "bg-orange-500",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  failed: "bg-rose-500",
  skipped: "bg-violet-500",
  retrying: "bg-fuchsia-500",
  waiting: "bg-indigo-500",
  active: "bg-orange-500",
};

function GroupNode({ data }: NodeProps<GroupNodeData>) {
  return (
    <div
      className={clsx(
        "pointer-events-none h-full w-full rounded-[28px] border px-5 py-4 backdrop-blur-sm",
        groupToneStyles[data.group.tone]
      )}
    >
      <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">{data.group.label}</div>
    </div>
  );
}

function MapperStateBadge({ status }: { status: PositionedExecutionNode["node"]["state"] }) {
  return (
    <span
      className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-slate-200 bg-white/88"
      title={status}
      aria-label={status}
    >
      <span className={clsx("h-2 w-2 rounded-full", mapperStatusTone[status])} />
    </span>
  );
}

function ExecutionCard({ data }: NodeProps<ExecutionNodeData>) {
  const { entry } = data;
  const icon = getNodeKindIcon(entry.node.kind);
  const Icon = icon;
  const primaryEventId = entry.node.eventIds[0];
  const handleSelect = () => data.onSelect(primaryEventId);
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handleSelect();
    }
  };
  const isChild = entry.lane === "child";
  const rcaRole = getRcaRoleForNodeId(entry.node.id);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={handleSelect}
      onMouseDownCapture={(event) => event.stopPropagation()}
      onPointerDownCapture={(event) => event.stopPropagation()}
      onKeyDown={handleKeyDown}
      className={clsx(
        "group nodrag nopan relative flex h-full w-full flex-col border border-slate-200 bg-white/96 text-left transition",
        isChild
          ? "rounded-[18px] p-2.5 shadow-[0_10px_24px_rgba(15,23,42,0.08)]"
          : "rounded-[20px] p-3.5 shadow-[0_16px_36px_rgba(15,23,42,0.08)]",
        data.selected && "border-orange-300 shadow-[0_0_0_1px_rgba(249,115,22,0.12),0_18px_44px_rgba(249,115,22,0.12)]",
        data.current && "animate-[mapperPulse_2.2s_ease-in-out_infinite]",
        !data.selected && data.emphasized && "border-orange-100 bg-[#fffaf3]",
        "hover:-translate-y-0.5 hover:border-slate-300 hover:bg-white",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-300/70"
      )}
    >
      <Handle type="target" position={Position.Left} className="!h-0 !w-0 !border-0 !bg-transparent !opacity-0" />
      <Handle type="source" position={Position.Right} className="!h-0 !w-0 !border-0 !bg-transparent !opacity-0" />

      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className={clsx("border border-orange-100 bg-orange-50 text-orange-700", isChild ? "rounded-xl p-1.5" : "rounded-2xl p-2")}>
            <Icon className={clsx(isChild ? "h-4 w-4" : "h-5 w-5")} />
          </div>
          <div className="min-w-0">
            <p className={clsx("font-semibold uppercase tracking-[0.16em] text-slate-500", isChild ? "text-[10px]" : "text-[11px]")}>{entry.node.kind}</p>
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              <h3 className={clsx("truncate font-semibold text-slate-900", isChild ? "text-[12px]" : "text-sm")}>{entry.node.label}</h3>
              {rcaRole ? (
                <span className="rounded-full border border-orange-200 bg-orange-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-orange-700">
                  {rcaRole === "final" ? "Final RCA" : "Draft"}
                </span>
              ) : null}
            </div>
          </div>
        </div>
        <div className="shrink-0">
          <MapperStateBadge status={entry.node.state} />
        </div>
      </div>

      <p className={clsx("text-slate-600", isChild ? "mt-2 line-clamp-2 text-[11px] leading-[18px]" : "mt-2.5 line-clamp-2 text-[12px] leading-5")}>
        {entry.node.summary}
      </p>

      <div className={clsx("flex flex-wrap items-center gap-2", isChild ? "mt-2.5" : "mt-3")}>
        <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-700">
          {formatDuration(entry.node.durationMs)}
        </span>
        {typeof entry.node.retryCount === "number" && entry.node.retryCount > 0 ? (
          <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-1 text-[11px] font-medium text-violet-700">
            {entry.node.retryCount} retry
          </span>
        ) : null}
        {!isChild && entry.node.tool ? (
          <span className="max-w-full truncate rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-600">
            {entry.node.tool}
          </span>
        ) : null}
        {!isChild && entry.node.model ? (
          <span className="max-w-full truncate rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-600">
            {entry.node.model}
          </span>
        ) : null}
      </div>

      <div className="pointer-events-none absolute left-0 top-full z-20 mt-3 hidden w-[320px] rounded-[18px] border border-slate-200 bg-white/98 p-4 shadow-[0_18px_44px_rgba(15,23,42,0.12)] group-hover:block">
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Quick Summary</p>
          <span className={clsx("rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]", statusStyles[entry.node.state])}>
            {entry.node.state}
          </span>
        </div>
        <div className="space-y-2 text-xs text-slate-700">
          <div>
            <p className="text-slate-500">Inputs</p>
            <p>{entry.node.inputSummary}</p>
          </div>
          <div>
            <p className="text-slate-500">Outputs</p>
            <p>{entry.node.outputSummary}</p>
          </div>
          <div className="text-slate-500">Duration: {formatDuration(entry.node.durationMs)}</div>
        </div>
      </div>
    </div>
  );
}

function SvgEdgeLabel({
  text,
  x,
  y,
  tone,
}: {
  text: string;
  x: number;
  y: number;
  tone: { stroke: string; fill: string; label: string };
}) {
  const width = Math.max(58, Math.round(text.length * 6.8 + 18));

  return (
    <g transform={`translate(${x - width / 2}, ${y - 10})`} pointerEvents="none">
      <rect width={width} height={20} rx={999} fill={tone.fill} stroke={tone.stroke} strokeWidth={1} />
      <text x={width / 2} y={13} textAnchor="middle" fontSize="9" fontWeight="600" fill={tone.label} letterSpacing="0.1em">
        {text.toUpperCase()}
      </text>
    </g>
  );
}

function StateEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps<EdgeData>) {
  const edge = data?.edge;
  const state = edge?.state ?? "idle";
  const tone = edgeToneStyles[state];
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 22,
    offset: 18,
  });

  const edgeLabel =
    edge?.label ??
    (edge?.relation === "fanout"
      ? "fanout"
      : edge?.relation === "model"
        ? "model"
        : edge?.relation === "retry"
          ? "retry"
          : undefined);

  return (
    <>
      <BaseEdge
        id={`${id}-glow`}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: tone.glow,
          strokeWidth: data?.emphasized ? 2.7 : 1.9,
          opacity: data?.emphasized ? 0.46 : 0.18,
        }}
      />
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: tone.stroke,
          strokeWidth: data?.emphasized ? 1.35 : 1.05,
          strokeDasharray: edge?.relation === "retry" ? "7 6" : edge?.animated ? "8 7" : undefined,
          opacity: data?.emphasized ? 1 : 0.82,
          transition: "stroke 150ms ease, opacity 150ms ease",
        }}
      />
      {edgeLabel ? <SvgEdgeLabel text={edgeLabel} x={labelX} y={labelY} tone={tone} /> : null}
    </>
  );
}

const nodeTypes: NodeTypes = {
  groupNode: GroupNode,
  executionNode: ExecutionCard,
};

const edgeTypes: EdgeTypes = {
  stateEdge: StateEdge,
};

function collectPathNodes(trace: ExecutionRunTrace, selectedNodeId: string | null): Set<string> {
  if (!selectedNodeId) {
    return new Set();
  }
  const path = new Set<string>([selectedNodeId]);
  const nodeMap = new Map(trace.nodes.map((node) => [node.id, node]));

  const walk = (nodeId: string, direction: "upstream" | "downstream") => {
    const node = nodeMap.get(nodeId);
    if (!node) {
      return;
    }
    for (const related of direction === "upstream" ? node.upstream : node.downstream) {
      if (path.has(related)) {
        continue;
      }
      path.add(related);
      walk(related, direction);
    }
  };

  walk(selectedNodeId, "upstream");
  walk(selectedNodeId, "downstream");
  return path;
}

function MapperCanvas({ trace, selectedEventId, onSelectEvent, onFitReady }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const lastFocusedNodeIdRef = useRef<string | null>(null);
  const flow = useReactFlow();
  const nodesInitialized = useNodesInitialized();

  const selectedEvent = getSelectedEvent(trace, selectedEventId);
  const selectedNodeId = selectedEvent?.nodeId ?? null;
  const currentNodeId = trace.currentEventId ? trace.events.find((event) => event.id === trace.currentEventId)?.nodeId ?? null : null;
  const emphasizedNodes = useMemo(() => collectPathNodes(trace, selectedNodeId), [selectedNodeId, trace]);
  const layout = useMemo(() => buildExecutionGraphLayout(trace), [trace]);

  const nodes = useMemo<Array<Node<ExecutionNodeData | GroupNodeData>>>(() => {
    const groupNodes: Array<Node<GroupNodeData>> = layout.groups.map((group) => ({
      id: group.id,
      type: "groupNode",
      position: { x: group.x, y: group.y },
      width: group.width,
      height: group.height,
      selectable: false,
      draggable: false,
      data: { group },
      style: {
        width: group.width,
        height: group.height,
        border: "none",
        background: "transparent",
        pointerEvents: "none",
      },
      zIndex: 0,
    }));

    const executionNodes: Array<Node<ExecutionNodeData>> = layout.nodes.map((entry) => ({
      id: entry.node.id,
      type: "executionNode",
      position: { x: entry.x, y: entry.y },
      width: entry.width,
      height: entry.height,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      selectable: false,
      draggable: false,
      data: {
        entry,
        selected: selectedNodeId === entry.node.id,
        current: currentNodeId === entry.node.id,
        emphasized: emphasizedNodes.size === 0 || emphasizedNodes.has(entry.node.id),
        onSelect: onSelectEvent,
      },
      style: {
        width: entry.width,
        height: entry.height,
        border: "none",
        background: "transparent",
      },
      zIndex: 20,
    }));

    return [...groupNodes, ...executionNodes];
  }, [currentNodeId, emphasizedNodes, layout.groups, layout.nodes, onSelectEvent, selectedNodeId]);

  const edges = useMemo<Array<Edge<EdgeData>>>(() => {
    return trace.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "stateEdge",
      animated: edge.animated,
      markerEnd: {
        type: MarkerType.Arrow,
        width: 7,
        height: 7,
        color: edgeToneStyles[edge.state].stroke,
      },
      data: {
        edge,
        emphasized: emphasizedNodes.size === 0 || (emphasizedNodes.has(edge.source) && emphasizedNodes.has(edge.target)),
      },
      zIndex: 10,
    }));
  }, [emphasizedNodes, trace.edges]);

  const fitToLayout = () => {
    if (!nodesInitialized || !containerRef.current || layout.nodes.length === 0) {
      return;
    }
    const containerBounds = containerRef.current.getBoundingClientRect();
    if (containerBounds.width === 0 || containerBounds.height === 0) {
      return;
    }
    const bounds = layout.nodes.reduce(
      (accumulator, entry) => ({
        minX: Math.min(accumulator.minX, entry.x),
        minY: Math.min(accumulator.minY, entry.y),
        maxX: Math.max(accumulator.maxX, entry.x + entry.width),
        maxY: Math.max(accumulator.maxY, entry.y + entry.height),
      }),
      {
        minX: Number.POSITIVE_INFINITY,
        minY: Number.POSITIVE_INFINITY,
        maxX: Number.NEGATIVE_INFINITY,
        maxY: Number.NEGATIVE_INFINITY,
      }
    );
    const contentWidth = bounds.maxX - bounds.minX;
    const contentHeight = bounds.maxY - bounds.minY;
    if (contentWidth <= 0 || contentHeight <= 0) {
      return;
    }
    const padding = 36;
    const computedZoom = Math.min(
      (containerBounds.width - padding * 2) / contentWidth,
      (containerBounds.height - padding * 2) / contentHeight
    );
    const zoom = Math.min(0.84, Math.max(0.34, computedZoom));
    const x =
      computedZoom < 0.34
        ? padding - bounds.minX * zoom
        : (containerBounds.width - contentWidth * zoom) / 2 - bounds.minX * zoom;
    const y = (containerBounds.height - contentHeight * zoom) / 2 - bounds.minY * zoom;

    flow.setViewport(
      {
        x,
        y,
        zoom,
      },
      {
        duration: 280,
      }
    );
  };

  useEffect(() => {
    if (!nodesInitialized) {
      return;
    }
    let rafOne = 0;
    let rafTwo = 0;
    rafOne = window.requestAnimationFrame(() => {
      rafTwo = window.requestAnimationFrame(() => {
        fitToLayout();
      });
    });
    return () => {
      window.cancelAnimationFrame(rafOne);
      window.cancelAnimationFrame(rafTwo);
    };
  }, [flow, nodes]);

  useEffect(() => {
    if (!nodesInitialized || !containerRef.current) {
      return;
    }
    let handle = 0;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(handle);
      handle = window.setTimeout(() => {
        fitToLayout();
      }, 180);
    });
    observer.observe(containerRef.current);
    return () => {
      window.clearTimeout(handle);
      observer.disconnect();
    };
  }, [flow, nodes]);

  useEffect(() => {
    if (!onFitReady || !nodesInitialized) {
      return;
    }
    onFitReady(fitToLayout);
  }, [nodesInitialized, onFitReady, flow, nodes]);

  useEffect(() => {
    if (!nodesInitialized || !selectedNodeId) {
      return;
    }
    if (lastFocusedNodeIdRef.current === selectedNodeId) {
      return;
    }
    const selectedNode = layout.nodes.find((entry) => entry.node.id === selectedNodeId);
    if (!selectedNode) {
      return;
    }
    lastFocusedNodeIdRef.current = selectedNodeId;
    flow.setCenter(
      selectedNode.x + selectedNode.width / 2,
      selectedNode.y + selectedNode.height / 2,
      {
        zoom: Math.max(flow.getZoom(), 0.48),
        duration: 240,
      }
    );
  }, [flow, layout.nodes, nodesInitialized, selectedNodeId]);

  return (
    <PanelFrame
      title="Execution Mapper"
      subtitle="Node graph, execution path, and live state"
      className="h-full"
      actions={
        <ToolbarButton onClick={fitToLayout} className="px-3 py-2 text-xs uppercase tracking-[0.16em]">
          Fit
        </ToolbarButton>
      }
    >
      <div ref={containerRef} className="execution-mapper h-full min-h-0">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          panOnDrag
          zoomOnScroll
          minZoom={0.34}
          maxZoom={1.15}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          className="bg-[#fffdf8]"
          proOptions={{ hideAttribution: true }}
        >
          <Background color="rgba(148, 163, 184, 0.18)" gap={28} size={1.1} variant={BackgroundVariant.Dots} />
          <MiniMap
            position="bottom-left"
            pannable
            zoomable
            style={{
              width: 128,
              height: 80,
              borderRadius: 18,
              overflow: "hidden",
              border: "1px solid rgba(203, 213, 225, 0.95)",
              background: "rgba(255, 255, 255, 0.92)",
            }}
            nodeColor={(node) => {
              const state = (node.data as ExecutionNodeData | undefined)?.entry?.node.state ?? "idle";
              return edgeToneStyles[state === "success" ? "active" : state === "failed" ? "failed" : state === "skipped" ? "skipped" : "idle"].stroke;
            }}
          />
          <Controls
            position="bottom-right"
            style={{
              borderRadius: 18,
              border: "1px solid rgba(203, 213, 225, 0.95)",
              background: "rgba(255, 255, 255, 0.95)",
              padding: 4,
            }}
            showInteractive={false}
          />
        </ReactFlow>
      </div>
    </PanelFrame>
  );
}

export function ExecutionMapper(props: Props) {
  return (
    <ReactFlowProvider>
      <MapperCanvas {...props} />
    </ReactFlowProvider>
  );
}
