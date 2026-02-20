export function LoadingState({ label }: { label: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600 shadow-panel">
      {label}
    </div>
  );
}
