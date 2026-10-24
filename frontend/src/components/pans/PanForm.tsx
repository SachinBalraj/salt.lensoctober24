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
  /**
   * The farmer's number as already recorded, masked. Shown so an operator editing a
   * pan knows a contact exists without the form having to hold a real number.
   */
  recordedFarmerNumber?: string | null;
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
  recordedFarmerNumber,
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

      {/* The operator's own decision limits. Every field is optional and blank means
          "not configured" — this project supplies none of these numbers. A decision
          rule reads them from here and is refused, not defaulted, when one is unset,
          so this group is where a pan becomes advisable. */}
      <fieldset className="space-y-4" disabled={submitting}>
        <legend className="text-[11px] font-semibold uppercase tracking-[0.14em] text-faint">
          Risk thresholds
        </legend>
        <p className="text-xs text-muted">
          Your own limits for this pan, compared against by the decision rule. Every
          field is optional and left blank means not configured — no value is assumed
          in its place. A rule that needs a limit you have not set is refused rather
          than advised against a number this software invented.
        </p>
        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField
            name={fieldId("flood_depth_cm")}
            label="Flood depth"
            unit="cm"
            value={values.flood_depth_cm}
            onChange={(value) => onChange("flood_depth_cm", value)}
            error={errors.flood_depth_cm}
            min={0}
            step={0.1}
            required={false}
            placeholder="e.g. 25"
            hint="Drain the pan once the predicted depth reaches this."
          />
          <NumberField
            name={fieldId("dilution_salinity_g_l")}
            label="Dilution salinity"
            unit="g/L"
            value={values.dilution_salinity_g_l}
            onChange={(value) => onChange("dilution_salinity_g_l", value)}
            error={errors.dilution_salinity_g_l}
            min={0}
            step={0.1}
            required={false}
            placeholder="e.g. 180"
            hint="Below this the brine is too dilute to sell."
          />
          <NumberField
            name={fieldId("critical_rainfall_24h_mm")}
            label="Critical rainfall (24 h)"
            unit="mm"
            value={values.critical_rainfall_24h_mm}
            onChange={(value) => onChange("critical_rainfall_24h_mm", value)}
            error={errors.critical_rainfall_24h_mm}
            min={0}
            step={1}
            required={false}
            placeholder="e.g. 50"
            hint="Rainfall over 24 h above which the pan is at risk."
          />
          <NumberField
            name={fieldId("target_flow_cm")}
            label="Target transfer depth"
            unit="cm"
            value={values.target_flow_cm}
            onChange={(value) => onChange("target_flow_cm", value)}
            error={errors.target_flow_cm}
            min={0}
            step={0.1}
            required={false}
            placeholder="e.g. 12"
            hint="The depth you keep when transferring brine."
          />
          <NumberField
            name={fieldId("max_observation_age_hours")}
            label="Maximum reading age"
            unit="h"
            value={values.max_observation_age_hours}
            onChange={(value) => onChange("max_observation_age_hours", value)}
            error={errors.max_observation_age_hours}
            min={0}
            step={1}
            required={false}
            placeholder="e.g. 24"
            hint="Oldest reading that may still support a recommendation."
          />
        </div>
      </fieldset>

      {/* Farmer contact lives in its own group because it is optional and behaves
          differently on the two forms: on registration there is nothing recorded,
          and on edit the number is deliberately blank because the read API never
          returns it. Typing a number adds or replaces one; leaving it blank changes
          nothing. */}
      <fieldset className="space-y-4" disabled={submitting}>
        <legend className="text-[11px] font-semibold uppercase tracking-[0.14em] text-faint">
          Farmer contact
        </legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            name={fieldId("farmer_name")}
            label="Farmer name"
            value={values.farmer_name}
            onChange={(value) => onChange("farmer_name", value)}
            error={errors.farmer_name}
            maxLength={120}
            placeholder="e.g. R. Patel"
            hint="Optional. Used only to confirm who is being messaged."
          />
          <TextField
            name={fieldId("farmer_whatsapp")}
            label="WhatsApp number"
            value={values.farmer_whatsapp}
            onChange={(value) => onChange("farmer_whatsapp", value)}
            error={errors.farmer_whatsapp}
            type="tel"
            autoComplete="off"
            placeholder="+919876543210"
            hint={
              recordedFarmerNumber
                ? `Recorded as ${recordedFarmerNumber}. The full number is never shown again — type a number here to replace it, or leave this blank to keep it.`
                : "Optional. Include the country code, as +919876543210. A recommendation can only be sent to a farmer whose number is recorded here."
            }
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
