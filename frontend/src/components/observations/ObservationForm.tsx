"use client";

import { useState } from "react";
import { Button, SegmentedControl, Spinner } from "@/components/ui/Controls";
import {
  NumberField,
  SelectField,
  TextAreaField,
  TextField,
} from "@/components/ui/Fields";
import { Alert } from "@/components/ui/Feedback";
import { ApiError } from "@/lib/api";
import {
  BRINE_TEMPERATURE_NOTE,
  DEMONSTRATION_EXPLANATION,
  DEMONSTRATION_LABEL,
  DEMONSTRATION_NOTICE,
} from "@/lib/demonstration";
import {
  DATA_SOURCE_LABELS,
  MAX_NOTE_LENGTH,
  MEASUREMENT_RANGES,
  SELECTABLE_DATA_SOURCES,
  emptyObservationForm,
  isDemonstrationMode,
  resetObservationForm,
  validateObservation,
  type DataSource,
  type ObservationFormErrors,
  type ObservationFormValues,
  type ObservationMode,
  type PanObservation,
  type SelectableDataSource,
} from "@/lib/observations";
import { recordObservation } from "@/lib/observationsApi";

const MEASUREMENT_HINTS: Record<keyof typeof MEASUREMENT_RANGES, string> = {
  salinity_g_l: "Leave blank if not measured.",
  water_depth_cm: "Zero is a valid reading for a dry pan.",
  brine_temperature_c: "Temperature of the brine itself, not the air.",
  humidity_percent: "Relative humidity at the pan.",
  ambient_temperature_c: "Air temperature at the pan.",
};

/**
 * Entry for one observation, as a measurement or as a demonstration.
 *
 * Every measurement is optional on its own, and the form makes that explicit:
 * a blank input is a statement that the value was not measured, and it is sent
 * and stored as such. The form refuses to submit a reading with nothing
 * measured, and never fills a field in on the operator's behalf.
 *
 * The mode toggle is the load-bearing part. It opens on **demonstration**, because
 * a pan with no data is the case this screen is otherwise useless for, and
 * because a form that opens on blank fields invites the reader to believe the
 * numbers they are about to type are ones they measured. Demonstration values
 * are constants from `lib/demonstration`, they are shown read-only, and the
 * provenance select is replaced by a statement of what will be stored — a menu
 * offering `manual` next to a full set of numbers is an invitation to pick the
 * wrong one.
 */
