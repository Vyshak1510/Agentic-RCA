"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Controls,
  Edge,
  MarkerType,
  MiniMap,
  Node,
  NodeProps,
  Position,
  ReactFlow,
  ReactFlowInstance,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from "reactflow";

import { WorkflowNode } from "@/components/workflow-node";
import { fetchWorkflowLayout, upsertWorkflowLayout } from "@/lib/api";
import { WorkflowRunDetail, WorkflowStageId } from "@/lib/types";
import { WORKFLOW_STAGES, latestAttempt, msToHuman, stageStatus } from "@/lib/workflow";

type Props = {
  run: WorkflowRunDetail | null;
  selectedStageId: WorkflowStageId | null;
  onSelectStage: (stageId: WorkflowStageId) => void;
  workflowKey?: string;
};

type StageNodeData = {
  stageId: WorkflowStageId;
  label: string;
  summary: string;
  status: ReturnType<typeof stageStatus>;
  attempts: number;
  durationLabel: string;
  citationCount: number;
  lastMessage: string | null;
  selected: boolean;
  onSelect: (stageId: WorkflowStageId) => void;
};

const NODE_WIDTH = 304;
const NODE_HEIGHT = 245;

const DEFAULT_POSITIONS: Record<WorkflowStageId, { x: number; y: number }> = {
  resolve_service_identity: { x: 70, y: 80 },
  build_investigation_plan: { x: 430, y: 80 },
  collect_evidence: { x: 790, y: 80 },
  synthesize_rca_report: { x: 1150, y: 80 },
  publish_report: { x: 610, y: 380 },
  emit_eval_event: { x: 970, y: 380 },
};

const EDGE_ORDER: Array<{ source: WorkflowStageId; target: WorkflowStageId }> = [
  { source: "resolve_service_identity", target: "build_investigation_plan" },
  { source: "build_investigation_plan", target: "collect_evidence" },
  { source: "collect_evidence", target: "synthesize_rca_report" },
  { source: "synthesize_rca_report", target: "publish_report" },
  { source: "publish_report", target: "emit_eval_event" },
];

function StageNodeCard({ data }: NodeProps<StageNodeData>) {
  return (
    <WorkflowNode
      order={WORKFLOW_STAGES.findIndex((item) => item.id === data.stageId) + 1}
      label={data.label}
      summary={data.summary}
      status={data.status}
      attempts={data.attempts}
      durationLabel={data.durationLabel}
      citationCount={data.citationCount}
      lastMessage={data.lastMessage}
      selected={data.selected}
      onClick={() => data.onSelect(data.stageId)}
    />
  );
}

const nodeTypes = { stageNode: StageNodeCard };

function edgeIsActive(run: WorkflowRunDetail | null, source: WorkflowStageId, target: WorkflowStageId, selected: WorkflowStageId | null): boolean {
  if (!run) {
    return selected === source || selected === target;
  }
  if (selected === source || selected === target) {
    return true;
  }
  const sourceState = stageStatus(run, source);
  const targetState = stageStatus(run, target);
  return sourceState === "completed" || targetState !== "idle";
}

function edgeColor(run: WorkflowRunDetail | null, source: WorkflowStageId, target: WorkflowStageId, selected: WorkflowStageId | null): string {
  if (run && stageStatus(run, source) === "failed") {
    return "#f43f5e";
  }
  return edgeIsActive(run, source, target, selected) ? "#ff6000" : "#b5b5b5";
}

