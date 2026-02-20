import { ErrorState } from "@/components/error-state";
import { LiveFlow } from "@/components/live-flow";
import { RerunButton } from "@/components/rerun-button";
import { StatusBadge } from "@/components/status-badge";
import { fetchInvestigation } from "@/lib/api";

export default async function IncidentDetailPage({ params }: { params: { id: string } }) {
  try {
    const incident = await fetchInvestigation(params.id);

    return (
      <div className="space-y-5">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold">{incident.alert.incident_key}</h2>
              <p className="text-xs text-slate-500">{incident.id}</p>
            </div>
            <StatusBadge status={incident.status} />
          </div>
          <RerunButton incidentId={incident.id} />
        </section>

        <LiveFlow
          investigationId={incident.id}
          incidentKey={incident.alert.incident_key}
          initialRunId={incident.active_run_id}
        />

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-700">RCA Report</h3>
          {incident.report ? (
            <div className="space-y-4 text-sm">
              <div>
                <h4 className="font-semibold">Likely Cause</h4>
                <p>{incident.report.likely_cause}</p>
              </div>
              <div>
                <h4 className="font-semibold">Top Hypotheses</h4>
                <ul className="list-disc space-y-1 pl-6">
                  {incident.report.top_hypotheses.map((hypothesis, idx) => (
                    <li key={`${hypothesis.statement}-${idx}`}>
                      {hypothesis.statement} ({Math.round(hypothesis.confidence * 100)}%)
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Report not yet available for this incident.</p>
          )}
        </section>
      </div>
    );
  } catch (error) {
    return <ErrorState message={error instanceof Error ? error.message : "Unable to load incident"} />;
  }
}
