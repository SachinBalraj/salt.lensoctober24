"use client";

import { useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import {
  NumberField,
  SelectField,
  TextAreaField,
  TextField,
} from "@/components/ui/Fields";
import { Alert } from "@/components/ui/Feedback";
import { ApiError } from "@/lib/api";
import {
  HARVEST_LABELS,
  MAX_EVENT_ID_LENGTH,
  MAX_OPERATOR_ACTION_LENGTH,
  OUTCOME_FIELD_RANGES,
  OUTCOME_RANGES,
  emptyOutcomeForm,
  resetOutcomeForm,
  validateOutcome,
  type EventOutcome,
  type HarvestOutcome,
  type OutcomeFormErrors,
  type OutcomeFormValues,
} from "@/lib/outcomes";
import { recordOutcome } from "@/lib/outcomesApi";

/**
 * Hints that state when a zero is evidence rather than a gap.
 *
 * The two questions an operator asks about a blank box are "is zero allowed?"
 * and "what happens if I leave this empty?", so each measurement says which it
 * is rather than leaving it to be guessed.
 */
const MEASUREMENT_HINTS: Record<string, string> = {
  forecast_rainfall_mm: "What the forecast said. Leave blank if none was issued.",
  actual_rainfall_mm: "What the gauge recorded. Zero is a real reading.",
  pre_event_salinity_g_l:
    "Needed to predict. Without it the model is not run at all.",
  pre_event_depth_cm: "Needed to predict. Without it the model is not run at all.",
  post_event_salinity_g_l:
    "The figure the prediction is graded against. Leave blank and the event waits for a reading.",
  post_event_depth_cm: "Not needed for the salinity comparison. Leave blank if unmeasured.",
};

/**
 * Operator entry for one actual outcome.
 *
 * Every figure is optional on its own, and the form is explicit about what a
 * blank means: not measured. A blank is sent as `null` and stored as `null`, and
 * the event is reported afterwards as not comparable rather than as a perfect
 * prediction.
 *
 * The form has no field for a predicted salinity or an error, because the
 * backend forbids one. The prediction is computed on the server from the figures
 * entered here, which is the only way the resulting accuracy figure means
 * anything.
 */
export default function OutcomeForm({
  panId,
  disabled = false,
  defaultEventId = "",
  onRecorded,
}: {
  panId: string;
  disabled?: boolean;
  defaultEventId?: string;
  onRecorded: (outcome: EventOutcome) => void;
}) {
  const [values, setValues] = useState<OutcomeFormValues>(() => ({
    ...emptyOutcomeForm(),
    event_id: defaultEventId,
  }));
  const [errors, setErrors] = useState<OutcomeFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [saved, setSaved] = useState<EventOutcome | null>(null);

  function update<K extends keyof OutcomeFormValues>(
    key: K,
    value: OutcomeFormValues[K],
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

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError(null);
    setSaved(null);

    const { payload, errors: localErrors } = validateOutcome(values, { panId });
    if (!payload) {
      setErrors(localErrors);
      return;
    }
    setErrors({});

    setSubmitting(true);
    try {
      const stored = await recordOutcome(payload);
      setValues(resetOutcomeForm(values));
      setSaved(stored);
      onRecorded(stored);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setErrors(caught.fields as OutcomeFormErrors);
        setSubmitError(caught.message);
      } else {
        setSubmitError("Unexpected error while recording this outcome.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  const savedTerms = saved?.simulation_error;
  const savedComparable = Boolean(savedTerms?.is_comparable);

  return (
    <form onSubmit={(event) => void handleSubmit(event)} noValidate>
      {disabled ? (
        <div className="px-5 pt-5">
          <Alert tone="info" title="Recording is unavailable for this record">
            <p>
              Outcomes are only recorded for pans in the registry. This record
              cannot be written to the database, and nothing entered here would
              be stored.
            </p>
          </Alert>
        </div>
      ) : null}

      <div className="space-y-5 p-5">
        {submitError ? (
          <Alert tone="danger" title="The outcome was not recorded">
            {submitError}
          </Alert>
        ) : null}

        {saved ? (
          <Alert
            tone="success"
            title="Outcome recorded"
            action={
              <Button variant="ghost" onClick={() => setSaved(null)}>
                Dismiss
              </Button>
            }
          >
            <p>
              Saved against{" "}
              <span className="font-mono">{panId}</span> as event{" "}
              <span className="font-mono">{saved.event_id}</span>.
            </p>
            <p className="mt-1 text-xs text-muted">
              The name is kept so the same storm can be filed against other pans.
              On this pan it is now used, so the next event here needs its own
              name — an event is recorded once.
            </p>
            {savedComparable && savedTerms ? (
              <p className="mt-1">
                The mass balance predicted{" "}
                <span className="font-mono">
                  {savedTerms.predicted?.toFixed(2)} {savedTerms.unit}
                </span>{" "}
                and the pan measured{" "}
                <span className="font-mono">
                  {savedTerms.actual?.toFixed(2)} {savedTerms.unit}
                </span>
                , a signed error of{" "}
                <span className="font-mono">
                  {savedTerms.signed_error?.toFixed(2)} {savedTerms.unit}
                </span>
                .
              </p>
            ) : (
              <p className="mt-1">
                The event is recorded but not yet comparable. It will be left out
                of the mean absolute error until the missing figures are
                supplied — it is not counted as a zero error in the meantime.
              </p>
            )}
          </Alert>
        ) : null}

        {errors.form ? <Alert tone="warning">{errors.form}</Alert> : null}

        <div className="grid gap-x-5 gap-y-5 sm:grid-cols-2">
          <TextField
            label="Event name"
            name="outcome-event-id"
            value={values.event_id}
            onChange={(value) => update("event_id", value)}
            placeholder="storm-2026-03-14"
            maxLength={MAX_EVENT_ID_LENGTH}
            hint="The operator's name for this event. Filing the same storm under the same name across pans is what lets them be compared."
            error={errors.event_id}
            disabled={disabled || submitting}
          />
          <TextField
            label="Event time"
            name="outcome-timestamp"
            value={values.timestamp}
            onChange={(value) => update("timestamp", value)}
            placeholder="Leave blank to record now"
            hint="Leave blank to stamp this event at the moment you save it, or supply an earlier time to backdate it."
            error={errors.timestamp}
            disabled={disabled || submitting}
          />
        </div>

        <fieldset disabled={disabled || submitting} className="min-w-0">
          <legend className="text-[13px] font-medium text-ink-soft">
            Before the event
          </legend>
          <p className="mt-1 text-xs text-muted">
            The starting state the prediction is computed from. Without the
            salinity and the depth the mass balance is not run at all, because a
            prediction from an assumed starting concentration would grade the
            model against a fiction.
          </p>
          <div className="mt-4 grid gap-x-5 gap-y-5 sm:grid-cols-2">
            <OutcomeNumberField
              field="pre_event_salinity_g_l"
              values={values}
              errors={errors}
              disabled={disabled || submitting}
              onChange={update}
            />
            <OutcomeNumberField
              field="pre_event_depth_cm"
              values={values}
              errors={errors}
              disabled={disabled || submitting}
              onChange={update}
            />
          </div>
        </fieldset>

        <fieldset disabled={disabled || submitting} className="min-w-0">
          <legend className="text-[13px] font-medium text-ink-soft">
            What actually happened
          </legend>
          <p className="mt-1 text-xs text-muted">
            Record what you actually measured. Leave the rest blank: an event
            that omits a value is honest, one that guesses it is not. A blank
            here keeps the event out of the mean rather than lowering it.
          </p>
          <div className="mt-4 grid gap-x-5 gap-y-5 sm:grid-cols-2">
            <OutcomeNumberField
              field="forecast_rainfall_mm"
              values={values}
              errors={errors}
              disabled={disabled || submitting}
              onChange={update}
            />
            <OutcomeNumberField
              field="actual_rainfall_mm"
              values={values}
              errors={errors}
              disabled={disabled || submitting}
              onChange={update}
            />
            <OutcomeNumberField
              field="post_event_salinity_g_l"
              values={values}
              errors={errors}
              disabled={disabled || submitting}
              onChange={update}
            />
            <OutcomeNumberField
              field="post_event_depth_cm"
              values={values}
              errors={errors}
              disabled={disabled || submitting}
              onChange={update}
            />
          </div>
        </fieldset>

        <div className="grid gap-x-5 gap-y-5 sm:grid-cols-2">
          <SelectField
            label="Harvest result"
            name="outcome-harvest"
            value={values.harvest_outcome}
            onChange={(value) =>
              update("harvest_outcome", value as "" | HarvestOutcome)
            }
            disabled={disabled || submitting}
            options={[
              { value: "", label: "Not reported" },
              ...(
                Object.keys(HARVEST_LABELS) as HarvestOutcome[]
              ).map((key) => ({ value: key, label: HARVEST_LABELS[key] })),
            ]}
            hint="“Nothing harvested” is a result. “Not reported” means no harvest result has come back yet, which is a different statement."
          />
          <TextAreaField
            label="What you did"
            name="outcome-operator-action"
            value={values.operator_action}
            onChange={(value) => update("operator_action", value)}
            placeholder="Opened the outlet gate for 40 minutes to spread the brine…"
            rows={3}
            maxLength={MAX_OPERATOR_ACTION_LENGTH}
            hint="This is the field that explains an event the model cannot. A recorded action on its own is enough to file an event."
            error={errors.operator_action}
            disabled={disabled || submitting}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button
            type="submit"
            variant="primary"
            disabled={disabled || submitting}
          >
            {submitting ? <Spinner label="Recording…" /> : "Record outcome"}
          </Button>
          <p className="text-[11px] leading-relaxed text-muted">
            The prediction and the error are computed by the mass balance on the
            server, from the figures above, and the run behind them is stored so
            the arithmetic can be replayed. Outcomes cannot be edited afterwards.
          </p>
        </div>
      </div>
    </form>
  );
}

/** One optional measurement, with its range and its own hint. */
function OutcomeNumberField({
  field,
  values,
  errors,
  disabled,
  onChange,
}: {
  field: keyof typeof OUTCOME_FIELD_RANGES;
  values: OutcomeFormValues;
  errors: OutcomeFormErrors;
  disabled: boolean;
  onChange: <K extends keyof OutcomeFormValues>(
    key: K,
    value: OutcomeFormValues[K],
  ) => void;
}) {
  const range = OUTCOME_RANGES[OUTCOME_FIELD_RANGES[field].range];
  return (
    <NumberField
      name={`outcome-${field}`}
      label={OUTCOME_FIELD_RANGES[field].label}
      unit={range.unit}
      value={values[field]}
      onChange={(value) => onChange(field, value)}
      hint={MEASUREMENT_HINTS[field]}
      error={errors[field]}
      required={false}
      step="any"
      min={range.min}
      max={range.max}
      disabled={disabled}
    />
  );
}
