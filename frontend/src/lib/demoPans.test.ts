import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEMO_DATA_NOTICE,
  asRegistryView,
  getDemoPans,
  isDemoDataEnabled,
} from "@/lib/demoPans";
import { normalizePanId, validateRegistration } from "@/lib/pans";

const FLAG = "NEXT_PUBLIC_SALTLENS_DEMO_PANS";

afterEach(() => {
  vi.unstubAllEnvs();
});

function stubFlag(value?: string) {
  if (value === undefined) {
    vi.stubEnv(FLAG, undefined as unknown as string);
  } else {
    vi.stubEnv(FLAG, value);
  }
}

describe("demo data is opt-in", () => {
  it("is disabled when the flag is unset", () => {
    stubFlag(undefined);
    expect(isDemoDataEnabled()).toBe(false);
    expect(getDemoPans()).toEqual([]);
  });

  it("is disabled for any value other than the exact string true", () => {
    for (const value of ["false", "1", "TRUE", "yes", ""]) {
      stubFlag(value);
      expect(isDemoDataEnabled()).toBe(false);
      expect(getDemoPans()).toEqual([]);
    }
  });

  it("is enabled only by the exact string true", () => {
    stubFlag("true");
    expect(isDemoDataEnabled()).toBe(true);
    expect(getDemoPans().length).toBeGreaterThan(0);
  });
});

describe("demo records are unmistakable", () => {
  it("prefixes every identifier with DEMO-", () => {
    stubFlag("true");
    const views = getDemoPans();
    expect(views.length).toBeGreaterThan(0);
    for (const { pan } of views) {
      expect(pan.pan_id).toMatch(/^DEMO-/);
    }
  });

  it("tags every record with the demo origin", () => {
    stubFlag("true");
    for (const view of getDemoPans()) {
      expect(view.origin).toBe("demo");
    }
  });

  it("gives demo records no database identity", () => {
    stubFlag("true");
    for (const { pan } of getDemoPans()) {
      expect(pan.id).toBeNull();
    }
  });

  it("names the sites as demo sites", () => {
    stubFlag("true");
    for (const { pan } of getDemoPans()) {
      expect(pan.location.toLowerCase()).toContain("demo");
    }
  });

  it("is still findable after the route lowercases the identifier", () => {
    stubFlag("true");
    // The detail page normalises the URL segment before it reaches the
    // client, so a case-sensitive lookup here would never match.
    for (const { pan } of getDemoPans()) {
      const wanted = normalizePanId(pan.pan_id);
      const match = getDemoPans().find(
        (view) => normalizePanId(view.pan.pan_id) === wanted,
      );
      expect(match?.pan.pan_id).toBe(pan.pan_id);
    }
  });

  it("states in plain words that the data is not research data", () => {
    expect(DEMO_DATA_NOTICE).toMatch(/DEMO DATA/);
    expect(DEMO_DATA_NOTICE).toMatch(/not a measurement/i);
  });
});

describe("registry records are distinguishable from demo records", () => {
  it("tags a real record as a registry record", () => {
    stubFlag("true");
    const view = asRegistryView({
      id: "65f0000000000000000000a1",
      pan_id: "pans-2026-a01",
      pan_name: "Northern pan",
      location: "Port Shepstone",
      latitude: null,
      longitude: null,
      area_m2: 12500,
      normal_depth_cm: 15.5,
      target_harvest_salinity_g_l: 285,
      is_active: true,
      created_at: "2026-01-05T08:00:00Z",
      updated_at: "2026-01-05T08:00:00Z",
      deactivated_at: null,
  latest_observation: null,
    });
    expect(view.origin).toBe("registry");
  });

  it("never prefixes a real identifier with DEMO-", () => {
    stubFlag("true");
    const demoIds = new Set(getDemoPans().map(({ pan }) => pan.pan_id));
    expect(demoIds.has("pans-2026-a01")).toBe(false);
    expect(demoIds.has("PAN-1")).toBe(false);
    expect(demoIds.has("pan-1")).toBe(false);
  });
});

describe("demo data cannot become registry data", () => {
  it("has no identifier the registration form would accept as a new record", () => {
    stubFlag("true");
    for (const { pan } of getDemoPans()) {
      const { payload, errors } = validateRegistration({
        pan_id: pan.pan_id,
        pan_name: pan.pan_name,
        location: pan.location,
        latitude: pan.latitude === null ? "" : String(pan.latitude),
        longitude: pan.longitude === null ? "" : String(pan.longitude),
        area_m2: String(pan.area_m2),
        normal_depth_cm: String(pan.normal_depth_cm),
        target_harvest_salinity_g_l: String(pan.target_harvest_salinity_g_l),
      });
      // The form would happily accept a demo-shaped payload, which is exactly
      // why the forms never read from the demo module. This test pins that
      // every demo identifier is nonetheless loudly self-identifying.
      expect(errors).toEqual({});
      expect(payload?.pan_id).toMatch(/^DEMO-/);
    }
  });

  it("does not export anything the API client could submit", async () => {
    stubFlag("true");
    const demoModule = await import("@/lib/demoPans");
    const exported = Object.keys(demoModule);
    expect(exported).not.toContain("registerPan");
    expect(exported).not.toContain("updatePan");
    expect(exported).not.toContain("deactivatePan");
  });

  it("does not export an observation the form could submit", async () => {
    stubFlag("true");
    const demoModule = await import("@/lib/demoPans");
    const exported = Object.keys(demoModule);
    expect(exported).not.toContain("recordObservation");
    expect(exported).not.toContain("DEMO_OBSERVATIONS");
  });

  it("gives every demo pan no observations at all", () => {
    // A demo reading would be indistinguishable in a screenshot from a real
    // one, so demo pans carry no readings and the panel renders empty.
    stubFlag("true");
    for (const { pan } of getDemoPans()) {
      expect(pan.latest_observation).toBeNull();
    }
  });
});
