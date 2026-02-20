"use client";

import { useState } from "react";

import { rerunInvestigation } from "@/lib/api";

export function RerunButton({ incidentId }: { incidentId: string }) {
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function handleRerun() {
    setLoading(true);
    setMessage(null);
    try {
      const response = await rerunInvestigation(incidentId);
      setMessage(`Rerun requested: ${response.status} (${response.run_id})`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Rerun failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={handleRerun}
        disabled={loading}
        className="rounded-md bg-ink px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
      >
        {loading ? "Requesting..." : "Rerun Investigation"}
      </button>
      {message ? <span className="text-xs text-slate-600">{message}</span> : null}
    </div>
  );
}
