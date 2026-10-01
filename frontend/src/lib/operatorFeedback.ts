/**
 * Module 14: operator feedback — types, labels and formatting.
 *
 * Free of React and of `fetch`, so the rules the decision panel depends on can be
 * unit tested directly.
 *
 * The one idea this module enforces is that **a recorded decision is not a
 * measurement**. It is the operator's disposition of the advice — accepted,
 * modified or rejected — plus the closed-vocabulary action they actually took,
 * their note and the moment they decided. It is a record of a human decision, and
 * it is the only step on the screen that is one.
 *
 * Two details carry the trace:
 *
 * **The pan is read from the stored advice, never from the client.** A decision
 * cannot be filed against a different pan than the recommendation it answers,
 * because the pan comes off the stored record. The disposition and the action
 * are separate facts: accepting records how the advice was received, and the
 * action field is a second, honest question. Leave it blank and the record says
 * no physical action followed; name an action and the record says you took it.
 * Neither is ever filed on the operator's behalf, because a record that says an
 * intervention happened must be one that actually did.
 *
 * **A decision is the seventh step, not the last one.** The record carries the
 * identifiers of the earlier stages — observation, twin, simulation, prediction,
 * explanation, recommendation — and the later outcome links back to it by its
 * `decision_id`. {@link trace} turns that chain into rows a component can render.
 */

import type { DecisionAction, DecisionAdvice } from "@/lib/decisions";
import type { EventOutcome } from "@/lib/outcomes";
import type { ProvenanceKind } from "@/lib/dashboard";

/** Every disposition an operator may record. Nothing else is a decision. */
export const OPERATOR_DECISIONS = ["accepted", "modified", "rejected"] as const;

export type OperatorDecision = (typeof OPERATOR_DECISIONS)[number];

/** Plain words for each disposition, as an operator reads them. */
export const DECISION_PRESENT_TENSE: Record<OperatorDecision, string> = {
  accepted: "Accepted",
  modified: "Modified",
  rejected: "Rejected",
};

/** The one word that describes how the advice was received. */
export function describeDecision(decision: OperatorDecision): string {
  return DECISION_PRESENT_TENSE[decision];
}

/**
 * The stored recommendation a decision answers, carrying its trace identities.
 *
 * Mirrored from the backend's `FeedbackRecommendationRead` block, so a single
 * decision row re-links observation -> twin -> simulation -> prediction ->
 * explanation -> recommendation without a second query.
 */
export type OperatorDecisionRecommendation = {
  recommendation_id: string;
  action: DecisionAction | null;
  issued_at: string;
  basis: string | null;
  priority: string | null;
  simulation_run_id: string | null;
  ml_prediction_id: string | null;
  explanation_id: string | null;
  twin_id: string | null;
  twin_as_of: string | null;
  observation_id: string | null;
  observation_timestamp: string | null;
  weather_forecast_id: string | null;
  rule_name: string | null;
  rule_version: number | null;
  specification_hash: string | null;
};

/** One recorded operator decision, exactly as the API returns it. */
export type OperatorFeedback = {
  decision_id: string;
  recommendation_id: string;
  pan_id: string;
  decision: OperatorDecision;
  /**
   * The action actually performed, from the closed vocabulary.
   *
   * Null is a real value, not a missing one: it records that no physical action
   * followed the advice — an acceptance left blank. It is never filled in on the
   * operator's behalf, so a reader can tell "accepted and nothing done" apart
   * from "accepted and the action taken".
   */
  actual_action: DecisionAction | null;
  recommended_action: DecisionAction | null;
  /**
   * The advice this decision answers, joined live.
   *
   * Null when the recommendation is no longer stored. It is a document this
   * record does not own: recommendations are versioned, superseded and pruned,
   * and losing one costs the reader the extra trace identifiers above, not the
   * decision. Every field needed to read the decision — including the rule
   * version and the demonstration flag — is carried on the decision itself for
   * exactly that reason.
   */
  recommendation: OperatorDecisionRecommendation | null;
  /** The rule version that produced the advice, copied in at decision time. */
  rule_name: string | null;
  rule_version: number | null;
  specification_hash: string | null;
  /**
   * True when the advice this decision answers was built from demonstration
   * records, false when it was not.
   *
   * Copied from that advice, so a research export reads the label rather than
   * inferring it from source strings it may not have been given. An operator can
   * make a perfectly real decision about a worked example, and the record is
   * worth keeping — what must not happen is that decision sitting in an export
   * beside real ones with nothing to tell them apart.
   *
   * Null is the third case and is not the same as false: it means the record
   * does not say, either because it predates the flag or because the advice it
   * cites is gone. A null must never be rendered as "not demonstration" — that
   * would assert a provenance no surviving document supports, which is the one
   * thing this field is here to prevent.
   */
  is_demonstration: boolean | null;
  demonstration_sources: string[];
  operator_id: string | null;
  notes: string | null;
  timestamp: string;
  created_at: string;
};

export type OperatorFeedbackListResponse = {
  count: number;
  items: OperatorFeedback[];
};

/** Every code the backend refuses to record a decision with. */
export const FEEDBACK_REFUSALS = [
  "recommendation_unknown",
  "recommendation_not_a_decision",
  "decision_already_recorded",
  "unknown_decision_vocabulary",
] as const;

