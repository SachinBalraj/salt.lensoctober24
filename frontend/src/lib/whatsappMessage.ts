/**
 * The farmer-facing message the Module 12 panel shares over WhatsApp.
 *
 * Free of React and of `fetch`, so the exact text an operator reviews before
 * opening a chat can be unit tested against the recommendation it came from.
 *
 * **The message is assembled from the advice, never from the operator's typing.**
 * Every figure is read from the stored recommendation and the pan it was made for —
 * the action, the twin state, the mass balance, the rule and the configured harvest
 * target. A panel that let free text through would be a panel that could tell a
 * farmer something the rule never said.
 *
 * **A simulated figure is labelled as simulated, and a measured one as measured.**
 * "Simulated salinity after rain" and "Current salinity" are separate lines for the
 * same reason they are separate sections on screen: a farmer reading one number
 * beside a physical gauge has no way to know which is which otherwise.
 *
 * **What is absent is absent, not zero.** A twin state with no salinity yet says
 * "not recorded" rather than showing a blank a reader would take for zero, and the
 * no-model statement appears only when no estimate was read — because when one was,
 * its presence is the honest fact and a claim of absence would be false.
 */

import { adviceHasModelEstimate, describeBasis, type DecisionAdvice } from "@/lib/decisions";
import type { SaltPan } from "@/lib/pans";

/**
 * The fixed sentence telling the farmer what this is and is not.
 *
 * The same words the server puts in its own recommendation message. Held here rather
 * than fetched so the message is complete and testable without a request, and
 * worded identically on purpose: two surfaces phrasing the same guarantee differently
 * is how a reader stops recognising it as a guarantee.
 */
export const FARMER_MESSAGE_DISCLAIMER =
  "This is a decision-support recommendation, not a measured field outcome. " +
  "Please verify the current field conditions before taking any physical action.";

/** What a line says when the state this panel describes does not hold a value yet. */
export const NOT_RECORDED = "not recorded";

/** The exact statement used when no model estimate was behind the recommendation. */
export const NO_ML_STATEMENT =
  "No ML prediction or model estimate was used for this recommendation.";

/**
 * A number as a person would read it.
 *
 * Trailing zeros dropped so `285` stays `285` and `300.4` stays `300.4`. A full
 * float is kept on the recommendation; this is the same value in the form a farmer
 * reading a message can act on, not a rounded figure.
 */
function formatFigure(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function figureOrNotRecorded(value: number | null, unit: string): string {
  // No unit on the absence: "not recorded g/L" reads as a measurement of zero, and
  // the whole point is that there is no figure to attach a unit to.
  if (value === null) return NOT_RECORDED;
  return `${formatFigure(value)} ${unit}`;
}

/**
 * Build the message for one recommendation on one pan.
 *
 * Both arguments are read-only. The result is deterministic for a given advice and
 * pan, which is what lets a test assert on the exact text and lets an operator
 * recognise the same recommendation if it is shared twice.
 */
export function buildFarmerWhatsAppMessage(input: {
  pan: SaltPan;
  advice: DecisionAdvice;
}): string {
  const { pan, advice } = input;
  const { twin, simulation_result: sim } = advice;

  const lines: string[] = [
    "SaltLens DSS - Salt-pan recommendation",
    "",
    `Pan: ${pan.pan_name}`,
    `Recommendation: ${advice.recommendation}`,
    "",
    `Current salinity: ${figureOrNotRecorded(twin.salinity_g_l, "g/L")}`,
    `Water depth: ${figureOrNotRecorded(twin.water_depth_cm, "cm")}`,
    `Rainfall scenario: ${formatFigure(sim.rainfall_mm)} mm (${sim.rainfall_source})`,
    `Simulated salinity after rain: ${formatFigure(sim.post_rain_salinity_g_l)} g/L`,
    `Target harvest salinity: ${formatFigure(pan.target_harvest_salinity_g_l)} g/L`,
    "",
    `Decision rule: ${advice.rule_name} v${advice.rule_version_number}`,
    `Decision basis: ${describeBasis(advice)}`,
  ];

  // Only when it is true. Stating a model's absence on advice that read one would be
  // a false statement to the person deciding whether to harvest.
  if (!adviceHasModelEstimate(advice)) {
    lines.push(NO_ML_STATEMENT);
  }

  lines.push("", FARMER_MESSAGE_DISCLAIMER);

  return lines.join("\n");
}
