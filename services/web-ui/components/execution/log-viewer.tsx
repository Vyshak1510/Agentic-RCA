"use client";

import clsx from "clsx";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useDeferredValue, useMemo, useRef, useState } from "react";

import { FilterChip, SearchBar } from "@/components/execution/execution-primitives";
import { ExecutionLog, ExecutionLogLevel, formatTimestamp } from "@/lib/execution-trace";

const levelOrder: ExecutionLogLevel[] = ["debug", "info", "warn", "error"];

const levelStyles: Record<ExecutionLogLevel, string> = {
  debug: "text-slate-400",
  info: "text-sky-700",
  warn: "text-amber-700",
  error: "text-rose-700",
};

export function LogViewer({ logs, scrollMode = "inner" }: { logs: ExecutionLog[]; scrollMode?: "inner" | "outer" }) {
  const [query, setQuery] = useState("");
  const [levels, setLevels] = useState<Set<ExecutionLogLevel>>(new Set(levelOrder));
  const parentRef = useRef<HTMLDivElement | null>(null);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const fillsPanel = scrollMode === "inner";

  const filtered = useMemo(() => {
    return logs.filter((entry) => {
      if (!levels.has(entry.level)) {
        return false;
      }
      if (!deferredQuery) {
        return true;
      }
      return `${entry.level} ${entry.message} ${entry.source ?? ""}`.toLowerCase().includes(deferredQuery);
    });
  }, [deferredQuery, levels, logs]);

  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 62,
    overscan: 8,
  });

  const toggleLevel = (level: ExecutionLogLevel) => {
    setLevels((current) => {
      const next = new Set(current);
      if (next.has(level)) {
        next.delete(level);
      } else {
        next.add(level);
      }
      return next.size > 0 ? next : new Set(levelOrder);
    });
  };

  return (
    <div className={clsx("flex min-h-0 flex-col gap-3", fillsPanel && "h-full")}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SearchBar value={query} onChange={setQuery} placeholder="Search logs" />
        <div className="flex flex-wrap gap-2">
          {levelOrder.map((level) => (
            <FilterChip key={level} active={levels.has(level)} onClick={() => toggleLevel(level)}>
              {level}
            </FilterChip>
          ))}
        </div>
      </div>

      <div ref={fillsPanel ? parentRef : null} className={clsx("rounded-[20px] border border-slate-200 bg-white/88", fillsPanel ? "min-h-0 flex-1 overflow-auto" : "overflow-hidden")}>
        {fillsPanel ? (
          <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
            {virtualizer.getVirtualItems().map((item) => {
              const entry = filtered[item.index];
              return (
                <div
                  key={entry.id}
                  className="absolute left-0 top-0 w-full px-3 py-2"
                  style={{ transform: `translateY(${item.start}px)` }}
                >
                  <LogCard entry={entry} />
                </div>
              );
            })}
          </div>
        ) : (
          <div className="space-y-3 px-3 py-3">
            {filtered.map((entry) => (
              <LogCard key={entry.id} entry={entry} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function LogCard({ entry }: { entry: ExecutionLog }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-[#fcfbf8] px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className={clsx("rounded-full border border-slate-200 bg-white px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em]", levelStyles[entry.level])}>
            {entry.level}
          </span>
          <span className="text-xs text-slate-500">{formatTimestamp(entry.timestamp)}</span>
          {entry.source ? <span className="text-xs text-slate-500">{entry.source}</span> : null}
        </div>
      </div>
      <p className="mt-2 text-sm text-slate-800">{entry.message}</p>
    </div>
  );
}
