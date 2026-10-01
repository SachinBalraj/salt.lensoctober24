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

  const errors: PanFormErrors = {};
  if (panId.error) errors.pan_id = panId.error;
  if (panName.error) errors.pan_name = panName.error;
  if (location.error) errors.location = location.error;
  if (area.error) errors.area_m2 = area.error;
  if (depth.error) errors.normal_depth_cm = depth.error;
  if (salinity.error) errors.target_harvest_salinity_g_l = salinity.error;
  if (latitude.error) errors.latitude = latitude.error;
  if (longitude.error) errors.longitude = longitude.error;

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
  { includePanId = false }: { includePanId?: boolean } = {},
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
  return {
    pan_id: pan.pan_id,
    pan_name: pan.pan_name,
    location: pan.location,
    latitude: pan.latitude === null ? "" : String(pan.latitude),
    longitude: pan.longitude === null ? "" : String(pan.longitude),
    area_m2: String(pan.area_m2),
    normal_depth_cm: String(pan.normal_depth_cm),
    target_harvest_salinity_g_l: String(pan.target_harvest_salinity_g_l),
  };
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
