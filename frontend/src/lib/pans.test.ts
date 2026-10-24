import {
  validateFarmerName,
  validateWhatsAppNumber,
} from "@/lib/pans";
import { describe, expect, it } from "vitest";
import {
  EMPTY_PAN_FORM,
  formatArea,
  formatCoordinate,
  formatMeasurement,
  normalizePanId,
  panToFormValues,
  referenceVolumeM3,
  validateCoordinate,
  validatePanId,
  validatePositiveMeasurement,
  validateRegistration,
  validateRequiredText,
  validateRiskThreshold,
  validateRiskThresholds,
  validateUpdate,
  type SaltPan,
  type SaltPanFormValues,
} from "@/lib/pans";

function formValues(overrides: Partial<SaltPanFormValues> = {}): SaltPanFormValues {
  return {
    ...EMPTY_PAN_FORM,
    pan_id: "pans-2026-a01",
    pan_name: "Northern evaporation pan",
    location: "Port Shepstone",
    area_m2: "12500",
    normal_depth_cm: "15.5",
    target_harvest_salinity_g_l: "285",
    ...overrides,
  };
}

describe("pan area, depth and salinity must be greater than zero", () => {
  const measurements = [
    ["area_m2", "Pan area", "m²"],
    ["normal_depth_cm", "Normal water depth", "cm"],
    ["target_harvest_salinity_g_l", "Target harvest salinity", "g/L"],
  ] as const;

  for (const [field, label, unit] of measurements) {
    it(`rejects a missing ${field}`, () => {
      const { payload, errors } = validateRegistration(formValues({ [field]: "" }));
      expect(payload).toBeNull();
      expect(errors[field]).toContain(label);
      expect(errors[field]).toContain("required");
    });

    it(`rejects a zero ${field}`, () => {
      const { payload, errors } = validateRegistration(formValues({ [field]: "0" }));
      expect(payload).toBeNull();
      expect(errors[field]).toBe(`${label} must be greater than 0 ${unit}.`);
    });

    it(`rejects a negative ${field}`, () => {
      const { payload, errors } = validateRegistration(
        formValues({ [field]: "-12.5" }),
      );
      expect(payload).toBeNull();
      expect(errors[field]).toBe(`${label} must be greater than 0 ${unit}.`);
    });

    it(`accepts a small positive ${field}`, () => {
      const { payload, errors } = validateRegistration(
        formValues({ [field]: "0.01" }),
      );
      expect(errors).toEqual({});
      expect(payload).not.toBeNull();
      expect(payload?.[field]).toBeCloseTo(0.01);
    });
  }

  it("rejects non-numeric input", () => {
    const { payload, errors } = validateRegistration(
      formValues({ area_m2: "twelve thousand" }),
    );
    expect(payload).toBeNull();
    expect(errors.area_m2).toBe("Pan area must be a number.");
  });
});

describe("validatePositiveMeasurement", () => {
  it("trims surrounding whitespace before parsing", () => {
    expect(
      validatePositiveMeasurement("  42.5 ", { label: "Pan area", unit: "m²" }),
    ).toEqual({ value: 42.5, error: null });
  });

  it("rejects a value that is only a sign", () => {
    expect(
      validatePositiveMeasurement("-", { label: "Pan area", unit: "m²" }).error,
    ).toBe("Pan area must be a number.");
  });

  it("rejects scientific notation to keep the input unambiguous", () => {
    expect(
      validatePositiveMeasurement("1e3", { label: "Pan area", unit: "m²" }).error,
    ).toBe("Pan area must be a number.");
  });
});

