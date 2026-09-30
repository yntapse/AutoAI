import type { ExplorerRelationship } from "./DatabaseExplorer";
import type { PreviewJoinHop, PreviewJoinPath } from "./LineageGraph";

interface JoinPreviewProps {
  paths: PreviewJoinPath[];
  relationships: ExplorerRelationship[];
}

function getImpact(hops: PreviewJoinHop[]) {
  if (hops.some((hop) => hop.row_multiplication === true)) {
    return {
      risk: "Fan-out detected",
      impact: "May increase intermediate row count",
      color: "text-amber-300",
    };
  }

  if (hops.length > 0 && hops.every((hop) => hop.row_multiplication === false)) {
    return {
      risk: "No fan-out detected",
      impact: "No row multiplication indicated",
      color: "text-emerald-300",
    };
  }

  return {
    risk: "Not measured",
    impact: "Expected row impact is not available",
    color: "text-slate-400",
  };
}

function HopDetails({ hops }: { hops: PreviewJoinHop[] }) {
  return (
    <div className="mt-3 space-y-2">
      {hops.map((hop, index) => (
        <div
          key={`${hop.left_table}.${hop.left_column}-${hop.right_table}.${hop.right_column}-${index}`}
          className="rounded-lg border border-white/[0.06] bg-black/20 p-3"
        >
          <div className="break-words text-xs text-slate-300">
            {hop.left_table}.{hop.left_column}
            <span className="px-2 text-cyan-400">-&gt;</span>
            {hop.right_table}.{hop.right_column}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            <span>Join type: {hop.join_type || "Not specified"}</span>
            <span>Cardinality: {hop.cardinality || "Not measured"}</span>
            <span>
              Match rate: {hop.match_rate == null ? "Not measured" : `${hop.match_rate}%`}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function JoinPreview({ paths, relationships }: JoinPreviewProps) {
  const hasPlanPaths = paths.length > 0;

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-3 text-sm font-semibold text-white">Join Preview</h4>

      {hasPlanPaths ? (
        <div className="space-y-3">
          {paths.map((path, index) => {
            const impact = getImpact(path.hops);
            return (
              <article
                key={`${path.entity_table}-${path.source_table}-${index}`}
                className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm text-white">
                    {path.entity_table} <span className="text-cyan-400">to</span>{" "}
                    {path.source_table}
                  </div>
                  <span className="text-xs text-slate-500">
                    {path.verified ? "Verified" : "Unverified"}
                  </span>
                </div>
                <HopDetails hops={path.hops} />
                <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                  <div>
                    <span className="text-slate-500">Fan-out risk: </span>
                    <span className={impact.color}>{impact.risk}</span>
                  </div>
                  <div>
                    <span className="text-slate-500">Expected row impact: </span>
                    <span className="text-slate-300">{impact.impact}</span>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : relationships.length > 0 ? (
        <div className="space-y-3">
          {relationships.map((relationship, index) => {
            const hop: PreviewJoinHop = relationship;
            const impact = getImpact([hop]);
            return (
              <article
                key={`${relationship.left_table}-${relationship.right_table}-${index}`}
                className="rounded-lg border border-white/[0.08] bg-white/[0.02] p-3"
              >
                <div className="text-xs text-slate-300">
                  {relationship.left_table}.{relationship.left_column}
                  <span className="px-2 text-cyan-400">-&gt;</span>
                  {relationship.right_table}.{relationship.right_column}
                </div>
                <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                  <div>
                    <span className="text-slate-500">Join type: </span>
                    <span className="text-slate-300">
                      {relationship.join_type || "Not specified"}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500">Fan-out risk: </span>
                    <span className={impact.color}>{impact.risk}</span>
                  </div>
                  <div className="sm:col-span-2">
                    <span className="text-slate-500">Expected row impact: </span>
                    <span className="text-slate-300">{impact.impact}</span>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-slate-500">
          Generate a plan to see relationship paths.
        </p>
      )}
    </section>
  );
}