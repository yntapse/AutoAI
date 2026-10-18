/**
 * Guided stages, worked out from the real pipeline.
 *
 * Nothing here is stored. The five stages are recomputed from the canvas (the
 * nodes and edges), the server's validation answer for each node, and the
 * preview runs that have really happened. Change the canvas and the stages
 * change with it; there is no separate "wizard state" that could disagree with
 * the pipeline.
 *
 * Three different words are used on purpose:
 *   Configured - the join has valid inputs and matching columns (server check).
 *   Measured   - join diagnostics really ran on the data, on the CURRENT settings.
 *   Validated  - every needed join is measured AND the final dataset was
 *                previewed successfully on the current settings.
 *
 * This file only reads facts. It never runs anything, and it never claims a
 * result that was not produced: "complete" for Validate needs a real preview of
 * the output step on the current settings.
 */

export type StageId = "select" | "join" | "transform" | "validate" | "final";

export type StageStatus =
  | "complete"
  | "incomplete"
  | "invalid"
  | "measurement_required" // a join is configured but its match has not been measured on real data
  | "measurement_outdated" // a join was measured, then something it depends on changed
  | "review_required" // checks passed, but repeated rows (fan-out) need the user's acknowledgement: a warning, not an error
  | "not_validated" // looks fine, but real rows have not been previewed yet
  | "outdated" // was previewed, but a setting changed since
  | "optional" // nothing added, and nothing is needed
  | "not_needed";

export interface StageNode {
  id: string;
  type: string;
  config: Record<string, unknown>;
}
export interface StageEdge {
  source: string;
  target: string;
}
/** What the Builder already shows on each node (server validation + latest preview run). */
export interface NodeView {
  status: "not_configured" | "invalid" | "blocked" | "ready" | "running" | "successful" | "failed";
  outdated: boolean;
}

/** The latest successful measurement of one join. Absent = never measured. */
export interface JoinMeasurement {
  /** True when the pipeline changed after it was measured. */
  outdated: boolean;
  /** The measured join repeats rows of its main side (fan-out). */
  multipliesMainSide: boolean;
  extraRows: number;
  /** The user acknowledged this exact measurement of this exact configuration. */
  acknowledged?: boolean;
  /** The measured numbers, exactly as the server reported them. */
  details?: {
    joinType: string;
    mainSide: string;
    leftRows: number;
    rightRows: number;
    matchedPairs: number;
    outputRows: number;
    largestLeftGroup: number;
    largestRightGroup: number;
  };
}

export type JoinState = "not_configured" | "configured" | "measured" | "measurement_outdated";

export interface JoinCheck {
  id: string;
  state: JoinState;
  /** Plain-language line shown next to the join. */
  text: string;
  /** Set when the measurement found repeated rows (fan-out). A warning, never an error. */
  fanOut: FanOut | null;
}

export interface FanOut {
  /** The user has acknowledged exactly this measurement of exactly this join configuration. */
  acknowledged: boolean;
  /** Numbers taken straight from the measurement; nothing is estimated. */
  evidence: string[];
}

/** Why repeated rows are a warning and not an error. */
export const FAN_OUT_EXPLANATION =
  "Repeated rows can be correct. For example, one customer with many orders is fine when the dataset should have one row per order. " +
  "They are a problem when you expect one row per customer, because totals can then be counted more than once. " +
  "Only you know the intended grain of this dataset, so this is a warning to review, not an error.";

export interface StageInput {
  nodes: readonly StageNode[];
  edges: readonly StageEdge[];
  views: Readonly<Record<string, NodeView | undefined>>;
  /** Rows of the latest successful preview of each node, when one exists. */
  previewedRows: Readonly<Record<string, { rowsShown: number; hasMore: boolean } | undefined>>;
  /** Join node id -> its latest successful measurement. */
  measurements: Readonly<Record<string, JoinMeasurement | undefined>>;
  /** False while the server check has not answered (or is unavailable). */
  checked: boolean;
  /** Source step id -> the data source it reads from. Only used for wording. */
  sourceLabels?: Readonly<Record<string, string>>;
}

export interface Stage {
  id: StageId;
  label: string;
  status: StageStatus;
  summary: string;
  problems: string[];
  /** Things worth a look that do not block the stage. */
  notes: string[];
}