function buildStageNode(
  stageId: WorkflowStageId,
  position: { x: number; y: number },
  run: WorkflowRunDetail | null,
  selectedStageId: WorkflowStageId | null,
  onSelectStage: (stageId: WorkflowStageId) => void
): Node<StageNodeData> {
  const stage = WORKFLOW_STAGES.find((item) => item.id === stageId);
  const attempt = latestAttempt(run, stageId);
  const attemptsCount = run?.stage_attempts[stageId]?.length ?? 0;
  return {
    id: stageId,
    type: "stageNode",
    position,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    draggable: true,
    selectable: false,
    data: {
      stageId,
      label: stage?.label ?? stageId,
      summary: stage?.summary ?? "",
      status: stageStatus(run, stageId),
      attempts: attemptsCount,
      durationLabel: msToHuman(attempt?.duration_ms ?? null),
      citationCount: attempt?.citations.length ?? 0,
      lastMessage: attempt?.message ?? null,
      selected: selectedStageId === stageId,
      onSelect: onSelectStage,
    },
    style: { width: NODE_WIDTH, height: NODE_HEIGHT, border: "none", background: "transparent" },
  };
}

function buildInitialNodes(
  run: WorkflowRunDetail | null,
  selectedStageId: WorkflowStageId | null,
  onSelectStage: (stageId: WorkflowStageId) => void
): Node<StageNodeData>[] {
  return WORKFLOW_STAGES.map((stage) =>
    buildStageNode(stage.id, DEFAULT_POSITIONS[stage.id], run, selectedStageId, onSelectStage)
  );
}

function buildEdges(run: WorkflowRunDetail | null, selectedStageId: WorkflowStageId | null): Edge[] {
  return EDGE_ORDER.map((edge) => {
    const color = edgeColor(run, edge.source, edge.target, selectedStageId);
    return {
      id: `${edge.source}-${edge.target}`,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      animated: edgeIsActive(run, edge.source, edge.target, selectedStageId),
      style: { stroke: color, strokeWidth: 2.8, strokeLinecap: "round" },
      pathOptions: { borderRadius: 24, offset: 24 },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color,
      },
    };
  });
}

