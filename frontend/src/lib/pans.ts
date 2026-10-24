/**
 * Salt-pan domain types, operator-facing validation and display formatting.
 *
 * This module is intentionally free of React and of `fetch` so the validation
 * rules the operator relies on can be unit-tested directly and reused by both
 * the create form and the edit form.
 */

export type PanStatusFilter = "active" | "inactive" | "all";

/**
 * A pan's most recent reading, denormalised onto the pan document.
 *
 * It is a copy of one observation, not an average and not an estimate. It is
 * absent until the first reading is recorded.
 */
export type LatestObservation = {
  observation_id: string;
  timestamp: string;
  salinity_g_l: number | null;
  water_depth_cm: number | null;
  brine_temperature_c: number | null;
  humidity_percent: number | null;
  ambient_temperature_c: number | null;
  data_source: string;
};

/**
 * The operator's own limits for one pan, as stored on the pan document.
 *
 * **Every field is nullable, and null means "not configured" — it does not mean
 * zero.** These are the numbers a decision rule compares against, and this project
 * supplies none of them: a flood depth, a dilution floor, a critical rainfall and a
 * transfer depth are judgements about a particular pan that only the operator can
 * make. A rule that needs a limit nobody configured is refused rather than served a
 * code default. See the backend `RiskThresholds` model and the reference rule's
 * `bound_variable` criteria.
 */
export type RiskThresholds = {
  /** Depth above which the pan should be drained, in cm. */
  flood_depth_cm: number | null;
  /** Salinity below which the brine is too dilute to sell, in g/L. */
  dilution_salinity_g_l: number | null;
  /** Rainfall over 24 h above which the pan is at risk, in mm. */
  critical_rainfall_24h_mm: number | null;
  /** Depth the operator keeps while transferring brine, in cm. */
  target_flow_cm: number | null;
  /** Oldest reading that may still support a recommendation, in hours. */
  max_observation_age_hours: number | null;
};

/** Every threshold unset. Not a set of defaults: an empty configuration. */
export const EMPTY_RISK_THRESHOLDS: RiskThresholds = {
  flood_depth_cm: null,
  dilution_salinity_g_l: null,
  critical_rainfall_24h_mm: null,
  target_flow_cm: null,
  max_observation_age_hours: null,
};

/** A salt pan exactly as the backend returns it. */
export type SaltPan = {
  id: string | null;
  pan_id: string;
  pan_name: string;
  location: string;
  latitude: number | null;
  longitude: number | null;
  area_m2: number;
  normal_depth_cm: number;
  target_harvest_salinity_g_l: number;
  /**
   * The pan's operator-configured limits. The backend always returns this object
   * (all-null when nothing is set); a response that omits it is read as nothing
   * configured rather than as a reason to fail, which is why it is optional here.
   */
  risk_thresholds?: RiskThresholds;
  /**
   * The farmer contact, as the read API returns it: a name and a *masked* number.
   *
   * The unmasked number is deliberately absent from every read. The send endpoint
   * does not need it from the browser — it reads the pan itself — so there is no
   * case where a client has a reason to hold it.
   */
  farmer_name: string | null;
  farmer_whatsapp_masked: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  deactivated_at: string | null;
  latest_observation: LatestObservation | null;
};

export type SaltPanListResponse = {
  items: SaltPan[];
  total: number;
  limit: number;
  offset: number;
  total_active: number;
  total_inactive: number;
};

/** Payload accepted by `POST /api/pans/`. */
export type SaltPanRegistration = {
  pan_id: string;
  pan_name: string;
  location: string;
  latitude: number | null;
  longitude: number | null;
  area_m2: number;
  normal_depth_cm: number;
  target_harvest_salinity_g_l: number;
  /**
   * The operator's own limits for this pan. Omitted fields are limits nobody has
   * configured; the backend stores them as null. Sent as a whole block so the
   * operator's configuration is explicit rather than assumed.
   */
  risk_thresholds?: RiskThresholds;
  /** Optional. Omit to register a pan nobody can be messaged about yet. */
  farmer_name?: string | null;
  farmer_whatsapp?: string | null;
};

/**
 * Payload accepted by `PATCH /api/pans/{pan_id}`.
 *
 * `pan_id` is absent by design: the identifier is referenced by observations,
 * weather, twin states and outcomes, so it is immutable after registration.
 */