export type FeedbackRefusal = (typeof FEEDBACK_REFUSALS)[number];

/**
 * What the panel submits for one decision.
 *
 * `actualAction` is optional: with an acceptance it may be omitted to record
 * that no physical action followed, or supplied for an action that was actually
 * performed. It is required before a modified or rejected decision can be sent.
 */
export type FeedbackSubmission = {
  recommendationId: string;
  decision: OperatorDecision;
  actualAction?: DecisionAction;
  operatorId?: string;
  notes?: string;
};

/** Whether the decision resulted in the exact action the advice recommended. */
export function decisionFollowedRecommendation(record: OperatorFeedback): boolean {
  return (
    record.actual_action !== null &&
    record.actual_action === record.recommended_action
  );
}

/**
 * What the record says was actually done, in a sentence.
 *
 * The disposition and the action are kept apart in words as they are in the
 * record: an acceptance that names no action is told plainly — the advice was
 * accepted and nothing was done — and an action is only ever reported as taken
 * if the record contains one.
 */
export function describeRecordedAction(record: OperatorFeedback): string {
  if (record.actual_action === null) {
    if (record.decision === "accepted") {
      return "Recommendation accepted — no physical action recorded.";
    }
    return "The record names no action actually taken.";
  }
  if (record.recommended_action === record.actual_action) {
    return `Took ${record.actual_action} — the recommended action stands.`;
  }
  return `Took ${record.actual_action} instead of the recommended ${record.recommended_action}.`;
}

// -- the trace ------------------------------------------------------------

/**
 * One stage of the reconstructed event chain.
 *
 * `id` and `timestamp` are null when the stage produced no record yet, and the
 * `detail` line states why in the module's own honesty language: a missing model,
 * an unattributed estimate or an outcome nobody has filed are ordinary states,
 * each with its own sentence.
 */
export type TraceStep = {
  key: string;
  label: string;
  provenance: ProvenanceKind;
  id: string | null;
  timestamp: string | null;
  detail: string;
};

/**
 * The eight stages of the chain, in order.
 *
 * Observation -> digital twin -> simulation -> ML prediction -> SHAP explanation
 * -> recommendation -> operator decision -> actual outcome. Every step carries the
 * identity and instant of its own record; a step that has no record yet keeps its
 * place and says so. An outcome is the last word, and it is joined by the matching
 * of its recorded decision reference to this decision.
 */
export function trace(
  advice: DecisionAdvice,
  decision: OperatorFeedback | null,
  outcome: EventOutcome | null,
): TraceStep[] {
  const ml = advice.ml_result;
  const outcomeMatches =
    outcome !== null &&
    decision !== null &&
    outcome.links.operator_decision?.record_id === decision.decision_id;

  return [
    {
      key: "observation",
      label: "Pan observation",
      provenance: "observed",
      id: advice.twin.observation_id,
      timestamp: advice.twin.observation_timestamp,
      detail: advice.twin.observation_id
        ? "The reading the digital twin was built around."
        : "No pan observation is on the record for this state.",
    },
    {
      key: "twin",
      label: "Digital twin",
      provenance: "observed",
      id: advice.twin.twin_id,
      timestamp: advice.twin.as_of,
      detail: "The recorded state the whole advice was made from.",
    },
    {
      key: "simulation",
      label: "Mass balance",
      provenance: "simulated",
      id: advice.simulation_result.run_id,
      timestamp: null,
      detail: advice.simulation_result.run_id
        ? "The stored run that produced the rainfall/salinity figures."
        : "The stored recommendation carries no run identifier.",
    },
    {
      key: "ml",
      label: "ML prediction",
      provenance: "ml",
      id: ml?.prediction_id ?? null,
      timestamp: ml?.predicted_at ?? null,
      detail: ml
        ? "The stored model estimate the rule may have read."
        : "No model estimate was read for this advice.",
    },
    {
      key: "shap",
      label: "SHAP explanation",
      provenance: "ml",
      id: ml?.explanation_id ?? null,
      timestamp: null,
      detail: ml?.explanation_id
        ? "The module 11 attribution of that exact estimate."
        : ml
          ? "The model estimate was not attributed."
          : "No SHAP explanation — no model estimate was read.",
    },
    {
      key: "recommendation",
      label: "Recommendation",
      provenance: "recommended",
      id: advice.recommendation_id,
      timestamp: advice.timestamp,
      detail: "The versioned rule's answer to what should be done.",
    },
    {
      key: "decision",
      label: "Operator decision",
      provenance: "operator_decision",
      id: decision?.decision_id ?? null,
      timestamp: decision?.timestamp ?? null,
      detail: decision
        ? "The recorded disposition of this recommendation."
        : "No operator decision is recorded for this advice yet.",
    },
    {
      key: "outcome",
      label: "Actual outcome",
      provenance: "observed",
      id: outcome?.outcome_id ?? null,
      timestamp: outcome?.timestamp ?? null,
      detail: outcome
        ? outcomeMatches
          ? "The recorded event, linked to this decision by its decision reference."
          : "Recorded, but its decision reference is a different decision or none."
        : "No outcome has been recorded for this pan yet.",
    },
  ];
}