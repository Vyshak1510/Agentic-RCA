"use client";

import clsx from "clsx";
import { Check, ChevronDown, ChevronRight, Copy, Download, GitCompareArrows } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";

import { EmptyState, SearchBar, ToolbarButton } from "@/components/execution/execution-primitives";
import { safeStringify } from "@/lib/execution-trace";

type ViewerMode = "json" | "table" | "text";

type Props = {
  value: unknown;
  compareValue?: unknown;
  defaultMode?: ViewerMode;
  allowDownload?: boolean;
  truncated?: boolean;
  title?: string;
  scrollMode?: "inner" | "outer";
};

type DiffRow = {
  key: string;
  change: "added" | "removed" | "changed";
  before?: string;
  after?: string;
};

function isObjectLike(value: unknown): value is Record<string, unknown> | unknown[] {
  return typeof value === "object" && value !== null;
}

function summarizeValue(value: unknown): string {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return `Array(${value.length})`;
  }
  if (typeof value === "object") {
    return `Object(${Object.keys(value as Record<string, unknown>).length})`;
  }
  if (typeof value === "string") {
    return value.length > 80 ? `${value.slice(0, 80)}...` : value;
  }
  return String(value);
}

function searchMatches(path: string, value: unknown, query: string): boolean {
  if (!query) {
    return true;
  }
  const lowerPath = path.toLowerCase();
  if (lowerPath.includes(query)) {
    return true;
  }
  if (!isObjectLike(value)) {
    return String(value ?? "").toLowerCase().includes(query);
  }
  if (Array.isArray(value)) {
    return value.some((item, index) => searchMatches(`${path}.${index}`, item, query));
  }
  return Object.entries(value).some(([key, item]) => searchMatches(`${path}.${key}`, item, query));
}

function collectDiffRows(before: unknown, after: unknown): DiffRow[] {
  if (!isObjectLike(before) || !isObjectLike(after) || Array.isArray(before) || Array.isArray(after)) {
    return [];
  }

  const beforeRecord = before as Record<string, unknown>;
  const afterRecord = after as Record<string, unknown>;
  const keys = new Set([...Object.keys(beforeRecord), ...Object.keys(afterRecord)]);
  const rows: DiffRow[] = [];

  for (const key of keys) {
    const previous = beforeRecord[key];
    const next = afterRecord[key];
    if (!(key in beforeRecord)) {
      rows.push({ key, change: "added", after: summarizeValue(next) });
      continue;
    }
    if (!(key in afterRecord)) {
      rows.push({ key, change: "removed", before: summarizeValue(previous) });
      continue;
    }
    if (safeStringify(previous) !== safeStringify(next)) {
      rows.push({ key, change: "changed", before: summarizeValue(previous), after: summarizeValue(next) });
    }
  }

  return rows;
}

