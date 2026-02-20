import Link from "next/link";

import { ErrorState } from "@/components/error-state";
import { StatusBadge } from "@/components/status-badge";
import { fetchInvestigations } from "@/lib/api";

type SearchParams = {
  status?: string;
  severity?: string;
  source?: string;
  page?: string;
};

export default async function PastIncidentsPage({ searchParams }: { searchParams: SearchParams }) {
  const page = Number(searchParams.page ?? "1");

  try {
    const response = await fetchInvestigations({
      page,
      page_size: 25,
      status: searchParams.status,
      severity: searchParams.severity,
      source: searchParams.source
    });

    return (
      <div className="space-y-5">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h2 className="mb-3 text-xl font-semibold">Past Incidents</h2>
          <form className="grid gap-3 md:grid-cols-4">
            <input name="source" defaultValue={searchParams.source} placeholder="source (newrelic/azure/otel)" className="rounded-md border border-slate-300 px-3 py-2 text-sm" />
            <input name="severity" defaultValue={searchParams.severity} placeholder="severity" className="rounded-md border border-slate-300 px-3 py-2 text-sm" />
            <input name="status" defaultValue={searchParams.status} placeholder="status" className="rounded-md border border-slate-300 px-3 py-2 text-sm" />
            <button type="submit" className="rounded-md bg-ink px-3 py-2 text-sm font-semibold text-white">Apply Filters</button>
          </form>
        </section>

        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-4 py-3">Incident</th>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">Severity</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Updated</th>
              </tr>
            </thead>
            <tbody>
              {response.items.map((incident) => (
                <tr key={incident.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link href={`/incidents/${incident.id}`} className="font-semibold text-ink hover:text-cyan">
                      {incident.alert.incident_key}
                    </Link>
                    <div className="text-xs text-slate-500">{incident.id}</div>
                  </td>
                  <td className="px-4 py-3 text-slate-700">{incident.alert.source}</td>
                  <td className="px-4 py-3 text-slate-700">{incident.alert.severity}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={incident.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-600">{new Date(incident.updated_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!response.items.length ? <div className="p-6 text-sm text-slate-500">No incidents matched this filter.</div> : null}
        </section>
      </div>
    );
  } catch (error) {
    return <ErrorState message={error instanceof Error ? error.message : "Unable to load incidents"} />;
  }
}
