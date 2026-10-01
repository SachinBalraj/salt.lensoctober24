/**
 * Why a recommendation could not be issued, in the operator's words.
 *
 * Each refusal code describes a state of the system rather than a mistake in the
 * request: nothing was asked wrongly, and there is nothing to retry. Each
 * therefore gets one fixed sentence, so two surfaces describing the same state say
 * the same thing, and none of them offers a retry button that cannot help.
 *
 * ``insufficient_evidence`` is kept separate from ``rule_undecided`` rather than
 * folded into it. Both withhold an action, but the remedy is different and the
 * distinction is the useful one for an operator: a rule that read everything and
 * matched nothing is a question about the rule, while an input that could not be
 * read — an unconfigured limit, or a reading too old to decide on — is a question
 * about the pan's configuration or records. Merging them would tell an operator to
 * widen a rule when the actual problem is a limit they never set. The backend
 * raises this for either cause and names the input in the detail, so the remedy
 * below has to cover both without guessing which applies.
 *
 * Kept in its own file because it is the one part of the client that a *rule
 * author* rather than an operator needs to read: each message names the thing that
 * has to exist before advice can be given, which is the same list of things the
 * engine refuses on.
 */

/** Every code the engine refuses with. Nothing else should reach the panel. */
export const ADVICE_REFUSALS = [
  "no_twin_state",
  "simulation_inputs_missing",
  "no_rule_defined",
  "rule_undecided",
  "insufficient_evidence",
] as const;

export type AdviceRefusal = (typeof ADVICE_REFUSALS)[number];

/** One fixed sentence per refusal, and what would have to exist to get past it. */
export const ADVICE_REFUSAL_MESSAGES: Record<AdviceRefusal, string> = {
  no_twin_state:
    "No recommendation — this pan has no recorded state to reason from.",
  simulation_inputs_missing:
    "No recommendation — this pan is missing the readings the physics needs.",
  no_rule_defined:
    "No recommendation — no decision rule has been written yet.",
  rule_undecided:
    "No recommendation — the rule that applies does not decide an action for this pan.",
  insufficient_evidence:
    "Insufficient evidence — something this rule needs is not there. Nothing was assumed in its place.",
};

/**
 * What has to be true before advice can be given, per refusal.
 *
 * Shown under the message so an operator reading it learns which record is
 * missing rather than only that something is. These are the same preconditions the
 * engine enforces, named in the order they are checked.
 */
export const ADVICE_REFUSAL_REMEDIES: Record<AdviceRefusal, string> = {
  no_twin_state:
    "Record an observation and weather for the pan, then assemble its twin state.",
  simulation_inputs_missing:
    "The mass balance needs the pan's area, a water depth and a salinity, plus a rainfall depth from the forecast or an explicit what-if.",
  no_rule_defined:
    "Write a rule through POST /api/decisions/rules. The engine holds no thresholds and no default action of its own.",
  rule_undecided:
    "The rule matched nothing and declared no fallback for this pan, so there is no action to recommend. Widen the rule or add a fallback it can support.",
  insufficient_evidence:
    "The refusal text names which input was missing. If it names a reading, record a fresh one, or raise max_observation_age_hours if the pan is genuinely monitored that rarely. If it names a limit, set it on the pan's risk thresholds.",
};
