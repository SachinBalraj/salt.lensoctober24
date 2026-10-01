/**
 * The demonstration source of truth.
 *
 * These constants are the single place the hypothetical values live, and several
 * things elsewhere depend on them being exactly these numbers: the fixed
 * scenario, the read-only fields in the two forms, and the inputs the simulation
 * card sends. A test that let the salinity drift to 240 would not fail here or
 * anywhere, and the demonstration would quietly become a different experiment
 * while still describing itself as the same one.
 */

import { describe, expect, it } from "vitest";
import {
  BRINE_TEMPERATURE_NOTE,
  DEMONSTRATION_EXPLANATION,
  DEMONSTRATION_LABEL,
  DEMONSTRATION_NOTE_PREFIX,
  DEMONSTRATION_NOTICE,
  DEMONSTRATION_OBSERVATION,
  DEMONSTRATION_RAINFALL_SCENARIOS_MM,
  DEMONSTRATION_SCENARIO,
  DEMONSTRATION_SIMULATION_INPUTS,
  DEMONSTRATION_SOURCE,
  isDemonstrationNote,
  isDemonstrationSource,
  markDemonstrationNote,
} from "@/lib/demonstration";

describe("the demonstration source", () => {
  it("is stored as its own provenance value, not as a real one", () => {
    // The whole isolation guarantee rests on this string: the backend filters
    // research datasets on it. If it collided with `manual` or `sensor` the
    // demonstration would be ingested as evidence.
    expect(DEMONSTRATION_SOURCE).toBe("demonstration");
  });

  it("recognises itself and nothing else", () => {
    expect(isDemonstrationSource("demonstration")).toBe(true);
    expect(isDemonstrationSource("manual")).toBe(false);
    expect(isDemonstrationSource("sensor")).toBe(false);
    expect(isDemonstrationSource(null)).toBe(false);
    expect(isDemonstrationSource(undefined)).toBe(false);
  });
});

describe("the note marker", () => {
  it("is added to an empty note", () => {
    expect(markDemonstrationNote(null)).toBe(DEMONSTRATION_NOTE_PREFIX);
    expect(markDemonstrationNote(undefined)).toBe(DEMONSTRATION_NOTE_PREFIX);
    expect(markDemonstrationNote("")).toBe(DEMONSTRATION_NOTE_PREFIX);
  });

  it("prepends to a note the operator wrote, keeping their words", () => {
    const marked = markDemonstrationNote("Handed over to the new operator.");
    expect(marked).toBe(
      `${DEMONSTRATION_NOTE_PREFIX} Handed over to the new operator.`,
    );
  });

  it("is not added twice", () => {
    // Saving a form re-validates on every render, so an idempotent marker is the
    // difference between one prefix and a stack of them growing in the note.
    const once = markDemonstrationNote("Original.");
    expect(markDemonstrationNote(once)).toBe(once);
    expect(markDemonstrationNote(markDemonstrationNote(once))).toBe(once);
  });

  it("survives a round trip", () => {
    const note = markDemonstrationNote("Shown to the board.");
    expect(isDemonstrationNote(note)).toBe(true);
    expect(isDemonstrationNote("Shown to the board.")).toBe(false);
    expect(isDemonstrationNote(null)).toBe(false);
  });
});

describe("the fixed scenario", () => {
  it("is the one that was specified, to the digit", () => {
    expect(DEMONSTRATION_SIMULATION_INPUTS.salinity_g_l).toBe(250);
    expect(DEMONSTRATION_SIMULATION_INPUTS.initial_depth_m).toBe(0.2);
  });

  it("covers the five requested rainfall depths and no others", () => {
    expect([...DEMONSTRATION_RAINFALL_SCENARIOS_MM]).toEqual([0, 5, 10, 20, 50]);
  });

  it("groups the scenario so it cannot be assembled from mixed parts", () => {
    // The object exists to stop a caller taking a salinity from here and a depth
    // from elsewhere, which is how a demonstration and a measurement end up
    // sharing a screen.
    expect(DEMONSTRATION_SCENARIO.observation).toBe(DEMONSTRATION_OBSERVATION);
    expect(DEMONSTRATION_SCENARIO.simulation).toBe(
      DEMONSTRATION_SIMULATION_INPUTS,
    );
    expect(DEMONSTRATION_SCENARIO.rainfall_scenarios_mm).toBe(
      DEMONSTRATION_RAINFALL_SCENARIOS_MM,
    );
  });

  it("gives the observation the salinity and depth the balance assumes", () => {
    // The read-only fields in the form and the inputs to the sweep have to be the
    // same numbers, or the dashboard would show a demonstration reading beside a
    // curve computed from a different one. The depth is held in centimetres and
    // converted once, so that the single unit conversion in the demonstration is
    // visible rather than scattered across call sites.
    expect(DEMONSTRATION_SIMULATION_INPUTS.salinity_g_l).toBe(
      DEMONSTRATION_OBSERVATION.salinity_g_l,
    );
    expect(DEMONSTRATION_OBSERVATION.water_depth_cm).toBe(20);
    expect(DEMONSTRATION_SIMULATION_INPUTS.initial_depth_m).toBe(0.2);
  });
});

describe("what the operator is told", () => {
  it("labels a demonstration as a demonstration, in every surface", () => {
    expect(DEMONSTRATION_LABEL).toBe("Demonstration");
    // A badge reading "Manual" or "Measured" beside fabricated values would be
    // the exact failure these strings exist to prevent.
    expect(DEMONSTRATION_LABEL).not.toMatch(/manual|measured|observed|forecast/i);
  });

  it("denies it is a measurement in every one of the three wordings", () => {
    for (const text of [
      DEMONSTRATION_NOTICE,
      DEMONSTRATION_EXPLANATION,
      BRINE_TEMPERATURE_NOTE,
    ]) {
      expect(text.length).toBeGreaterThan(0);
      expect(text).toMatch(/not|no |never/i);
    }
  });

  it("explains that the temperature is recorded context, not a balance input", () => {
    // The balance does not use brine temperature, so a card that listed it as an
    // input would be claiming the model reads something it does not.
    expect(BRINE_TEMPERATURE_NOTE).toContain("32");
    expect(BRINE_TEMPERATURE_NOTE.toLowerCase()).toMatch(
      /record|context|not .*(use|input)|does not/,
    );
  });
});