describe("latitude and longitude are validated only when supplied", () => {
  it("accepts both being omitted", () => {
    const { payload, errors } = validateRegistration(
      formValues({ latitude: "", longitude: "" }),
    );
    expect(errors).toEqual({});
    expect(payload?.latitude).toBeNull();
    expect(payload?.longitude).toBeNull();
  });

  it("accepts a valid pair", () => {
    const { payload, errors } = validateRegistration(
      formValues({ latitude: "-30.7444", longitude: "30.8071" }),
    );
    expect(errors).toEqual({});
    expect(payload?.latitude).toBeCloseTo(-30.7444);
    expect(payload?.longitude).toBeCloseTo(30.8071);
  });

  it("rejects latitude without longitude", () => {
    const { payload, errors } = validateRegistration(
      formValues({ latitude: "-30.7444", longitude: "" }),
    );
    expect(payload).toBeNull();
    expect(errors.longitude).toContain("Longitude is required because latitude");
  });

  it("rejects longitude without latitude", () => {
    const { payload, errors } = validateRegistration(
      formValues({ latitude: "", longitude: "30.8071" }),
    );
    expect(payload).toBeNull();
    expect(errors.latitude).toContain("Latitude is required because longitude");
  });

  it.each([
    ["latitude", "90.0001", "91"],
    ["latitude", "-90.0001", "-91"],
    ["longitude", "180.0001", "181"],
    ["longitude", "-180.0001", "-181"],
  ])("rejects out-of-range %s of %s", (field, value) => {
    const values = formValues({
      latitude: "0",
      longitude: "0",
      [field]: value,
    });
    const { payload, errors } = validateRegistration(values);
    expect(payload).toBeNull();
    expect(errors[field as "latitude" | "longitude"]).toContain("must be between");
  });

  it.each([
    ["latitude", "90"],
    ["latitude", "-90"],
    ["longitude", "180"],
    ["longitude", "-180"],
  ])("accepts %s at the %s boundary", (field, value) => {
    const { payload, errors } = validateRegistration(
      formValues({ latitude: "0", longitude: "0", [field]: value }),
    );
    expect(errors).toEqual({});
    expect(payload).not.toBeNull();
  });

  it("rejects a coordinate that is not numeric", () => {
    const { errors } = validateRegistration(
      formValues({ latitude: "south", longitude: "30" }),
    );
    expect(errors.latitude).toContain("decimal degrees");
  });
});

describe("validateCoordinate", () => {
  it("treats blank as valid when the partner is also blank", () => {
    expect(
      validateCoordinate("", {
        label: "Latitude",
        min: -90,
        max: 90,
        partnerLabel: "longitude",
        partnerPresent: false,
      }),
    ).toEqual({ value: null, error: null });
  });
});

describe("pan identifier", () => {
  it("is required", () => {
    expect(validatePanId("   ")).toEqual({ value: "", error: "Pan ID is required." });
  });

  it.each(["pans-2026-a01", "A01", "pan_1", "pan.1", "ABC123"])(
    "accepts %s",
    (identifier) => {
      expect(validatePanId(identifier).error).toBeNull();
    },
  );

  it.each(["pan 1", "pan/1", "pan#1", "pän-1", "pan:1"])(
    "rejects %s",
    (identifier) => {
      expect(validatePanId(identifier).error).toContain(
        "letters, digits, dots, underscores and hyphens",
      );
    },
  );

  it("rejects an identifier longer than 64 characters", () => {
    expect(validatePanId("a".repeat(65)).error).toContain("64 characters or fewer");
  });

  it("normalises to lower case for display and routing", () => {
    expect(normalizePanId("  PANS-2026-A01 ")).toBe("pans-2026-a01");
  });
});

describe("required text fields", () => {
  it.each(["pan_name", "location"] as const)("rejects a blank %s", (field) => {
    const { payload, errors } = validateRegistration(formValues({ [field]: "  " }));
    expect(payload).toBeNull();
    expect(errors[field]).toContain("is required");
  });

  it("rejects text beyond the stored length", () => {
    expect(validateRequiredText("a".repeat(129), { label: "Pan name" }).error).toContain(
      "128 characters or fewer",
    );
  });
});

