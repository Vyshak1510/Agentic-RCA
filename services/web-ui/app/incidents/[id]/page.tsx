import { ErrorState } from "@/components/error-state";
import { LiveFlow } from "@/components/live-flow";
import { fetchInvestigation } from "@/lib/api";

export default async function IncidentDetailPage({ params }: { params: { id: string } }) {
  try {
    const incident = await fetchInvestigation(params.id);

    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <LiveFlow
          investigationId={incident.id}
          incidentKey={incident.alert.incident_key}
          initialRunId={incident.active_run_id}
          initialReport={incident.report ?? null}
        />
      </div>
    );
  } catch (error) {
    return <ErrorState message={error instanceof Error ? error.message : "Unable to load incident"} />;
  }
}
