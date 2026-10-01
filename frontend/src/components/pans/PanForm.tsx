"use client";

import { Button } from "@/components/ui/Controls";
import { NumberField, TextField } from "@/components/ui/Fields";
import {
  LATITUDE_RANGE,
  LONGITUDE_RANGE,
  type PanFormErrors,
  type SaltPanFormValues,
} from "@/lib/pans";

export type PanFormProps = {
  values: SaltPanFormValues;
  errors: PanFormErrors;
  onChange: (field: keyof SaltPanFormValues, value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  submitting: boolean;
  /** Edit mode locks the identifier and reuses the stored value. */
  panIdLocked: boolean;
  submitLabel: string;
  idPrefix: string;
};

export default function PanForm({
  values,
  errors,
  onChange,
  onSubmit,
  onCancel,
  submitting,
  panIdLocked,
  submitLabel,
  idPrefix,
}: PanFormProps) {
  const fieldId = (name: keyof SaltPanFormValues) => `${idPrefix}-${name}`;

  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      className="space-y-7"
    >
      <fieldset className="space-y-4" disabled={submitting}>
        <legend className="text-[11px] font-semibold uppercase tracking-[0.14em] text-faint">
          Identity
        </legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            name={fieldId("pan_id")}
            label="Pan ID"
            value={values.pan_id}
            onChange={(value) => onChange("pan_id", value)}
            error={errors.pan_id}
            readOnly={panIdLocked}
            maxLength={64}
            placeholder="pans-2026-a01"
            hint={
              panIdLocked
                ? "The pan ID is immutable: observations, weather and outcomes all reference it."
                : "Letters, digits, dots, underscores and hyphens. Stored lowercased."
            }
          />
          <TextField
            name={fieldId("pan_name")}
            label="Pan name"
            value={values.pan_name}
            onChange={(value) => onChange("pan_name", value)}
            error={errors.pan_name}
            maxLength={128}
            placeholder="Northern evaporation pan"
          />
        </div>
        <TextField
          name={fieldId("location")}
          label="Location"
          value={values.location}
          onChange={(value) => onChange("location", value)}
          error={errors.location}
          maxLength={128}
          placeholder="Site or locality"
        />
      </fieldset>

      <fieldset className="space-y-4" disabled={submitting}>
        <legend className="text-[11px] font-semibold uppercase tracking-[0.14em] text-faint">
          Geometry
        </legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField
            name={fieldId("latitude")}
            label="Latitude"
            unit="°"
            value={values.latitude}
            onChange={(value) => onChange("latitude", value)}
            error={errors.latitude}
            min={LATITUDE_RANGE.min}
            max={LATITUDE_RANGE.max}
            step={0.0001}
            required={false}
            placeholder="e.g. -30.7444"
            hint={`Decimal degrees, ${LATITUDE_RANGE.min} to ${LATITUDE_RANGE.max}. Leave blank together with longitude for an ungeolocated pan.`}
          />
          <NumberField
            name={fieldId("longitude")}
            label="Longitude"
            unit="°"
            value={values.longitude}
            onChange={(value) => onChange("longitude", value)}
            error={errors.longitude}
            min={LONGITUDE_RANGE.min}
            max={LONGITUDE_RANGE.max}
            step={0.0001}
            required={false}
            placeholder="e.g. 30.8071"
            hint={`Decimal degrees, ${LONGITUDE_RANGE.min} to ${LONGITUDE_RANGE.max}.`}
          />
        </div>
      </fieldset>

      <fieldset className="space-y-4" disabled={submitting}>
        <legend className="text-[11px] font-semibold uppercase tracking-[0.14em] text-faint">
          Operating parameters
        </legend>
        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField
            name={fieldId("area_m2")}
            label="Pan area"
            unit="m²"
            value={values.area_m2}
            onChange={(value) => onChange("area_m2", value)}
            error={errors.area_m2}
            min={0}
            step={0.01}
            placeholder="e.g. 12500"
            hint="Must be greater than 0."
          />
          <NumberField
            name={fieldId("normal_depth_cm")}
            label="Normal water depth"
            unit="cm"
            value={values.normal_depth_cm}
            onChange={(value) => onChange("normal_depth_cm", value)}
            error={errors.normal_depth_cm}
            min={0}
            step={0.1}
            placeholder="e.g. 15.5"
            hint="Must be greater than 0."
          />
          <NumberField
            name={fieldId("target_harvest_salinity_g_l")}
            label="Target harvest salinity"
            unit="g/L"
            value={values.target_harvest_salinity_g_l}
            onChange={(value) =>
              onChange("target_harvest_salinity_g_l", value)
            }
            error={errors.target_harvest_salinity_g_l}
            min={0}
            step={0.1}
            placeholder="e.g. 285"
            hint="Must be greater than 0."
          />
        </div>
      </fieldset>

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-5">
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? "Saving…" : submitLabel}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
        <p className="text-xs text-faint">
          {panIdLocked
            ? "Saving sends the full set of editable fields."
            : "All fields are required except the coordinate pair."}
        </p>
      </div>
    </form>
  );
}