export type SaltPanUpdate = Partial<
  Omit<SaltPanRegistration, "pan_id">
>;

/** Raw operator input, before numeric coercion. */
export type SaltPanFormValues = {
  pan_id: string;
  pan_name: string;
  location: string;
  latitude: string;
  longitude: string;
  area_m2: string;
  normal_depth_cm: string;
  target_harvest_salinity_g_l: string;
  /**
   * The operator's limits, held as raw strings like every other numeric input.
   * A blank field is a limit nobody configured — it is submitted as null, not as
   * zero, because zero is a number this project would be inventing.
   */
  flood_depth_cm: string;
  dilution_salinity_g_l: string;
  critical_rainfall_24h_mm: string;
  target_flow_cm: string;
  max_observation_age_hours: string;
  /**
   * Write-only: the stored number is never returned, so this starts blank on the
   * edit form and typing a value replaces whatever is recorded. Blank means "leave
   * it as it is", not "remove it".
   */
  farmer_name: string;
  farmer_whatsapp: string;
};

export const EMPTY_PAN_FORM: SaltPanFormValues = {
  pan_id: "",
  pan_name: "",
  location: "",
  latitude: "",
  longitude: "",
  area_m2: "",
  normal_depth_cm: "",
  target_harvest_salinity_g_l: "",
  flood_depth_cm: "",
  dilution_salinity_g_l: "",
  critical_rainfall_24h_mm: "",
  target_flow_cm: "",
  max_observation_age_hours: "",
  farmer_name: "",
  farmer_whatsapp: "",
};

export type PanFormErrors = Partial<Record<keyof SaltPanFormValues, string>>;

export const PAN_ID_PATTERN = /^[A-Za-z0-9._-]+$/;

export const LATITUDE_RANGE = { min: -90, max: 90 } as const;
export const LONGITUDE_RANGE = { min: -180, max: 180 } as const;

/**
 * Result of validating one form field.
 *
 * `value` is the coerced number, or `null` when the field was left blank.
 * `error` is `null` when the input is acceptable, `"required"` when a mandatory
 * field is missing, and a sentence when the value is present but out of range.
 */
export type CoercedField = {
  value: number | null;
  error: string | null;
};

function parseNumericField(
  raw: string,
  {
    required,
    label,
    range,
    unit,
  }: {
    required: boolean;
    label: string;
    range?: { min: number; max: number };
    unit?: string;
  },
): CoercedField {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return {
      value: null,
      error: required ? `${label} is required.` : null,
    };
  }
  if (!/^-?\d*\.?\d+$/.test(trimmed)) {
    return { value: null, error: `${label} must be a number.` };
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) {
    return { value: null, error: `${label} must be a finite number.` };
  }
  if (unit && parsed <= 0) {
    return { value: null, error: `${label} must be greater than 0 ${unit}.` };
  }
  if (range && (parsed < range.min || parsed > range.max)) {
    return {
      value: null,
      error: `${label} must be between ${range.min} and ${range.max}.`,
    };
  }
  return { value: parsed, error: null };
}

/** Validates a strictly positive measurement, as used for area, depth and salinity. */
export function validatePositiveMeasurement(
  raw: string,
  { label, unit }: { label: string; unit: string },
): CoercedField {
  return parseNumericField(raw, { required: true, label, unit });
}

/**
 * The five operator limits, with the label, unit and lower bound the backend
 * enforces: `flood_depth_cm`, `dilution_salinity_g_l` and `critical_rainfall_24h_mm`
 * may be zero, while `target_flow_cm` and `max_observation_age_hours` must be
 * greater than zero. Declared as data so the form, the validator and their tests
 * name the same fields in the same order.
 */
export const RISK_THRESHOLD_SPECS = [
  { field: "flood_depth_cm", label: "Flood depth", unit: "cm", allowZero: true },
  {
    field: "dilution_salinity_g_l",
    label: "Dilution salinity",
    unit: "g/L",
    allowZero: true,
  },
  {
    field: "critical_rainfall_24h_mm",
    label: "Critical rainfall (24 h)",
    unit: "mm",
    allowZero: true,
  },
  {
    field: "target_flow_cm",
    label: "Target transfer depth",
    unit: "cm",
    allowZero: false,
  },
  {
    field: "max_observation_age_hours",
    label: "Maximum reading age",
    unit: "h",
    allowZero: false,
  },
] as const;

