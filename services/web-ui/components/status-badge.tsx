import clsx from "clsx";

type Props = {
  status: "pending" | "running" | "completed" | "failed" | "idle";
};

const styleMap: Record<Props["status"], string> = {
  pending: "bg-amber-100 text-amber-700 border-amber-300",
  running: "bg-cyan-100 text-cyan-700 border-cyan-300",
  completed: "bg-emerald-100 text-emerald-700 border-emerald-300",
  failed: "bg-rose-100 text-rose-700 border-rose-300",
  idle: "bg-slate-100 text-slate-700 border-slate-300"
};

export function StatusBadge({ status }: Props) {
  return (
    <span className={clsx("inline-flex rounded-full border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide", styleMap[status])}>
      {status}
    </span>
  );
}