describe("validateRegistration", () => {
  it("returns a clean payload for a valid pan", () => {
    const { payload, errors } = validateRegistration(
      formValues({ latitude: "-30.7444", longitude: "30.8071" }),
    );
    expect(errors).toEqual({});
    expect(payload).toEqual({
      pan_id: "pans-2026-a01",
      pan_name: "Northern evaporation pan",
      location: "Port Shepstone",
      latitude: -30.7444,
      longitude: 30.8071,
      area_m2: 12500,
      normal_depth_cm: 15.5,
      target_harvest_salinity_g_l: 285,
      // The whole operator-limits block is submitted, unset fields as null, so the
      // absence of a limit is explicit rather than implied by omission.
      risk_thresholds: {
        flood_depth_cm: null,
        dilution_salinity_g_l: null,
        critical_rainfall_24h_mm: null,
        target_flow_cm: null,
        max_observation_age_hours: null,
      },
      // Sent explicitly as null rather than omitted: a registration that records no
      // farmer should say so, rather than leaving the field's absence to be read.
      farmer_name: null,
      farmer_whatsapp: null,
    });
  });

  it("trims text before sending it", () => {
    const { payload } = validateRegistration(
      formValues({ pan_name: "  Northern pan  ", location: "  Durban " }),
    );
    expect(payload?.pan_name).toBe("Northern pan");
    expect(payload?.location).toBe("Durban");
  });

  it("reports every problem at once rather than one at a time", () => {
    const { errors } = validateRegistration(
      formValues({ area_m2: "0", normal_depth_cm: "-1", pan_id: "bad id" }),
    );
    expect(Object.keys(errors).sort()).toEqual([
      "area_m2",
      "normal_depth_cm",
      "pan_id",
    ]);
  });
});

describe("validateUpdate", () => {
  it("never sends a pan ID unless explicitly asked to", () => {
    const { payload } = validateUpdate(formValues());
    expect(payload).not.toHaveProperty("pan_id");
  });

  it("returns every editable field so the pre-filled form round-trips", () => {
    const { payload, errors } = validateUpdate(
      formValues({ latitude: "-30.7444", longitude: "30.8071" }),
    );
    expect(errors).toEqual({});
    expect(payload).toEqual({
      pan_name: "Northern evaporation pan",
      location: "Port Shepstone",
      latitude: -30.7444,
      longitude: 30.8071,
      area_m2: 12500,
      normal_depth_cm: 15.5,
      target_harvest_salinity_g_l: 285,
      risk_thresholds: {
        flood_depth_cm: null,
        dilution_salinity_g_l: null,
        critical_rainfall_24h_mm: null,
        target_flow_cm: null,
        max_observation_age_hours: null,
      },
    });
  });

  it("clears the coordinate pair when both fields are blank", () => {
    const { payload, errors } = validateUpdate(
      formValues({ latitude: "", longitude: "" }),
    );
    expect(errors).toEqual({});
    expect(payload?.latitude).toBeNull();
    expect(payload?.longitude).toBeNull();
  });

  it("applies the same measurement rules as registration", () => {
    const { payload, errors } = validateUpdate(formValues({ normal_depth_cm: "0" }));
    expect(payload).toBeNull();
    expect(errors.normal_depth_cm).toBe("Normal water depth must be greater than 0 cm.");
  });

  it("validates the pan ID when the field is editable", () => {
    const { errors } = validateUpdate(formValues({ pan_id: "not valid" }), {
      includePanId: true,
    });
    expect(errors.pan_id).toBeTruthy();
  });
});

