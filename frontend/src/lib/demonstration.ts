/**
 * What "demonstration" means, in one place.
 *
 * A demonstration exists because a project with no measurements has nothing to
 * show. The temptation is to fill the gap with plausible numbers and let them
 * pass for readings. This module is the answer to that, and it is deliberately
 * the only place the vocabulary lives, because the alternative is four screens
 * each inventing its own wording for the same state and an operator who cannot
 * tell whether the number in front of them is evidence.
 *
 * So three things are fixed here, and the rest of the application refers to them
 * rather than repeating them.
 *
 * **The values are constants, not defaults.** `DEMONSTRATION_SCENARIO` is what
 * the demonstration is; it is not a starting point an operator is expected to
 * adjust, because a demonstration that can be edited is a measurement form with
 * a different label. It is read-only, and the UI says so.
 *
 * **The provenance is carried in the data, not the styling.** A demonstration
 * record is filed under `data_source: "demonstration"` and `source:
 * "demonstration"`, which the backend excludes from research dataset builds. A
 * demonstration is therefore not merely labelled on screen; it is a different
 * kind of object to everything downstream, and no amount of re-labelling in the
 * interface can turn it back into evidence.
 *
 * **Brine temperature is context, not an input.** The mass balance conserves
 * salt in water and has no temperature term, so the demonstration records
 * 32 °C to show a complete observation while `DEMONSTRATION_SCENARIO.simulation`
 * says plainly that the balance did not use it. Quoting it as though it did
 * would be a claim about the model that the model does not make.
 */

/** The provenance both an observation and a weather record are filed under. */
export const DEMONSTRATION_SOURCE = "demonstration" as const;

/**
 * Prefix stamped into a demonstration record's note.
 *
 * The backend already refuses to build research rows from a demonstration, so
 * this is not what keeps the record out of the dataset. It is there so a person
 * reading the record outside SaltLens — in an export someone opened by hand, in
 * a support ticket, in a screenshot — can still tell what it is without a
 * second lookup. The marker is idempotent: re-saving a demonstration does not
 * accumulate prefixes.
 */
export const DEMONSTRATION_NOTE_PREFIX = "[DEMONSTRATION — not a measurement]";

/** The banner shown wherever a demonstration value is entered or displayed. */
export const DEMONSTRATION_NOTICE =
  "Demonstration value — this is not a measurement of this pan and is excluded from research datasets.";

/** Short label for a provenance badge. */
export const DEMONSTRATION_LABEL = "Demonstration";

/** One-line explanation shown under a demonstration badge. */
export const DEMONSTRATION_EXPLANATION =
  "Typed in to show the workflow. It is stored and visible, but no research dataset is built from it.";

/** The four quantities a demonstration observation records. */
export const DEMONSTRATION_OBSERVATION = {
  salinity_g_l: 250,
  water_depth_cm: 20,
  brine_temperature_c: 32,
  ambient_temperature_c: 27,
  humidity_percent: 60,
} as const;

/**
 * The mass balance's starting brine, which is the same pan as above.
 *
 * `initial_depth_m` is the centimetre figure divided by 100, and it is spelled
 * out in metres here rather than converted at the call site so that the one unit
 * conversion in the demonstration is visible in the constant that states it.
 */
export const DEMONSTRATION_SIMULATION_INPUTS = {
  salinity_g_l: DEMONSTRATION_OBSERVATION.salinity_g_l,
  /** 20 cm, as metres. */
  initial_depth_m: DEMONSTRATION_OBSERVATION.water_depth_cm / 100,
} as const;

/**
 * The rainfall depths the demonstration sweeps, in millimetres.
 *
 * A subset of the server's presets. The server always resolves its own six
 * because "no rain" is the reference the others are read against; the
 * demonstration shows the five it was asked for rather than implying a sixth
 * was never computed.
 */
export const DEMONSTRATION_RAINFALL_SCENARIOS_MM = [0, 5, 10, 20, 50] as const;

/**
 * What the demonstration is, in one object.
 *
 * Grouped so a caller cannot assemble a demonstration out of the salinity from
 * here and the depth from somewhere else, which is how a "demonstration" and a
 * "measurement" end up sharing a screen.
 */
export const DEMONSTRATION_SCENARIO = {
  observation: DEMONSTRATION_OBSERVATION,
  simulation: DEMONSTRATION_SIMULATION_INPUTS,
  rainfall_scenarios_mm: DEMONSTRATION_RAINFALL_SCENARIOS_MM,
} as const;

/**
 * Why the balance ignores the brine temperature.
 *
 * Stated on the screen next to the number. It is the honest reading: the
 * observation is complete, the calculation is not temperature-dependent, and a
 * reader who is not told will assume the 32 °C was an input.
 */
export const BRINE_TEMPERATURE_NOTE =
  "Brine temperature is shown as part of the observation. The mass balance conserves salt in water and has no temperature term, so 32 °C is recorded and displayed but does not enter the calculation.";

/** Adds the demonstration marker to a note, at most once. */
export function markDemonstrationNote(note: string | null | undefined): string {
  const trimmed = (note ?? "").trim();
  if (trimmed.startsWith(DEMONSTRATION_NOTE_PREFIX)) return trimmed;
  return trimmed ? `${DEMONSTRATION_NOTE_PREFIX} ${trimmed}` : DEMONSTRATION_NOTE_PREFIX;
}

/** True when a note already carries the demonstration marker. */
export function isDemonstrationNote(note: string | null | undefined): boolean {
  return (note ?? "").trim().startsWith(DEMONSTRATION_NOTE_PREFIX);
}

/** True when a record's provenance says it is a demonstration. */
export function isDemonstrationSource(
  source: string | null | undefined,
): boolean {
  return source === DEMONSTRATION_SOURCE;
}
