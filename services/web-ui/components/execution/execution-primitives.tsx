"use client";

import clsx from "clsx";
import {
  AlertTriangle,
  Bot,
  BrainCircuit,
  Database,
  FlagTriangleRight,
  Hammer,
  Layers3,
  Search,
  ShieldCheck,
  Sparkles,
  Waypoints,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { ComponentPropsWithoutRef, ReactNode } from "react";

import { ExecutionEventKind, ExecutionNodeKind, ExecutionStatus } from "@/lib/execution-trace";

export const statusStyles: Record<ExecutionStatus, string> = {
  idle: "border-slate-200 bg-slate-100 text-slate-600",
  queued: "border-amber-200 bg-amber-50 text-amber-700",
  running: "border-orange-200 bg-orange-50 text-orange-700",
  success: "border-emerald-200 bg-emerald-50 text-emerald-700",
  warning: "border-orange-200 bg-orange-50 text-orange-700",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  skipped: "border-violet-200 bg-violet-50 text-violet-700",
  retrying: "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700",
  waiting: "border-indigo-200 bg-indigo-50 text-indigo-700",
};

const nodeKindIcons: Record<ExecutionNodeKind, LucideIcon> = {
  trigger: FlagTriangleRight,
  stage: Workflow,
  tool: Wrench,
  model: BrainCircuit,
  retrieval: Database,
  memory: Layers3,
  human: ShieldCheck,
  branch: Waypoints,
  evaluation: Sparkles,
};

export function getNodeKindIcon(kind: ExecutionNodeKind): LucideIcon {
  return nodeKindIcons[kind];
}

export function getEventKindIcon(kind: ExecutionEventKind): LucideIcon {
  switch (kind) {
    case "model_call":
      return BrainCircuit;
    case "tool_call":
      return Hammer;
    case "retrieval":
      return Database;
    case "retry":
      return AlertTriangle;
    case "publish":
      return Bot;
    case "stream":
      return Sparkles;
    default:
      return Workflow;
  }
}

export function StatusPill({ status, className }: { status: ExecutionStatus; className?: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.16em]",
        statusStyles[status],
        className
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-90" />
      {status}
    </span>
  );
}

export function ToolbarButton({
  active,
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<"button"> & { active?: boolean }) {
  return (
    <button
      {...props}
      type={props.type ?? "button"}
      className={clsx(
        "inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-sm transition",
        active
          ? "border-orange-200 bg-orange-50 text-orange-700 shadow-[0_0_0_1px_rgba(251,146,60,0.12)]"
          : "border-slate-200 bg-white/80 text-slate-600 hover:border-slate-300 hover:bg-white hover:text-slate-900",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-300/70",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
    >
      {children}
    </button>
  );
}

export function FilterChip({
  active,
  children,
  ...props
}: ComponentPropsWithoutRef<"button"> & { active?: boolean }) {
  return (
    <button
      {...props}
      type={props.type ?? "button"}
      className={clsx(
        "inline-flex items-center rounded-full border px-2.5 py-1 text-[12px] font-medium transition",
        active
          ? "border-orange-200 bg-orange-50 text-orange-700"
          : "border-slate-200 bg-white/75 text-slate-500 hover:border-slate-300 hover:text-slate-900"
      )}
    >
      {children}
    </button>
  );
}

export function SearchBar({
  value,
  onChange,
  placeholder,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  className?: string;
}) {
  return (
    <label
      className={clsx(
        "inline-flex min-w-[220px] items-center gap-2 rounded-xl border border-slate-200 bg-white/85 px-3 py-2 text-sm text-slate-600 transition focus-within:border-orange-300 focus-within:text-slate-900",
        className
      )}
    >
      <Search className="h-4 w-4 text-slate-400" />
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="w-full bg-transparent text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none"
      />
    </label>
  );
}

export function PanelFrame({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={clsx(
        "flex h-full min-h-0 flex-col overflow-hidden rounded-[24px] border border-slate-200/90 bg-white/88 shadow-[0_18px_48px_rgba(15,23,42,0.08)] backdrop-blur-sm",
        className
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">{title}</p>
          {subtitle ? <p className="mt-1 text-sm text-slate-600">{subtitle}</p> : null}
        </div>
        {actions}
      </div>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

export function MetadataRow({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: ReactNode;
  emphasis?: boolean;
}) {
  return (
    <div className="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-3 py-2">
      <dt className="text-xs uppercase tracking-[0.16em] text-slate-500">{label}</dt>
      <dd className={clsx("min-w-0 break-words text-left text-sm [overflow-wrap:anywhere] text-slate-700", emphasis && "font-semibold text-slate-900")}>
        {value}
      </dd>
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 rounded-[20px] border border-dashed border-slate-200 bg-[#fcfbf8]/85 px-6 text-center">
      <div className="rounded-2xl border border-orange-100 bg-orange-50 p-3 text-orange-700">
        <CircleDecoration />
      </div>
      <div>
        <h3 className="text-base font-semibold text-slate-900">{title}</h3>
        <p className="mt-1 max-w-md text-sm text-slate-600">{body}</p>
      </div>
      {action}
    </div>
  );
}

function CircleDecoration() {
  return <Workflow className="h-6 w-6" />;
}

export function SkeletonBlock({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-2xl bg-slate-200/70", className)} />;
}

export function Tabs<T extends string>({
  value,
  onChange,
  items,
}: {
  value: T;
  onChange: (value: T) => void;
  items: Array<{ value: T; label: string; count?: number }>;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          onClick={() => onChange(item.value)}
          className={clsx(
            "rounded-full border px-3 py-1.5 text-[12px] font-medium transition",
            value === item.value
              ? "border-orange-200 bg-orange-50 text-orange-700"
              : "border-slate-200 bg-white/75 text-slate-500 hover:border-slate-300 hover:text-slate-900"
          )}
        >
          {item.label}
          {typeof item.count === "number" ? <span className="ml-1 text-slate-400">{item.count}</span> : null}
        </button>
      ))}
    </div>
  );
}
