export interface ReconciliationCheck {
  check: string;
  expected?: unknown;
  actual?: unknown;
  passed?: boolean;
}

export interface ReconciliationData {
  available?: boolean;
  passed?: boolean | null;
  checks?: ReconciliationCheck[];
  message?: string;
}

interface ReconciliationReportProps {
  reconciliation: ReconciliationData | null | undefined;
  mode?: "preview" | "build";
}

function displayValue(value: unknown) {
  return value === null || value === undefined || value === ""
    ? "Not Available"
    : String(value);
}

function difference(expected: unknown, actual: unknown) {
  if (expected === null || expected === undefined || actual === null || actual === undefined) {
    return "Not Available";
  }

  const expectedValue = Number(expected);
  const actualValue = Number(actual);
  if (!Number.isFinite(expectedValue) || !Number.isFinite(actualValue)) {
    return "Not Available";
  }

  return String(actualValue - expectedValue);
}

export default function ReconciliationReport({
  reconciliation,
  mode = "build",
}: ReconciliationReportProps) {
  const checks = reconciliation?.checks ?? [];
  const totals = checks.filter((item) =>
    item.check.startsWith("source_grain_total:"),
  );
  const status = reconciliation?.available === false
    ? "NOT AVAILABLE"
    : reconciliation?.passed === true
      ? mode === "preview" ? "PREVIEW PASS" : "PASS"
      : reconciliation?.passed === false
        ? mode === "preview" ? "PREVIEW FAIL" : "FAIL"
        : "NOT AVAILABLE";
  const statusClass =
    status === "PASS" || status === "PREVIEW PASS"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
      : status === "FAIL" || status === "PREVIEW FAIL"
        ? "border-red-500/30 bg-red-500/10 text-red-300"
        : "border-white/10 bg-white/[0.04] text-slate-400";

  return (
    <section className="rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-white">
          {mode === "preview" ? "Preview Reconciliation" : "Build Verification"}
        </h4>
        <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusClass}`}>
          {status}
        </span>
      </div>

      {reconciliation?.message && (
        <p className="mb-3 text-xs text-slate-400">{reconciliation.message}</p>
      )}

      {totals.length > 0 ? (
        <div className="space-y-3">
          {totals.map((item) => (
            <div
              key={item.check}
              className="grid gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 sm:grid-cols-4"
            >
              <div>
                <div className="text-xs text-slate-500">Measure</div>
                <div className="mt-1 break-words text-sm text-slate-200">
                  {item.check.slice("source_grain_total:".length)}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Source Total</div>
                <div className="mt-1 text-sm text-white">
                  {displayValue(item.expected)}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Dataset Total</div>
                <div className="mt-1 text-sm text-white">
                  {displayValue(item.actual)}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Difference</div>
                <div className="mt-1 text-sm text-white">
                  {difference(item.expected, item.actual)}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            ["Source Total", "Not Available"],
            ["Dataset Total", "Not Available"],
            ["Difference", "Not Available"],
          ].map(([label, value]) => (
            <div
              key={label}
              className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3"
            >
              <div className="text-xs text-slate-500">{label}</div>
              <div className="mt-1 text-sm text-slate-300">{value}</div>
            </div>
          ))}
        </div>
      )}

      {checks.length > 0 && (
        <div className="mt-3 space-y-1 border-t border-white/[0.06] pt-3">
          {checks.map((item, index) => (
            <div
              key={`${item.check}-${index}`}
              className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-slate-400"
            >
              <span>{item.check}</span>
              <span className={item.passed ? "text-emerald-300" : "text-red-300"}>
                {item.passed === true ? "PASS" : item.passed === false ? "FAIL" : "Not Available"}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}