interface DatasetReadyCardProps {
  rows: number | null | undefined;
  columns: number | null | undefined;
  fileId: string | null | undefined;
  projectId?: string | null;
  projectName?: string | null;
}

function displayCount(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString()
    : "Not Available";
}

export default function DatasetReadyCard({
  rows,
  columns,
  fileId,
  projectId,
  projectName,
}: DatasetReadyCardProps) {
  return (
    <section
      role="status"
      className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.06] p-4"
    >
      <h4 className="mb-3 text-sm font-semibold text-emerald-300">
        Dataset Ready
      </h4>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div className="min-w-0">
          <dt className="text-xs text-slate-500">Project</dt>
          <dd className="mt-1 break-words text-sm text-white">
            {projectName || "Registered project"}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-slate-500">Project ID</dt>
          <dd className="mt-1 break-all text-sm text-white">
            {projectId || "Not Available"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Rows</dt>
          <dd className="mt-1 text-sm text-white">{displayCount(rows)}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Columns</dt>
          <dd className="mt-1 text-sm text-white">{displayCount(columns)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-slate-500">File</dt>
          <dd className="mt-1 break-all text-sm text-white">
            {fileId || "Not Available"}
          </dd>
        </div>
      </dl>
    </section>
  );
}