export type RiskThresholdField = (typeof RISK_THRESHOLD_SPECS)[number]["field"];

/**
 * Validates one operator limit.
 *
 * **A blank field is valid and means "not configured"**, returning `null` rather
 * than an error, because a limit nobody set is a legitimate state and not an
 * incomplete form. The bounds mirror `RiskThresholdsWrite` on the backend: the
 * three depth/rainfall/salinity limits accept zero, while the transfer depth and the
 * freshness window must be positive.
 */
export function validateRiskThreshold(
  raw: string,
  { label, unit, allowZero }: { label: string; unit: string; allowZero: boolean },
): CoercedField {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null, error: null };
  if (!/^-?\d*\.?\d+$/.test(trimmed)) {
    return { value: null, error: `${label} must be a number.` };
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) {
    return { value: null, error: `${label} must be a finite number.` };
  }
  if (allowZero) {
    if (parsed < 0) {
      return { value: null, error: `${label} must be 0 or greater.` };
    }
  } else if (parsed <= 0) {
    return { value: null, error: `${label} must be greater than 0 ${unit}.` };
  }
  return { value: parsed, error: null };
}

/**
 * Validates the whole operator-limits block, returning the value to submit and any
 * field errors. Always returns all five keys, so the block can be submitted whole:
 * a blank field becomes null ("not configured") rather than being dropped, which is
 * what lets an operator clear a limit they had set.
 */
export function validateRiskThresholds(values: SaltPanFormValues): {
  value: RiskThresholds;
  errors: Partial<Record<RiskThresholdField, string>>;
} {
  const value: RiskThresholds = { ...EMPTY_RISK_THRESHOLDS };
  const errors: Partial<Record<RiskThresholdField, string>> = {};
  for (const spec of RISK_THRESHOLD_SPECS) {
    const result = validateRiskThreshold(values[spec.field], spec);
    if (result.error) {
      errors[spec.field] = result.error;
    } else {
      value[spec.field] = result.value;
    }
  }
  return { value, errors };
}

/**
 * Validates a decimal-degree coordinate.
 *
 * Coordinates are optional, but latitude and longitude must be supplied
 * together, matching the persistence model.
 */
export function validateCoordinate(
  raw: string,
  {
    label,
    min,
    max,
    partnerLabel,
    partnerPresent,
  }: {
    label: string;
    min: number;
    max: number;
    partnerLabel: string;
    partnerPresent: boolean;
  },
): CoercedField {
  const trimmed = raw.trim();
  if (trimmed === "") {
    if (partnerPresent) {
      return {
        value: null,
        error: `${label} is required because ${partnerLabel} was supplied.`,
      };
    }
    return { value: null, error: null };
  }
  if (!/^-?\d*\.?\d+$/.test(trimmed)) {
    return { value: null, error: `${label} must be a number in decimal degrees.` };
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) {
    return { value: null, error: `${label} must be a finite number.` };
  }
  if (parsed < min || parsed > max) {
    return { value: null, error: `${label} must be between ${min} and ${max}.` };
  }
  return { value: parsed, error: null };
}

export function validatePanId(raw: string): { value: string; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return { value: "", error: "Pan ID is required." };
  }
  if (trimmed.length > 64) {
    return { value: trimmed, error: "Pan ID must be 64 characters or fewer." };
  }
  if (!PAN_ID_PATTERN.test(trimmed)) {
    return {
      value: trimmed,
      error: "Pan ID may contain letters, digits, dots, underscores and hyphens only.",
    };
  }
  return { value: trimmed, error: null };
}

export function validateRequiredText(
  raw: string,
  { label, maximum = 128 }: { label: string; maximum?: number },
): { value: string; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return { value: trimmed, error: `${label} is required.` };
  }
  if (trimmed.length > maximum) {
    return {
      value: trimmed,
      error: `${label} must be ${maximum} characters or fewer.`,
    };
  }
  return { value: trimmed, error: null };
}

