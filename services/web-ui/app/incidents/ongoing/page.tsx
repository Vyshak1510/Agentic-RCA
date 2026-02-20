import { OngoingDashboard } from "@/components/ongoing-dashboard";
import { fetchInvestigations } from "@/lib/api";

export default async function OngoingIncidentsPage() {
  const response = await fetchInvestigations({ page: 1, page_size: 25 });
  const active = response.items.filter((incident) => incident.status === "pending" || incident.status === "running");

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <h2 className="text-xl font-semibold">Ongoing Incidents</h2>
        <p className="text-sm text-slate-600">Live graph updates stream from run-level SSE events.</p>
      </section>
      <OngoingDashboard incidents={active} />
    </div>
  );
}