describe("divergent rules and the operator's risk thresholds", () => {
  // A decision rule reads these values from the pan's own configuration and is
  // refused, not defaulted, when one is missing. These tests pin the frontend half:
  // the fields exist, blank is "not configured" rather than zero, and the whole
  // block reaches the payload so the operator can set (and clear) each limit.

  it("treats every blank threshold as not configured, not as zero", () => {
    expect(validateRiskThreshold("", { label: "Flood depth", unit: "cm", allowZero: true })).toEqual(
      { value: null, error: null },
    );
  });

  it("verifies the exact three limits the harvest/flood rule refuses without", () => {
    // The rule's criteria name these three bounds plus the harvest target, so a pan
    // that is complete but unconfigured reaches `insufficient_evidence` naming them.
    const { payload, errors } = validateRegistration(
      formValues({
        flood_depth_cm: "25",
        dilution_salinity_g_l: "180",
        target_flow_cm: "12",
      }),
    );
    expect(errors).toEqual({});
    expect(payload?.risk_thresholds).toEqual({
      flood_depth_cm: 25,
      dilution_salinity_g_l: 180,
      critical_rainfall_24h_mm: null,
      target_flow_cm: 12,
      max_observation_age_hours: null,
    });
  });

  it("accepts zero for the depth, salinity and rainfall limits", () => {
    for (const field of [
      "flood_depth_cm",
      "dilution_salinity_g_l",
      "critical_rainfall_24h_mm",
    ] as const) {
      const { payload, errors } = validateRegistration(formValues({ [field]: "0" }));
      expect(errors[field]).toBeUndefined();
      expect(payload?.risk_thresholds?.[field]).toBe(0);
    }
  });

  it("refuses zero or negative transfer depth and reading age", () => {
    const zeroFlow = validateRegistration(formValues({ target_flow_cm: "0" }));
    expect(zeroFlow.payload).toBeNull();
    expect(zeroFlow.errors.target_flow_cm).toBe(
      "Target transfer depth must be greater than 0 cm.",
    );

    const negativeAge = validateRegistration(
      formValues({ max_observation_age_hours: "-1" }),
    );
    expect(negativeAge.payload).toBeNull();
    expect(negativeAge.errors.max_observation_age_hours).toBe(
      "Maximum reading age must be greater than 0 h.",
    );
  });

  it("refuses a negative depth, salinity or rainfall limit", () => {
    for (const [field, label] of [
      ["flood_depth_cm", "Flood depth"],
      ["dilution_salinity_g_l", "Dilution salinity"],
      ["critical_rainfall_24h_mm", "Critical rainfall (24 h)"],
    ] as const) {
      const { payload, errors } = validateRegistration(formValues({ [field]: "-5" }));
      expect(payload).toBeNull();
      expect(errors[field]).toBe(`${label} must be 0 or greater.`);
    }
  });

  it("reports a non-numeric threshold rather than coercing it", () => {
    const { payload, errors } = validateRegistration(
      formValues({ dilution_salinity_g_l: "salty" }),
    );
    expect(payload).toBeNull();
    expect(errors.dilution_salinity_g_l).toBe("Dilution salinity must be a number.");
  });

  it("returns all five keys so a limit can be cleared on update", () => {
    const { value, errors } = validateRiskThresholds(
      formValues({ flood_depth_cm: "25" }),
    );
    expect(errors).toEqual({});
    // Every key is present, and the configured one is a number while the rest are
    // null. That is what lets the backend replace the block whole and express
    // "I removed my flood depth".
    expect(value).toEqual({
      flood_depth_cm: 25,
      dilution_salinity_g_l: null,
      critical_rainfall_24h_mm: null,
      target_flow_cm: null,
      max_observation_age_hours: null,
    });
  });

  it("sends the threshold block on update so the pre-filled form round-trips", () => {
    const { payload, errors } = validateUpdate(
      formValues({ flood_depth_cm: "25", target_flow_cm: "12" }),
    );
    expect(errors).toEqual({});
    expect(payload?.risk_thresholds).toEqual({
      flood_depth_cm: 25,
      dilution_salinity_g_l: null,
      critical_rainfall_24h_mm: null,
      target_flow_cm: 12,
      max_observation_age_hours: null,
    });
  });

  it("rejects an update that carries an invalid threshold", () => {
    const { payload, errors } = validateUpdate(formValues({ target_flow_cm: "0" }));
    expect(payload).toBeNull();
    expect(errors.target_flow_cm).toBe(
      "Target transfer depth must be greater than 0 cm.",
    );
  });
});