export const STAGE_ORDER: readonly StageId[] = ["select", "join", "transform", "validate", "final"];

export const STAGE_LABELS: Record<StageId, string> = {
  select: "Select Tables",
  join: "Join",
  transform: "Transform",
  validate: "Validate",
  final: "Final Dataset",
};

export const STATUS_TEXT: Record<StageStatus, string> = {
  complete: "Complete",
  incomplete: "Incomplete",
  invalid: "Has errors",
  review_required: "Review required",
  measurement_required: "Join measurement required",
  measurement_outdated: "Measurement outdated",
  not_validated: "Not validated",
  outdated: "Outdated",
  optional: "Optional",
  not_needed: "Not needed",
};

const TRANSFORMS = new Set(["filter", "drop_columns", "rename_columns", "remove_duplicates", "handle_missing"]);
const NEEDS_ATTENTION: ReadonlySet<StageStatus> = new Set([
  "incomplete",
  "invalid",
  "measurement_required",
  "measurement_outdated",
  "review_required",
  "not_validated",
  "outdated",
]);

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Every node that can be reached by following edges forward from `from`. */
function reachable(edges: readonly StageEdge[], from: string): Set<string> {
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length) {
    const id = stack.pop() as string;
    for (const e of edges) {
      if (e.source === id && !seen.has(e.target)) {
        seen.add(e.target);
        stack.push(e.target);
      }
    }
  }
  return seen;
}

/**
 * The joins that must be measured: those that feed the output (or every join,
 * while there is no output yet), and the state of each.
 */
export function joinChecks(input: Pick<StageInput, "nodes" | "edges" | "views" | "measurements">): JoinCheck[] {
  const { nodes, edges, views, measurements } = input;
  const output = nodes.find((n) => n.type === "output");
  const needed = nodes.filter((n) => n.type === "join" && (!output || reachable(edges, n.id).has(output.id)));
  return needed.map((j) => {
    const status = views[j.id]?.status;
    const configured = status === "ready" || status === "successful" || status === "failed" || status === "running";
    if (!configured) return { id: j.id, state: "not_configured", text: "Not configured yet.", fanOut: null };
    const m = measurements[j.id];
    if (!m) return { id: j.id, state: "configured", text: "Configured. Join measurement required.", fanOut: null };
    if (m.outdated) {
      return { id: j.id, state: "measurement_outdated", text: "Measurement outdated: the pipeline changed after it was measured. Measure again.", fanOut: null };
    }
    if (!m.multipliesMainSide) return { id: j.id, state: "measured", text: "Measured on the data.", fanOut: null };
    const n = (x: number) => x.toLocaleString("en-US");
    const d = m.details;
    const evidence = [`${j.id} repeats rows of its ${d?.mainSide ?? "main"} side: ${n(m.extraRows)} extra rows.`];
    if (d) {
      evidence.push(
        `${d.joinType} join: ${n(d.leftRows)} left rows and ${n(d.rightRows)} right rows give ${n(d.matchedPairs)} matching pairs and ${n(d.outputRows)} result rows.`,
        `Largest group of rows sharing one key: ${n(d.largestLeftGroup)} on the left, ${n(d.largestRightGroup)} on the right.`
      );
    }
    const acknowledged = m.acknowledged === true;
    return {
      id: j.id,
      state: "measured",
      text: acknowledged ? "Measured on the data. Repeated rows acknowledged." : "Measured on the data. Repeated rows found: your review is required.",
      fanOut: { acknowledged, evidence },
    };
  });
}

