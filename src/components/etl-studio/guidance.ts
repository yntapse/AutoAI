/**
 * The one sentence the guide shows for the current stage ("what to do now").
 * Pure: it only reads the stage the app already computed from the real pipeline,
 * so it can never say something the pipeline does not support.
 */
import type { Stage } from "./guidedStages";

export interface GuidanceContext {
  hasOutput: boolean;
  sourceCount: number;
  joinCount: number;
}

export function instructionFor(stage: Stage, ctx: GuidanceContext): string {
  switch (stage.id) {
    case "select":
      if (stage.status === "complete") return "Tables are chosen. Continue to Join.";
      if (stage.status === "invalid") return "A chosen table has a problem. Select it on the canvas to read why, then fix or remove it.";
      if (ctx.sourceCount === 0) return "Pick the tables you need from the list on the left.";
      return "Choose a table for the empty Source step, or remove it.";

    case "join":
      if (stage.status === "not_needed") return "One table needs no join. Continue.";
      if (stage.status === "complete") return "Joins are set up. Validate will measure each one on the real data.";
      if (stage.status === "invalid") return "A join has errors. Select it on the canvas to read why, then fix its settings.";
      if (ctx.joinCount === 0) return "Tell the pipeline how your tables connect: accept a suggestion in the Suggested joins panel, or use New join… in the Steps list.";
      return "Finish the join: connect both inputs and choose the matching columns, and join any table that is still loose.";

    case "transform":
      if (stage.status === "invalid") return "A step has errors. Select it on the canvas to read why, then fix its settings.";
      if (stage.status === "incomplete") return "Finish the step you started: choose its columns or conditions, or remove it.";
      if (stage.status === "complete") return "Transform steps are set up. Continue to Validate.";
      return "Optional: clean or reshape the data, or skip ahead to Validate.";

    case "validate":
      switch (stage.status) {
        case "measurement_required":
          return "Measure each join on the real data: open it, then press Measure this join in its settings.";
        case "measurement_outdated":
          return "A join changed after it was measured. Open it and measure it again.";
        case "not_validated":
          return "Preview the final dataset to see real rows.";
        case "outdated":
          return "A setting changed after the last preview. Preview the final dataset again.";
        case "invalid":
          return "The final dataset has an error. Select the Output step to read it, then fix it.";
        case "review_required":
          return "Review the repeated-rows warning. Accept it only if the repetition is what you intend.";
        case "complete":
          return "Validated on real data. Continue to the final dataset.";
        default:
          if (!ctx.hasOutput) return "Add an Output dataset step at the end of the flow, using Output dataset in the Steps list.";
          if (/earlier stages/i.test(stage.summary)) return "Finish the earlier stages first.";
          return "Connect the last step to the Output dataset step.";
      }

    case "final":
      return stage.status === "complete"
        ? "Your dataset definition has been checked on real data. Saving, publishing and sending to AutoML are not available yet."
        : "Finish Validate first.";
  }
}