export default function ObservationForm({
  panId,
  sensorAvailable = false,
  disabled = false,
  onRecorded,
}: {
  panId: string;
  sensorAvailable?: boolean;
  disabled?: boolean;
  onRecorded: (observation: PanObservation) => void;
}) {
  const [values, setValues] = useState<ObservationFormValues>(() =>
    emptyObservationForm({ mode: "demonstration" }),
  );
  const [errors, setErrors] = useState<ObservationFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [saved, setSaved] = useState<PanObservation | null>(null);
  const demonstration = isDemonstrationMode(values);

  function update<K extends keyof ObservationFormValues>(
    key: K,
    value: ObservationFormValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => {
      if (!current[key] && !current.form) return current;
      const next = { ...current };
      delete next[key];
      delete next.form;
      return next;
    });
    setSaved(null);
  }

  /**
   * Switching mode reseeds the form.
   *
   * Not a merge of the existing values into the new mode. A demonstration's
   * numbers and a measurement's are different things, and carrying whichever
   * fields happen to overlap from one to the other is how a measurement ends up
   * filed as a demonstration carrying two fields of somebody's fiction.
   */
  function setMode(mode: ObservationMode) {
    setValues(emptyObservationForm({ mode }));
    setErrors({});
    setSaved(null);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    setSaved(null);

    const { payload, errors: localErrors } = validateObservation(values, { panId });
    if (!payload) {
      setErrors(localErrors);
      return;
    }
    setErrors({});

    setSubmitting(true);
    try {
      const stored = await recordObservation(payload);
      setValues(resetObservationForm(values));
      setSaved(stored);
      onRecorded(stored);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setErrors(caught.fields as ObservationFormErrors);
        setSubmitError(caught.message);
      } else {
        setSubmitError("Unexpected error while recording this observation.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={(event) => void handleSubmit(event)} noValidate>
      {disabled ? (
        <div className="px-5 pt-5">
          <Alert tone="info" title="Recording is unavailable for this record">
            <p>
              Observations are only recorded for pans in the registry. This
              record cannot be written to the database, and nothing entered here
              would be stored.
            </p>
          </Alert>
        </div>
      ) : null}

      <div className="space-y-5 p-5">
        {submitError ? (
          <Alert tone="danger" title="The observation was not recorded">
            {submitError}
          </Alert>
        ) : null}
        {saved ? (
          <Alert
            tone={isDemonstrationMode({ data_source: saved.data_source }) ? "demo" : "success"}
            title={
              isDemonstrationMode({ data_source: saved.data_source })
                ? "Demonstration recorded"
                : "Observation recorded"
            }
            action={
              <Button variant="ghost" onClick={() => setSaved(null)}>
                Dismiss
              </Button>
            }
          >
            {isDemonstrationMode({ data_source: saved.data_source }) ? (
              <p>
                Saved against <span className="font-mono">{panId}</span> and
                labelled <strong className="font-semibold">demonstration</strong>
                . It appears on the dashboard so the workflow can be followed,
                and no research dataset is built from it.
              </p>
            ) : (
              <p>
                Saved against{" "}
                <span className="font-mono">{panId}</span>. The pan&apos;s latest
                observed state and the charts below now include this reading.
              </p>
            )}
          </Alert>
        ) : null}

        {errors.form ? <Alert tone="warning">{errors.form}</Alert> : null}

        <SegmentedControl
          name="observation-mode"
          legend="What is this?"
          value={demonstration ? "demonstration" : "measured"}
          onChange={setMode}
          disabled={disabled || submitting}
          options={[
            {
              value: "demonstration",
              label: DEMONSTRATION_LABEL,
              description: DEMONSTRATION_EXPLANATION,
            },
            {
              value: "measured",
              label: "A measurement",
              description: "You measured this pan, and the record is evidence.",
            },
          ]}
        />

        {demonstration ? (
          <Alert tone="demo" title={DEMONSTRATION_NOTICE}>
            <p>
              The values below are fixed and read-only. They are the
              demonstration&apos;s own, not this pan&apos;s. SaltLens stores the
              record and shows it on the dashboard under the{" "}
              <strong className="font-semibold">{DEMONSTRATION_LABEL}</strong>{" "}
              label, and{" "}
              <strong className="font-semibold">
                no research dataset is built from it
              </strong>
              .
            </p>
            <p className="mt-1.5">{BRINE_TEMPERATURE_NOTE}</p>
          </Alert>
        ) : null}

        <div className="grid gap-x-5 gap-y-5 sm:grid-cols-2">
          <TextField
            label="Observed at"
            name="observation-timestamp"
            value={values.timestamp}
            onChange={(value) => update("timestamp", value)}
            placeholder="Leave blank to record now"
            hint="Leave blank to stamp this reading at the moment you save it, or supply an earlier time to backdate it."
            error={errors.timestamp}
            disabled={disabled || submitting}
          />
          {demonstration ? (
            <ReadOnlyProvenance source={values.data_source} />
          ) : (
            <SelectField
              label="Provenance"
              name="observation-source"
              value={values.data_source}
              onChange={(value) =>
                update("data_source", value as SelectableDataSource)
              }
              disabled={disabled || submitting}
              options={SELECTABLE_DATA_SOURCES.map((source) => ({
                value: source,
                label:
                  source === "sensor" && !sensorAvailable
                    ? `${DATA_SOURCE_LABELS[source]} — no gateway connected`
                    : DATA_SOURCE_LABELS[source],
                disabled: source === "sensor" && !sensorAvailable,
              }))}
              hint={
                sensorAvailable
                  ? "How this reading reached SaltLens."
                  : "No sensor gateway is connected, so a reading cannot be attributed to an instrument."
              }
            />
          )}
        </div>

        <fieldset disabled={disabled || submitting || demonstration} className="min-w-0">
          <legend className="text-[13px] font-medium text-ink-soft">
            {demonstration ? "Demonstration readings" : "Measurements"}
          </legend>
          <p className="mt-1 text-xs text-muted">
            {demonstration
              ? "Fixed values. Switch to a measurement above to enter values of your own."
              : "Record what you actually measured. Leave the rest blank: a reading that omits a value is honest, one that guesses it is not."}
          </p>
          <div className="mt-4 grid gap-x-5 gap-y-5 sm:grid-cols-2 lg:grid-cols-3">
            {(
              Object.keys(MEASUREMENT_RANGES) as Array<
                keyof typeof MEASUREMENT_RANGES
              >
            ).map((key) => (
              <NumberField
                key={key}
                name={`observation-${key}`}
                label={MEASUREMENT_RANGES[key].label}
                unit={MEASUREMENT_RANGES[key].unit}
                value={values[key]}
                onChange={(value) => update(key, value)}
                hint={MEASUREMENT_HINTS[key]}
                error={errors[key]}
                required={false}
                step="any"
                min={MEASUREMENT_RANGES[key].min}
                max={MEASUREMENT_RANGES[key].max}
                readOnly={demonstration}
              />
            ))}
          </div>
        </fieldset>

        <TextAreaField
          label="Note"
          name="observation-note"
          value={values.note}
          onChange={(value) => update("note", value)}
          placeholder="Conditions, instrument used, anything that explains a gap…"
          maxLength={MAX_NOTE_LENGTH}
          hint={
            demonstration
              ? "Carries the demonstration marker. It is what makes the record recognisable outside SaltLens, in an export or a screenshot."
              : `Optional. ${MAX_NOTE_LENGTH} characters at most.`
          }
          error={errors.note}
          disabled={disabled || submitting || demonstration}
        />

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button
            type="submit"
            variant="primary"
            disabled={disabled || submitting}
          >
            {submitting ? (
              <Spinner label="Recording…" />
            ) : demonstration ? (
              "Record demonstration"
            ) : (
              "Record observation"
            )}
          </Button>
          <p className="text-[11px] leading-relaxed text-muted">
            {demonstration
              ? "Stored under the demonstration label and kept out of every research dataset. Demonstrations cannot be edited afterwards either."
              : "Readings are stored exactly as entered and cannot be edited afterwards."}
          </p>
        </div>
      </div>
    </form>
  );
}

/**
 * The provenance a demonstration is filed under, stated rather than chosen.
 *
 * A select here would be a lie in two directions at once: it would offer the
 * operator a way to file a demonstration as a manual reading, and it would
 * imply the label is a preference. So the field is a statement of a fixed fact,
 * and the form's own copy says what the label means.
 */
function ReadOnlyProvenance({ source }: { source: DataSource }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-[13px] font-medium text-ink-soft">Provenance</p>
      <div className="flex items-center rounded-lg border border-demo-line bg-demo-soft px-3 py-2">
        <span className="text-sm font-semibold text-demo">
          {DATA_SOURCE_LABELS[source]}
        </span>
      </div>
      <p className="text-xs text-muted">
        Fixed. A demonstration is always stored under this label, and the research
        dataset builder never reads it.
      </p>
    </div>
  );
}
