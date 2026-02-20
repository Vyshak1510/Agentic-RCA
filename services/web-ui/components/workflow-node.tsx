"use client";

import clsx from "clsx";
import { Handle, Position } from "reactflow";

import { StageVisualStatus } from "@/lib/workflow";

type Props = {
  order: number;
  label: string;
  summary: string;
  status: StageVisualStatus;
  attempts: number;
  durationLabel: string;
  citationCount: number;
  lastMessage: string | null;
  selected: boolean;
  onClick: () => void;
};

const statusStyles: Record<StageVisualStatus, string> = {
  idle: "border-neutral-300 bg-neutral-100 text-neutral-600",
  queued: "border-amber-200 bg-amber-50 text-amber-700",
  running: "border-sky-200 bg-sky-50 text-sky-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  skipped: "border-violet-200 bg-violet-50 text-violet-700"
};

export function WorkflowNode({
  order,
  label,
  summary,
  status,
  attempts,
  durationLabel,
  citationCount,
  lastMessage,
  selected,
  onClick
}: Props) {
  return (
    <div className="relative w-[304px]">
      <Handle
        id="left"
        type="target"
        position={Position.Left}
        isConnectable={false}
        className="workflow-handle workflow-handle-target"
      />
      <Handle
        id="right"
        type="source"
        position={Position.Right}
        isConnectable={false}
        className="workflow-handle workflow-handle-source"
      />
      <button
        type="button"
        onClick={onClick}
        className={clsx(
          "relative w-full rounded-2xl border border-neutral-200 bg-white p-4 text-left shadow-[0_8px_24px_rgba(23,23,23,0.06)] transition",
          "hover:-translate-y-0.5 hover:border-neutral-300 hover:shadow-[0_14px_28px_rgba(23,23,23,0.08)]",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-[#ff6000]",
          selected ? "ring-2 ring-[#ff6000] ring-offset-1" : "ring-0"
        )}
      >
        {status === "running" ? (
          <span className="absolute right-3 top-3 h-2.5 w-2.5 rounded-full bg-sky-400">
            <span className="absolute inset-0 animate-ping rounded-full bg-sky-300" />
          </span>
        ) : null}

        <div className="mb-3 flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-2 rounded-full border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-[11px] font-semibold tracking-[0.02em] text-neutral-700">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-neutral-900 text-[10px] text-white">
              {order}
            </span>
            Stage
          </span>
          <span className={clsx("rounded-full border px-2 py-1 text-[11px] font-semibold capitalize", statusStyles[status])}>{status}</span>
        </div>

        <p className="text-sm font-semibold tracking-[-0.01em] text-[#171717]">{label}</p>
        <p className="mt-2 max-h-10 overflow-hidden text-[13px] leading-5 text-[#676767]">{summary}</p>

        <div className="mt-4 grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-2">
            <p className="text-[10px] uppercase tracking-[0.08em] text-neutral-500">Attempts</p>
            <p className="text-[13px] font-semibold text-[#171717]">{attempts}</p>
          </div>
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-2">
            <p className="text-[10px] uppercase tracking-[0.08em] text-neutral-500">Duration</p>
            <p className="text-[13px] font-semibold text-[#171717]">{durationLabel}</p>
          </div>
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-2">
            <p className="text-[10px] uppercase tracking-[0.08em] text-neutral-500">Citations</p>
            <p className="text-[13px] font-semibold text-[#171717]">{citationCount}</p>
          </div>
        </div>

        {lastMessage ? (
          <p className="mt-3 max-h-[56px] overflow-hidden rounded-lg border border-neutral-200 bg-[#f7f7f7] px-2.5 py-2 text-[12px] text-[#676767]">
            {lastMessage}
          </p>
        ) : null}
      </button>
    </div>
  );
}