function MapperCanvas({ run, selectedStageId, onSelectStage, workflowKey }: Required<Props>) {
  const [nodes, setNodes, onNodesChange] = useNodesState<StageNodeData>(buildInitialNodes(run, selectedStageId, onSelectStage));
  const [edges, setEdges, onEdgesChange] = useEdgesState(buildEdges(run, selectedStageId));
  const [layoutReady, setLayoutReady] = useState(false);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null);
  const saveTimer = useRef<number | null>(null);
  const viewportRef = useRef({ x: 0, y: 0, zoom: 1 });

  useEffect(() => {
    setEdges(buildEdges(run, selectedStageId));
    setNodes((previous) =>
      previous.map((node) =>
        buildStageNode(node.id as WorkflowStageId, node.position, run, selectedStageId, onSelectStage)
      )
    );
  }, [run, selectedStageId, onSelectStage, setEdges, setNodes]);

  useEffect(() => {
    let alive = true;
    async function loadLayout() {
      try {
        const layout = await fetchWorkflowLayout(workflowKey);
        if (!alive) {
          return;
        }
        if (layout) {
          const byId = new Map(layout.nodes.map((node) => [node.id, node]));
          setNodes((previous) =>
            previous.map((node) => {
              const saved = byId.get(node.id);
              return saved ? { ...node, position: { x: saved.x, y: saved.y } } : node;
            })
          );
          viewportRef.current = layout.viewport;
          if (flowInstance) {
            flowInstance.setViewport(layout.viewport, { duration: 0 });
          }
        }
      } catch {
        // Best-effort restore.
      } finally {
        if (alive) {
          setLayoutReady(true);
        }
      }
    }

    void loadLayout();
    return () => {
      alive = false;
    };
  }, [workflowKey, flowInstance, setNodes]);

  const persistLayout = useCallback(
    (currentNodes: Node<StageNodeData>[], viewport: { x: number; y: number; zoom: number }) => {
      if (!layoutReady) {
        return;
      }
      if (saveTimer.current) {
        window.clearTimeout(saveTimer.current);
      }
      saveTimer.current = window.setTimeout(() => {
        void upsertWorkflowLayout(workflowKey, {
          nodes: currentNodes.map((node) => ({ id: node.id, x: node.position.x, y: node.position.y })),
          viewport,
        }).catch(() => {
          // Best-effort persistence.
        });
      }, 400);
    },
    [layoutReady, workflowKey]
  );

  const onNodeDragStop = useCallback(
    (_event: unknown, _node: Node<StageNodeData>, currentNodes: Node<StageNodeData>[]) => {
      persistLayout(currentNodes, viewportRef.current);
    },
    [persistLayout]
  );

  const onMoveEnd = useCallback(
    (_event: unknown, viewport: { x: number; y: number; zoom: number }) => {
      viewportRef.current = viewport;
      persistLayout(nodes, viewport);
    },
    [nodes, persistLayout]
  );

  const resetLayout = useCallback(() => {
    setNodes(buildInitialNodes(run, selectedStageId, onSelectStage));
    if (flowInstance) {
      flowInstance.setViewport({ x: 0, y: 0, zoom: 1 }, { duration: 300 });
    }
    viewportRef.current = { x: 0, y: 0, zoom: 1 };
    persistLayout(buildInitialNodes(run, selectedStageId, onSelectStage), viewportRef.current);
  }, [setNodes, run, selectedStageId, onSelectStage, flowInstance, persistLayout]);

  const stageTotal = WORKFLOW_STAGES.length;
  const completedCount = WORKFLOW_STAGES.filter((stage) => stageStatus(run, stage.id) === "completed").length;
  const runningCount = WORKFLOW_STAGES.filter((stage) => stageStatus(run, stage.id) === "running").length;

  return (
    <div className="w-full overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-[0_8px_32px_rgba(23,23,23,0.08)]">
      <div className="border-b border-neutral-200 bg-[#fcfcfc] px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-[#171717]">Execution Mapper</h3>
            <p className="text-[13px] text-[#676767]">Drag cards, pan canvas, zoom in/out, and inspect stage execution.</p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 font-semibold text-emerald-700">
              {completedCount}/{stageTotal} completed
            </span>
            <span className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 font-semibold text-sky-700">
              {runningCount} running
            </span>
            <button
              type="button"
              onClick={resetLayout}
              className="rounded-full border border-neutral-300 bg-white px-2.5 py-1 font-semibold text-neutral-700 hover:bg-neutral-50"
            >
              Reset Layout
            </button>
          </div>
        </div>
      </div>

      <div className="h-[700px] w-full bg-[radial-gradient(circle_at_50%_0%,#fff5eb_0%,#ffffff_60%)]">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeDragStop={onNodeDragStop}
          onMoveEnd={onMoveEnd}
          onInit={(instance) => {
            setFlowInstance(instance);
            instance.setViewport(viewportRef.current, { duration: 0 });
          }}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.35}
          maxZoom={1.8}
          defaultEdgeOptions={{
            type: "smoothstep",
            animated: false,
            style: { stroke: "#b5b5b5", strokeWidth: 2.8, strokeLinecap: "round" },
            markerEnd: { type: MarkerType.ArrowClosed, color: "#b5b5b5" },
          }}
          snapToGrid
          snapGrid={[16, 16]}
          panOnScroll
          connectionLineType={ConnectionLineType.Bezier}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} color="#e9e9e9" />
          <MiniMap
            pannable
            zoomable
            nodeColor={(node) => {
              const stageId = node.id as WorkflowStageId;
              const status = stageStatus(run, stageId);
              if (status === "completed") return "#10b981";
              if (status === "running") return "#0284c7";
              if (status === "failed") return "#f43f5e";
              return "#b5b5b5";
            }}
            maskColor="rgba(245,245,245,0.65)"
          />
          <Controls position="bottom-left" showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}

export function WorkflowMapper({
  run,
  selectedStageId,
  onSelectStage,
  workflowKey = "rca-v1-stage-map",
}: Props) {
  const stableProps = useMemo(
    () => ({ run, selectedStageId, onSelectStage, workflowKey }),
    [run, selectedStageId, onSelectStage, workflowKey]
  );
  return (
    <ReactFlowProvider>
      <MapperCanvas {...stableProps} />
    </ReactFlowProvider>
  );
}