export function JsonViewer({
  value,
  compareValue,
  defaultMode = "json",
  allowDownload = false,
  truncated = false,
  title,
  scrollMode = "inner",
}: Props) {
  const [mode, setMode] = useState<ViewerMode>(defaultMode);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    root: true,
  });
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());

  const stringValue = useMemo(() => safeStringify(value), [value]);
  const diffRows = useMemo(() => collectDiffRows(compareValue, value), [compareValue, value]);
  const fillsPanel = scrollMode === "inner";

  const togglePath = (path: string) => {
    setExpanded((current) => ({
      ...current,
      [path]: !(current[path] ?? true),
    }));
  };

  const handleCopy = async () => {
    await navigator.clipboard.writeText(stringValue);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  const handleDownload = () => {
    const blob = new Blob([stringValue], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${title?.toLowerCase().replace(/\s+/g, "-") ?? "trace-payload"}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={clsx("flex min-h-0 flex-col gap-3", fillsPanel && "h-full")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {(["json", "table", "text"] as ViewerMode[]).map((item) => (
            <ToolbarButton key={item} active={mode === item} onClick={() => setMode(item)} className="px-2.5 py-1.5 text-xs uppercase tracking-[0.16em]">
              {item}
            </ToolbarButton>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SearchBar value={query} onChange={setQuery} placeholder="Search payload" className="min-w-[220px]" />
          <ToolbarButton onClick={() => void handleCopy()} className="px-2.5 py-1.5 text-xs">
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            Copy
          </ToolbarButton>
          {allowDownload ? (
            <ToolbarButton onClick={handleDownload} className="px-2.5 py-1.5 text-xs">
              <Download className="h-4 w-4" />
              Download
            </ToolbarButton>
          ) : null}
        </div>
      </div>

      {truncated ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          This payload includes truncated content. Copy or download the raw payload to inspect the complete structure safely.
        </div>
      ) : null}

      {diffRows.length > 0 && mode !== "text" ? (
        <div className="rounded-2xl border border-orange-200 bg-orange-50/70 px-3 py-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-orange-700">
            <GitCompareArrows className="h-4 w-4" />
            Input vs output diff
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {diffRows.slice(0, 8).map((row) => (
              <div key={`${row.change}-${row.key}`} className="rounded-xl border border-slate-200 bg-white/80 px-3 py-2 text-xs text-slate-700">
                <p className="font-semibold text-slate-900">{row.key}</p>
                <p className="mt-1 uppercase tracking-[0.14em] text-slate-500">{row.change}</p>
                <p className="mt-1 text-slate-600">
                  {row.before ? `before: ${row.before}` : "before: -"}
                  <br />
                  {row.after ? `after: ${row.after}` : "after: -"}
                </p>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className={clsx("rounded-[20px] border border-slate-200 bg-white/88", fillsPanel ? "min-h-0 flex-1 overflow-hidden" : "overflow-hidden")}>
        {value === undefined ? (
          <EmptyState title="No payload captured" body="This event did not record a structured payload." />
        ) : mode === "text" ? (
          <pre className={clsx("px-4 py-4 text-xs leading-6 text-slate-700", fillsPanel ? "h-full overflow-auto" : "overflow-x-auto whitespace-pre-wrap break-words")}>{stringValue}</pre>
        ) : mode === "table" ? (
          <PayloadTable value={value} query={deferredQuery} fillHeight={fillsPanel} />
        ) : (
          <div className={clsx("px-3 py-3", fillsPanel && "h-full overflow-auto")}>
            <JsonTree value={value} path="root" depth={0} expanded={expanded} onToggle={togglePath} query={deferredQuery} />
          </div>
        )}
      </div>
    </div>
  );
}

function PayloadTable({ value, query, fillHeight }: { value: unknown; query: string; fillHeight: boolean }) {
  if (!isObjectLike(value)) {
    return (
      <pre className={clsx("px-4 py-4 text-xs leading-6 text-slate-700", fillHeight ? "h-full overflow-auto" : "overflow-x-auto whitespace-pre-wrap break-words")}>
        {safeStringify(value)}
      </pre>
    );
  }

  const rows = Array.isArray(value)
    ? value.map((item, index) => [`[${index}]`, item] as const)
    : Object.entries(value as Record<string, unknown>);

  const filtered = rows.filter(([key, item]) => searchMatches(key, item, query));

  return (
    <div className={clsx(fillHeight ? "h-full overflow-auto" : "overflow-x-auto")}>
      <table className="min-w-full border-collapse text-left text-xs text-slate-700">
        <thead className="sticky top-0 bg-[#fcfbf8] text-slate-500">
          <tr>
            <th className="border-b border-slate-200 px-4 py-3 font-medium uppercase tracking-[0.14em]">Field</th>
            <th className="border-b border-slate-200 px-4 py-3 font-medium uppercase tracking-[0.14em]">Type</th>
            <th className="border-b border-slate-200 px-4 py-3 font-medium uppercase tracking-[0.14em]">Value</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(([key, item]) => (
            <tr key={key} className="border-b border-slate-200/80 align-top">
              <td className="px-4 py-3 font-mono text-slate-900">{key}</td>
              <td className="px-4 py-3 text-slate-500">{Array.isArray(item) ? "array" : item === null ? "null" : typeof item}</td>
              <td className="max-w-[560px] px-4 py-3 text-slate-700">{summarizeValue(item)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JsonTree({
  value,
  path,
  depth,
  expanded,
  onToggle,
  query,
  label,
}: {
  value: unknown;
  path: string;
  depth: number;
  expanded: Record<string, boolean>;
  onToggle: (path: string) => void;
  query: string;
  label?: string;
}) {
  if (!searchMatches(path, value, query)) {
    return null;
  }

  const open = expanded[path] ?? depth < 1;
  const collapsible = isObjectLike(value);
  const paddingLeft = depth * 16;

  if (!collapsible) {
    return (
      <div className="py-1.5 text-xs" style={{ paddingLeft }}>
        {label ? <span className="font-mono text-orange-700">{label}: </span> : null}
        <span className={clsx("break-all", typeof value === "string" ? "text-emerald-700" : "text-slate-700")}>
          {typeof value === "string" ? `"${value}"` : String(value)}
        </span>
      </div>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((item, index) => [`[${index}]`, item] as const)
    : Object.entries(value as Record<string, unknown>);

  return (
    <div>
      <button
        type="button"
        onClick={() => onToggle(path)}
        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-slate-700 hover:bg-slate-100/80"
        style={{ paddingLeft }}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 text-slate-500" /> : <ChevronRight className="h-3.5 w-3.5 text-slate-500" />}
        {label ? <span className="font-mono text-orange-700">{label}</span> : <span className="text-slate-500">root</span>}
        <span className="text-slate-500">{summarizeValue(value)}</span>
      </button>
      {open ? (
        <div>
          {entries.map(([entryLabel, entryValue]) => (
            <JsonTree
              key={`${path}-${entryLabel}`}
              value={entryValue}
              path={`${path}.${entryLabel}`}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              query={query}
              label={entryLabel}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