describe("panToFormValues", () => {
  const pan: SaltPan = {
    id: "65f0000000000000000000a1",
    pan_id: "pans-2026-a01",
    pan_name: "Northern pan",
    location: "Port Shepstone",
    latitude: -30.7444,
    longitude: 30.8071,
    area_m2: 12500,
    normal_depth_cm: 15.5,
    target_harvest_salinity_g_l: 285,
    is_active: true,
    created_at: "2026-01-05T08:00:00Z",
    updated_at: "2026-01-05T08:00:00Z",
    deactivated_at: null,
  farmer_name: null,
  farmer_whatsapp_masked: null,
  latest_observation: null,
  };

  it("round-trips a geolocated pan through validation unchanged", () => {
    const values = panToFormValues(pan);
    const { payload, errors } = validateUpdate(values);
    expect(errors).toEqual({});
    expect(payload?.area_m2).toBe(12500);
    expect(payload?.latitude).toBeCloseTo(-30.7444);
  });

  it("renders absent coordinates as empty strings", () => {
    const values = panToFormValues({ ...pan, latitude: null, longitude: null });
    expect(values.latitude).toBe("");
    expect(values.longitude).toBe("");
    expect(validateUpdate(values).errors).toEqual({});
  });

  it("seeds the operator limits, showing an unconfigured one as blank", () => {
    const values = panToFormValues({
      ...pan,
      risk_thresholds: {
        flood_depth_cm: 25,
        dilution_salinity_g_l: null,
        critical_rainfall_24h_mm: 50,
        target_flow_cm: 12,
        max_observation_age_hours: null,
      },
    });
    expect(values.flood_depth_cm).toBe("25");
    expect(values.critical_rainfall_24h_mm).toBe("50");
    expect(values.target_flow_cm).toBe("12");
    // Nothing configured is a blank box, so the operator can see what is set and
    // what a rule would still be refused for.
    expect(values.dilution_salinity_g_l).toBe("");
    expect(values.max_observation_age_hours).toBe("");
    expect(validateUpdate(values).errors).toEqual({});
  });

  it("tolerates a pan returned without a threshold block", () => {
    // Older responses, or a fixture that predates the block, must read as nothing
    // configured rather than crashing the edit form.
    const values = panToFormValues({ ...pan, risk_thresholds: undefined });
    expect(values.flood_depth_cm).toBe("");
    expect(values.target_flow_cm).toBe("");
    expect(validateUpdate(values).errors).toEqual({});
  });
});

describe("display formatting", () => {
  it("shows an em dash for a missing measurement", () => {
    expect(formatMeasurement(null, "cm")).toBe("—");
  });

  it("appends the unit to a present measurement", () => {
    expect(formatMeasurement(15.5, "cm")).toBe("15.5 cm");
  });

  it("switches large areas to hectares", () => {
    expect(formatArea(12_500)).toBe("1.25 ha");
    expect(formatArea(4_600)).toBe("4,600 m²");
  });

  it("labels coordinates with a hemisphere", () => {
    expect(formatCoordinate(-30.7444, "lat")).toBe("30.7444° S");
    expect(formatCoordinate(30.8071, "lon")).toBe("30.8071° E");
    expect(formatCoordinate(null, "lat")).toBe("—");
  });

  it("derives reference volume from area and depth", () => {
    expect(referenceVolumeM3({
      id: null,
      pan_id: "p",
      pan_name: "p",
      location: "p",
      latitude: null,
      longitude: null,
      area_m2: 1000,
      normal_depth_cm: 10,
      target_harvest_salinity_g_l: 200,
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      deactivated_at: null,
  farmer_name: null,
  farmer_whatsapp_masked: null,
  latest_observation: null,
    })).toBeCloseTo(100);
  });
});