/** Full-form validation for registration. Returns coerced values and any errors. */
export function validateRegistration(
  values: SaltPanFormValues,
): { payload: SaltPanRegistration | null; errors: PanFormErrors } {
  const panId = validatePanId(values.pan_id);
  const panName = validateRequiredText(values.pan_name, { label: "Pan name" });
  const location = validateRequiredText(values.location, { label: "Location" });
  const area = validatePositiveMeasurement(values.area_m2, {
    label: "Pan area",
    unit: "m²",
  });
  const depth = validatePositiveMeasurement(values.normal_depth_cm, {
    label: "Normal water depth",
    unit: "cm",
  });
  const salinity = validatePositiveMeasurement(
    values.target_harvest_salinity_g_l,
    { label: "Target harvest salinity", unit: "g/L" },
  );
  const latitude = validateCoordinate(values.latitude, {
    label: "Latitude",
    min: LATITUDE_RANGE.min,
    max: LATITUDE_RANGE.max,
    partnerLabel: "longitude",
    partnerPresent: values.longitude.trim() !== "",
  });
  const longitude = validateCoordinate(values.longitude, {
    label: "Longitude",
    min: LONGITUDE_RANGE.min,
    max: LONGITUDE_RANGE.max,
    partnerLabel: "latitude",
    partnerPresent: values.latitude.trim() !== "",
  });

  const farmerName = validateFarmerName(values.farmer_name);
  // Only validated when a number was typed. A pan with no farmer recorded is a
  // legitimate registration, not an incomplete one, so the field is not required.
  const whatsapp =
    values.farmer_whatsapp.trim() === ""
      ? { value: null, error: null }
      : validateWhatsAppNumber(values.farmer_whatsapp);

  // The operator's own limits. None is required: an unconfigured limit is a state
  // the system is built to report, not a form that has been filled in wrongly.
  const thresholds = validateRiskThresholds(values);

  const errors: PanFormErrors = {};
  if (panId.error) errors.pan_id = panId.error;
  if (panName.error) errors.pan_name = panName.error;
  if (location.error) errors.location = location.error;
  if (area.error) errors.area_m2 = area.error;
  if (depth.error) errors.normal_depth_cm = depth.error;
  if (salinity.error) errors.target_harvest_salinity_g_l = salinity.error;
  if (latitude.error) errors.latitude = latitude.error;
  if (longitude.error) errors.longitude = longitude.error;
  if (farmerName.error) errors.farmer_name = farmerName.error;
  if (whatsapp.error) errors.farmer_whatsapp = whatsapp.error;
  Object.assign(errors, thresholds.errors);

  const hasErrors = Object.keys(errors).length > 0;
  const measurementsMissing =
    area.value === null || depth.value === null || salinity.value === null;

  if (hasErrors || measurementsMissing) {
    return { payload: null, errors };
  }

  return {
    payload: {
      pan_id: panId.value,
      pan_name: panName.value,
      location: location.value,
      latitude: latitude.value,
      longitude: longitude.value,
      area_m2: area.value as number,
      normal_depth_cm: depth.value as number,
      target_harvest_salinity_g_l: salinity.value as number,
      risk_thresholds: thresholds.value,
      farmer_name: farmerName.value,
      farmer_whatsapp: whatsapp.value,
    },
    errors: {},
  };
}

/**
 * Validation for the edit form.
 *
 * The edit form is pre-filled with the stored values, so it validates the same
 * way registration does and returns every editable field. `pan_id` is excluded
 * by default because the identifier is immutable after registration; pass
 * `includePanId` only where the field is editable.
 */
