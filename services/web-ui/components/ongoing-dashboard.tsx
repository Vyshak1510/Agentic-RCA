"use client";

import { useMemo, useState } from "react";

import { LiveFlow } from "@/components/live-flow";
import { StatusBadge } from "@/components/status-badge";
import { InvestigationRecord } from "@/lib/types";

type Props = {
  incidents: InvestigationRecord[];
};

export function OngoingDashboard({ incidents }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(incidents[0]?.id ?? null);

  const selectedIncident = useMemo(
    () => incidents.find((incident) => incident.id === selectedId) ?? null,
    [incidents, selectedId]
  );

  if (!incidents.length) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600 shadow-panel">
        No active incidents right now.
      </div>
    );
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
      <aside className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-600">Active Incidents</h3>
        <div className="space-y-2">
          {incidents.map((incident) => (
            <button
              key={incident.id}
              onClick={() => setSelectedId(incident.id)}
              className={`w-full rounded-lg border p-3 text-left ${selectedId === incident.id ? "border-cyan bg-cyan/10" : "border-slate-200 bg-slate-50"}`}
            >
              <div className="mb-1 text-xs text-slate-500">{incident.id}</div>
              <div className="mb-2 text-sm font-semibold">{incident.alert.incident_key}</div>
              <div className="flex items-center justify-between">
                <StatusBadge status={incident.status} />
                <span className="text-xs text-slate-500">{incident.alert.source}</span>
              </div>
            </button>
          ))}
        </div>
      </aside>

      <section className="space-y-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
          <h2 className="text-lg font-semibold">{selectedIncident?.alert.incident_key}</h2>
          <p className="text-xs text-slate-500">{selectedIncident?.id}</p>
        </div>

        {selectedIncident ? (
          <LiveFlow
            investigationId={selectedIncident.id}
            incidentKey={selectedIncident.alert.incident_key}
            initialRunId={selectedIncident.active_run_id}
          />
        ) : null}
      </section>
    </div>
  );
}