export function computeStages(input: StageInput): Stage[] {
  const { nodes, edges, views, previewedRows, checked } = input;
  const checks = joinChecks(input);
  const view = (id: string) => views[id];
  const label = (n: StageNode) => n.id;
  const notChecked = checked ? [] : ["The server has not checked this pipeline yet, so step statuses may be missing."];

  // ---- 1. Select tables
  const sources = nodes.filter((n) => n.type === "source");
  const select: Stage = { id: "select", label: STAGE_LABELS.select, status: "complete", summary: "", problems: [], notes: [] };
  if (sources.length === 0) {
    select.status = "incomplete";
    select.summary = "No tables on the canvas yet.";
    select.problems.push("Pick at least one table from the list.");
  } else if (sources.some((s) => typeof s.config.table_id !== "string" || !s.config.table_id)) {
    select.status = "incomplete";
    select.summary = "A Source step has no table chosen.";
    select.problems.push("Choose a table for every Source step.");
  } else if (sources.some((s) => view(s.id)?.status === "invalid")) {
    select.status = "invalid";
    select.summary = "A selected table has a problem.";
    select.problems.push(...sources.filter((s) => view(s.id)?.status === "invalid").map((s) => `${label(s)} has errors; select it to see why.`));
  } else {
    const kinds = new Set(sources.map((s) => input.sourceLabels?.[s.id]).filter((x): x is string => Boolean(x)));
    if (kinds.size > 1) {
      select.summary = `${plural(sources.length, "table")} from ${kinds.size} data sources.`;
      select.notes.push(
        "Tables from different data sources cannot be joined or run together here. Previews and measurements work for tables of the main data source only."
      );
    } else {
      select.summary = `${plural(sources.length, "table")} on the canvas.`;
    }
  }

  // ---- 2. Join
  const joins = nodes.filter((n) => n.type === "join");
  const join: Stage = { id: "join", label: STAGE_LABELS.join, status: "complete", summary: "", problems: [], notes: [] };
  if (sources.length === 0) {
    join.status = "incomplete";
    join.summary = "Select tables first.";
  } else if (sources.length === 1 && joins.length === 0) {
    join.status = "not_needed";
    join.summary = "One table needs no join.";
  } else {
    const joined = new Set<string>();
    for (const j of joins) for (const s of sources) if (reachable(edges, s.id).has(j.id)) joined.add(s.id);
    const loose = sources.filter((s) => !joined.has(s.id));
    const bad = joins.filter((j) => view(j.id)?.status === "invalid");
    const open = joins.filter((j) => {
      const st = view(j.id)?.status;
      return st === undefined || st === "not_configured" || st === "blocked";
    });
    if (joins.length === 0) {
      join.status = "incomplete";
      join.summary = `${plural(sources.length, "table")} not joined yet.`;
      join.problems.push("Accept a suggested join or add a join step.");
    } else if (bad.length) {
      join.status = "invalid";
      join.summary = `${plural(bad.length, "join")} with errors.`;
      join.problems.push(...bad.map((j) => `${label(j)} has errors; select it to see why.`));
      if (new Set(Object.values(input.sourceLabels ?? {})).size > 1) {
        join.problems.push(
          "Tables from different data sources cannot be joined here. Use tables from one source, or join them in a view or a copy inside one database."
        );
      }
    } else if (open.length) {
      join.status = "incomplete";
      join.summary = `${plural(open.length, "join")} not finished.`;
      join.problems.push(...open.map((j) => `${label(j)} needs both inputs connected and its matching columns chosen.`));
    } else if (loose.length) {
      join.status = "incomplete";
      join.summary = `${plural(loose.length, "table")} not part of any join.`;
      join.problems.push(...loose.map((s) => `${label(s)} is not joined to anything yet.`));
    } else {
      const measured = checks.filter((c) => c.state === "measured").length;
      join.summary = `${plural(joins.length, "join")} configured, ${measured} measured.`;
    }
  }

  // ---- 3. Transform (optional)
  const steps = nodes.filter((n) => TRANSFORMS.has(n.type));
  const transform: Stage = { id: "transform", label: STAGE_LABELS.transform, status: "complete", summary: "", problems: [], notes: [] };
  if (steps.length === 0) {
    transform.status = "optional";
    transform.summary = "No cleaning or reshaping steps. Optional.";
  } else if (steps.some((s) => view(s.id)?.status === "invalid")) {
    transform.status = "invalid";
    transform.summary = "A step has errors.";
    transform.problems.push(...steps.filter((s) => view(s.id)?.status === "invalid").map((s) => `${label(s)} has errors; select it to see why.`));
  } else if (steps.some((s) => ["not_configured", "blocked", undefined].includes(view(s.id)?.status))) {
    transform.status = "incomplete";
    transform.summary = "A step is not finished.";
    transform.problems.push(
      ...steps.filter((s) => ["not_configured", "blocked", undefined].includes(view(s.id)?.status)).map((s) => `${label(s)} is not finished.`)
    );
  } else {
    transform.summary = `${plural(steps.length, "step")} set up.`;
  }

  // ---- 4. Validate
  const output = nodes.find((n) => n.type === "output");
  const validate: Stage = { id: "validate", label: STAGE_LABELS.validate, status: "incomplete", summary: "", problems: [], notes: [] };
  const earlierBlocked = [select, join, transform].some((s) => s.status === "incomplete" || s.status === "invalid");
  if (!output) {
    validate.summary = "No Output dataset step yet.";
    validate.problems.push("Add an Output dataset step at the end of the flow.");
  } else if (earlierBlocked) {
    validate.summary = "Finish the earlier stages first.";
  } else if (checks.some((c) => c.state === "configured" || c.state === "measurement_outdated")) {
    const missing = checks.filter((c) => c.state === "configured");
    const stale = checks.filter((c) => c.state === "measurement_outdated");
    validate.status = missing.length ? "measurement_required" : "measurement_outdated";
    validate.summary = missing.length
      ? `Join measurement required for ${missing.map((c) => c.id).join(", ")}.`
      : `Measurement outdated for ${stale.map((c) => c.id).join(", ")}.`;
    validate.problems.push(...checks.filter((c) => c.state === "configured" || c.state === "measurement_outdated").map((c) => `${c.id}: ${c.text}`));
  } else {
    const v = view(output.id);
    const rows = previewedRows[output.id];
    if (!v || v.status === "not_configured" || v.status === "blocked") {
      validate.summary = "The Output step is not connected to the flow yet.";
      validate.problems.push("Connect the last step to the Output dataset step.");
    } else if (v.status === "invalid") {
      validate.status = "invalid";
      validate.summary = "The Output step has errors.";
    } else if (v.status === "failed") {
      validate.status = "invalid";
      validate.summary = "The last preview of the final dataset failed.";
      validate.problems.push("Select the Output step to read the error, fix it, then preview again.");
    } else if (v.status === "running") {
      validate.summary = "A preview is running.";
    } else if (v.status === "ready" && v.outdated) {
      validate.status = "outdated";
      validate.summary = "A setting changed after the last preview.";
      validate.problems.push("Preview the final dataset again.");
    } else if (v.status === "ready") {
      validate.status = "not_validated";
      validate.summary = "The structure passes the server's checks, but no real rows have been previewed yet.";
      validate.problems.push("Preview the final dataset to see real rows.");
    } else {
      const open = checks.filter((c) => c.fanOut && !c.fanOut.acknowledged);
      const base = rows
        ? `${checks.length ? "Joins measured. " : ""}The final preview returned ${plural(rows.rowsShown, "row")}${rows.hasMore ? " (more exist)" : ""}.`
        : "The final dataset was previewed.";
      if (open.length) {
        // Technically valid, but not approved until the user has looked at the warning.
        validate.status = "review_required";
        validate.summary = `${base} Review the repeated rows in ${open.map((c) => c.id).join(", ")} before approving.`;
        validate.problems.push(...open.map((c) => `${c.id}: acknowledge the repeated-rows warning (or fix the join).`));
      } else {
        validate.status = "complete";
        validate.summary = base;
      }
    }
  }

  // ---- 5. Final dataset
  const final: Stage = { id: "final", label: STAGE_LABELS.final, status: "incomplete", summary: "", problems: [], notes: [] };
  if (validate.status === "complete") {
    final.status = "complete";
    final.summary = "The dataset definition has been previewed on real data.";
  } else {
    final.summary = validate.status === "review_required" ? "Not fully approved: the repeated-rows warning has not been acknowledged." : "Validate the pipeline first.";
  }

  for (const c of checks) if (c.fanOut?.acknowledged) validate.notes.push(`${c.id}: repeated rows acknowledged by you.`);
  if (!output) for (const c of checks) if (c.state === "configured" || c.state === "measurement_outdated") validate.problems.push(`${c.id}: ${c.text}`);

  const stages = [select, join, transform, validate, final];
  if (notChecked.length) select.problems.push(...notChecked);
  return stages;
}

/** The first stage that still needs attention, or the last stage if none does. */
export function currentStage(stages: readonly Stage[]): StageId {
  return stages.find((s) => NEEDS_ATTENTION.has(s.status))?.id ?? "final";
}