export function validateUpdate(
  values: SaltPanFormValues,
  {
    includePanId = false,
    panHasFarmerName = false,
  }: { includePanId?: boolean; panHasFarmerName?: boolean } = {},
): { payload: SaltPanUpdate | null; errors: PanFormErrors } {
  const errors: PanFormErrors = {};
  const payload: Record<string, unknown> = {};

  if (includePanId) {
    const panId = validatePanId(values.pan_id);
    if (panId.error) {
      errors.pan_id = panId.error;
    } else {
      payload.pan_id = panId.value;
    }
  }

  for (const field of ["pan_name", "location"] as const) {
    const label = field === "pan_name" ? "Pan name" : "Location";
    const result = validateRequiredText(values[field], { label });
    if (result.error) {
      errors[field] = result.error;
    } else {
      payload[field] = result.value;
    }
  }

  for (const [field, label, unit] of [
    ["area_m2", "Pan area", "m²"],
    ["normal_depth_cm", "Normal water depth", "cm"],
    ["target_harvest_salinity_g_l", "Target harvest salinity", "g/L"],
  ] as const) {
    const result = validatePositiveMeasurement(values[field], { label, unit });
    if (result.error) {
      errors[field] = result.error;
    } else {
      payload[field] = result.value;
    }
  }

  const latitude = validateCoordinate(values.latitude, {
    label: "Latitude",
    min: LATITUDE_RANGE.min,
    max: LATITUDE_RANGE.max,
    partnerLabel: "longitude",
    partnerPresent: values.longitude.trim() !== "",
  });
  const longitude = validateCoordinate(values.longitude, {
    label: "Longitude",
    min: LONGITUDE_RANGE.min,
    max: LONGITUDE_RANGE.max,
    partnerLabel: "latitude",
    partnerPresent: values.latitude.trim() !== "",
  });
  if (latitude.error) errors.latitude = latitude.error;
  if (longitude.error) errors.longitude = longitude.error;
  if (!latitude.error && !longitude.error) {
    payload.latitude = latitude.value;
    payload.longitude = longitude.value;
  }

  // The name is included whenever it is non-empty, and cleared by an explicit empty
  // string rather than by omission — an operator who clears a name means to clear
  // it. The number is different: it is never returned by the read API, so the form
  // is always blank, and omission is the only way to express "leave the recorded
  // number alone". Typing a value replaces it.
  const farmerName = validateFarmerName(values.farmer_name);
  if (farmerName.error) {
    errors.farmer_name = farmerName.error;
  } else if (values.farmer_name.trim() !== "" || panHasFarmerName) {
    payload.farmer_name = farmerName.value;
  }

  if (values.farmer_whatsapp.trim() !== "") {
    const whatsapp = validateWhatsAppNumber(values.farmer_whatsapp);
    if (whatsapp.error) {
      errors.farmer_whatsapp = whatsapp.error;
    } else {
      payload.farmer_whatsapp = whatsapp.value;
    }
  }

  // The operator's limits are submitted as a whole block, matching the backend,
  // which replaces the block rather than merging into it. Because the edit form is
  // pre-filled from the stored pan, saving without touching them re-sends what is
  // already there, and clearing a field clears that limit rather than leaving the
  // old value in place.
  const thresholds = validateRiskThresholds(values);
  Object.assign(errors, thresholds.errors);
  if (Object.keys(thresholds.errors).length === 0) {
    payload.risk_thresholds = thresholds.value;
  }

  if (Object.keys(errors).length > 0) {
    return { payload: null, errors };
  }
  return { payload: payload as SaltPanUpdate, errors: {} };
}

/** Normalises a pan identifier the way the API does, for display and routing. */
export function normalizePanId(value: string): string {
  return value.trim().toLowerCase();
}

/** Seeds the edit form from a stored pan. */
export function panToFormValues(pan: SaltPan): SaltPanFormValues {
  const thresholds = pan.risk_thresholds ?? EMPTY_RISK_THRESHOLDS;
  return {
    pan_id: pan.pan_id,
    pan_name: pan.pan_name,
    location: pan.location,
    latitude: pan.latitude === null ? "" : String(pan.latitude),
    longitude: pan.longitude === null ? "" : String(pan.longitude),
    area_m2: String(pan.area_m2),
    normal_depth_cm: String(pan.normal_depth_cm),
    target_harvest_salinity_g_l: String(pan.target_harvest_salinity_g_l),
    // A stored null is shown as a blank field, because a limit nobody configured is
    // exactly what an empty box means. The operator sees at a glance what is set
    // and what a rule would still be refused for.
    flood_depth_cm: thresholdToString(thresholds.flood_depth_cm),
    dilution_salinity_g_l: thresholdToString(thresholds.dilution_salinity_g_l),
    critical_rainfall_24h_mm: thresholdToString(thresholds.critical_rainfall_24h_mm),
    target_flow_cm: thresholdToString(thresholds.target_flow_cm),
    max_observation_age_hours: thresholdToString(
      thresholds.max_observation_age_hours,
    ),
    // The name round-trips because it is not sensitive. The number does not: only
    // the mask ever leaves the server, so the field starts blank and the operator
    // types a number to replace whatever is recorded.
    farmer_name: pan.farmer_name ?? "",
    farmer_whatsapp: "",
  };
}

