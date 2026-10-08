import type { CleaningPreviewResult } from "@/services/projectService";

interface CleaningPreviewProps {
  preview: CleaningPreviewResult | null;
}

export default function CleaningPreview({ preview }: CleaningPreviewProps) {
  if (!preview) return null;
  return (
    <section className="space-y-3 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.03] p-4">
      <h4 className="text-sm font-semibold text-emerald-200">Cleaning Preview (not saved)</h4>
      <div className="grid gap-2 sm:grid-cols-4">
        {[
          ["Original rows", preview.original_row_count],
          ["New rows", preview.new_row_count],
          ["Rows removed", preview.rows_removed],
          ["Columns changed", preview.columns_changed.length],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-lg bg-black/20 p-3">
            <p className="text-xs text-slate-500">{label}</p>
            <p className="mt-1 text-lg font-semibold text-white">{Number(value).toLocaleString()}</p>
          </div>
        ))}
      </div>
      {preview.columns_changed.length > 0 && (
        <p className="text-xs text-slate-300">
          Changed columns: {preview.columns_changed.join(", ")}
        </p>
      )}
      <div className="overflow-x-auto rounded-lg border border-white/10">
        <table className="w-full text-left text-xs">
          <thead className="bg-white/5 text-slate-300">
            <tr>
              <th className="px-3 py-2">Step</th>
              <th className="px-3 py-2">Operation</th>
              <th className="px-3 py-2">Result</th>
            </tr>
          </thead>
          <tbody>
            {preview.applied_steps.map((step) => (
              <tr key={step.step_number} className="border-t border-white/5 text-slate-300">
                <td className="px-3 py-2">{step.step_number}</td>
                <td className="px-3 py-2">{step.step_name}</td>
                <td className="px-3 py-2">{step.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview.sample_rows.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-cyan-300">Preview first {preview.sample_rows.length} rows</summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-black/30 p-3 text-xs text-slate-300">
            {JSON.stringify(preview.sample_rows, null, 2)}
          </pre>
        </details>
      )}
    </section>
  );
}
