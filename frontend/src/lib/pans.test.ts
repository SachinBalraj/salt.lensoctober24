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
  latest_observation: null,
    })).toBeCloseTo(100);
  });
});