/** Renders a stored limit as an input value; an unconfigured limit is blank. */
function thresholdToString(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

/**
 * Measurements are shown with a decimal point and thin thousands separators.
 * A decimal comma would clash with the decimal-point convention used for
 * operator input and for coordinates, so the en-US grouping is used
 * deliberately rather than a locale-derived format.
 */
const NUMBER_FORMAT = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

export function formatMeasurement(value: number | null, unit: string): string {
  if (value === null) return "—";
  return `${NUMBER_FORMAT.format(value)} ${unit}`;
}

export function formatCoordinate(value: number | null, axis: "lat" | "lon"): string {
  if (value === null) return "—";
  const hemisphere =
    axis === "lat" ? (value >= 0 ? "N" : "S") : value >= 0 ? "E" : "W";
  return `${Math.abs(value).toFixed(4)}° ${hemisphere}`;
}

/** Square metres rendered as hectares when large enough to be more readable. */
export function formatArea(areaM2: number): string {
  if (areaM2 >= 10_000) {
    return `${NUMBER_FORMAT.format(areaM2 / 10_000)} ha`;
  }
  return `${NUMBER_FORMAT.format(areaM2)} m²`;
}

/** Volume in cubic metres, derived from the two operator-entered dimensions. */
export function referenceVolumeM3(pan: SaltPan): number {
  return (pan.area_m2 * pan.normal_depth_cm) / 100;
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(parsed);
}

/**
 * Farmer contact validation, mirroring the backend's `app/models/phone.py`.
 *
 * **The rule is E.164 and nothing softer.** A `+` and a country code, 8 to 15 digits
 * in total. A national-format number is refused here rather than guessed at: the
 * backend cannot know which country an operator is dialling from, so a number
 * without a country code has one correct answer only if a person supplies it, and
 * guessing would mean messaging a stranger.
 *
 * **Separators are stripped, not rejected.** Numbers get read aloud and written down
 * with spaces, hyphens and parentheses, and `"+91 98765-43210"` is the same number as
 * `"+919876543210"`. Refusing it would train operators to work around the form.
 *
 * **Client validation is a convenience, not the boundary.** The server validates
 * again and its answer is the one that counts; this exists so an operator finds out
 * about a typo before submitting rather than after.
 */
export const E164_PATTERN = /^\+[1-9]\d{7,14}$/;

/** The minimum and maximum digit counts E.164 permits, inclusive. */
export const E164_MIN_DIGITS = 8;
export const E164_MAX_DIGITS = 15;

/**
 * Turn what was typed into an E.164 number, or explain why it cannot be one.
 *
 * Returns the normalised value rather than only a verdict, so the form can submit
 * the clean form — which is also what gets stored.
 */
export function validateWhatsAppNumber(
  value: string,
): { value: string | null; error: string | null } {
  const compact = value.replace(/[\s()\-.]/g, "");
  if (compact === "") {
    return {
      value: null,
      error:
        "Enter the farmer's number with its country code, for example +919876543210.",
    };
  }
  if (!compact.startsWith("+")) {
    return {
      value: null,
      error:
        "Start with + and the country code. A number without one cannot be dialled from outside its own country.",
    };
  }
  const digits = compact.slice(1);
  if (!/^\d+$/.test(digits)) {
    return { value: null, error: "A phone number holds digits only, after the +." };
  }
  if (digits.length < E164_MIN_DIGITS || digits.length > E164_MAX_DIGITS) {
    return {
      value: null,
      error: `A phone number has ${E164_MIN_DIGITS} to ${E164_MAX_DIGITS} digits after the country code.`,
    };
  }
  if (!E164_PATTERN.test(compact)) {
    return {
      value: null,
      error:
        "That is not a valid international number. Check the country code and try again.",
    };
  }
  return { value: compact, error: null };
}

/**
 * The farmer's name, or absent.
 *
 * Optional and unvalidated beyond length, because a name is not a fact the system
 * reasons about. A pan with no name recorded is still a pan that can be messaged.
 */
export function validateFarmerName(
  value: string,
): { value: string | null; error: string | null } {
  const trimmed = value.trim();
  if (trimmed === "") return { value: null, error: null };
  if (trimmed.length > 120) {
    return { value: null, error: "A farmer's name must be 120 characters or fewer." };
  }
  return { value: trimmed, error: null };
}