describe("farmer contact", () => {
  it("registers a pan with a farmer and a number", () => {
    const { payload, errors } = validateRegistration(
      formValues({
        latitude: "-30.7444",
        longitude: "30.8071",
        farmer_name: "  Ravi Patel  ",
        farmer_whatsapp: "+919876543210",
      }),
    );

    expect(errors).toEqual({});
    // The name is trimmed and the number submitted in its normalised form, so the
    // value stored is the value the send will dialled.
    expect(payload).toMatchObject({
      farmer_name: "Ravi Patel",
      farmer_whatsapp: "+919876543210",
    });
  });

  it("treats a pan with no farmer as a complete registration", () => {
    const { payload, errors } = validateRegistration(
      formValues({ latitude: "-30.7444", longitude: "30.8071" }),
    );

    expect(errors).toEqual({});
    expect(payload).not.toBeNull();
  });

  it.each([
    ["09876543210", /Start with \+/],
    ["+9876", /8 to 15 digits/],
    ["+1234567890123456", /8 to 15 digits/],
    ["+91 98765 abc", /digits only/],
  ])("rejects %s", (number, expected) => {
    const { payload, errors } = validateRegistration(
      formValues({ farmer_whatsapp: number }),
    );

    expect(payload).toBeNull();
    expect(errors.farmer_whatsapp).toMatch(expected);
  });

  it.each([
    ["+919876543210", "+919876543210"],
    ["+91 98765-43210", "+919876543210"],
    ["+91 (987) 654.3210", "+919876543210"],
  ])("normalises %s", (typed, expected) => {
    // Separators are how numbers are read aloud and written down. Refusing them would
    // train an operator to work around the form rather than fix the number.
    expect(validateWhatsAppNumber(typed)).toEqual({ value: expected, error: null });
  });

  it("keeps the name optional but bounded", () => {
    expect(validateFarmerName("  ")).toEqual({ value: null, error: null });
    expect(validateFarmerName("a".repeat(121)).error).toContain("120 characters or fewer");
  });

  it("sends nothing on update when the number field is left blank", () => {
    // The read API never returns the number, so the field is always blank on the edit
    // form. Omitting the key is the only way to say "leave it as it is" — sending
    // null would clear a contact the operator never saw.
    const { payload } = validateUpdate(formValues(), { panHasFarmerName: true });

    expect(payload).not.toHaveProperty("farmer_whatsapp");
  });

  it("replaces the number on update when one is typed", () => {
    const { payload, errors } = validateUpdate(
      formValues({ farmer_whatsapp: "+91 98765-43210" }),
      { panHasFarmerName: true },
    );

    expect(errors).toEqual({});
    expect(payload).toMatchObject({ farmer_whatsapp: "+919876543210" });
  });

  it("clears the name on update only when one was recorded", () => {
    // Clearing a name has to be an explicit act; an untouched blank field must not
    // wipe a name the operator cannot see on a pan they opened to change something else.
    expect(validateUpdate(formValues(), { panHasFarmerName: true }).payload).toMatchObject({
      farmer_name: null,
    });
    expect(validateUpdate(formValues(), { panHasFarmerName: false }).payload).not.toHaveProperty(
      "farmer_name",
    );
  });

  it("round-trips the name through the form but never the number", () => {
    const stored: SaltPan = {
      id: "65f0000000000000000000a1",
      pan_id: "pans-2026-a01",
      pan_name: "Northern pan",
      location: "Port Shepstone",
      latitude: -30.7444,
      longitude: 30.8071,
      area_m2: 12500,
      normal_depth_cm: 15.5,
      target_harvest_salinity_g_l: 285,
      is_active: true,
      created_at: "2026-03-01T00:00:00Z",
      updated_at: "2026-03-01T00:00:00Z",
      deactivated_at: null,
      latest_observation: null,
      farmer_name: "Ravi Patel",
      farmer_whatsapp_masked: "+919•••••••10",
    };

    const values = panToFormValues(stored);

    expect(values.farmer_name).toBe("Ravi Patel");
    // Only the mask ever left the server, so the form cannot show the number even if
    // it wanted to.
    expect(values.farmer_whatsapp).toBe("");
  });
});
