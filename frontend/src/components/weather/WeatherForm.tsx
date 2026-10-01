"use client";

import { useState } from "react";
import { Button, SegmentedControl, Spinner } from "@/components/ui/Controls";
import { NumberField, SelectField, TextField } from "@/components/ui/Fields";
import { Alert } from "@/components/ui/Feedback";
import { ApiError } from "@/lib/api";
import {
  DEMONSTRATION_EXPLANATION,
  DEMONSTRATION_LABEL,
  DEMONSTRATION_NOTICE,
} from "@/lib/demonstration";
import {
  MANUALLY_SELECTABLE_SOURCES,
  WEATHER_RANGES,
  WEATHER_SOURCE_LABELS,
  emptyWeatherForm,
  isDemonstrationMode,
  resetWeatherForm,
  validateWeather,
  type ManualWeatherSource,
  type WeatherFormErrors,
  type WeatherFormValues,
  type WeatherMode,
  type WeatherObservation,
} from "@/lib/weather";
import { recordWeather } from "@/lib/weatherApi";

const MEASUREMENT_HINTS: Record<keyof typeof WEATHER_RANGES, string> = {
  rainfall_mm:
    "Zero means a forecast of no rain, which is different from leaving this blank.",
  rainfall_probability_percent: "Chance of rain. Leave blank if not known.",
  ambient_temperature_c: "Air temperature at the pan.",
  humidity_percent: "Relative humidity at the pan.",
};

/**
 * Entry for one weather record, as something real or as a demonstration.
 *
 * The form refuses to offer `weather_api` as a source, and shows it disabled
 * with the reason instead. An operator typing a number cannot produce a record
 * that later reads as if a service supplied it, which is the whole point: a
 * hand-entered figure is evidence, and labelling it honestly is part of the
 * evidence.
 *
 * The mode toggle adds the third case that refusal alone does not cover. A
 * hand-entered forecast is still a forecast somebody published, and filing one
 * as `manual` says exactly that. A demonstration is not: it is a rainfall
 * figure typed in to show the workflow, and the backend keeps such records out of
 * every research dataset. Without the toggle the only way to demonstrate a
 * forecast would be to file it as a real one.
 *
 * Every quantity is optional on its own and the form says so. A blank input is a
 * statement that the value was not measured, and it is sent and stored as such.
 */
export default function WeatherForm({
  panId,
  sensorAvailable = false,
  providersConnected = 0,
  disabled = false,
  onRecorded,
}: {
  panId: string;
  sensorAvailable?: boolean;
  providersConnected?: number;
  disabled?: boolean;
  onRecorded: (observation: WeatherObservation) => void;
}) {
  const [values, setValues] = useState<WeatherFormValues>(() =>
    emptyWeatherForm({ mode: "actual" }),
  );
  const [errors, setErrors] = useState<WeatherFormErrors>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [saved, setSaved] = useState<WeatherObservation | null>(null);

  const isForecast = values.forecast_for.trim() !== "";
  const demonstration = isDemonstrationMode(values);

  /**
   * Switching mode reseeds rather than merges.
   *
   * The demonstration's forecast instant is relative to now, so carrying a real
   * form's blank timestamps into it would leave a demonstration aimed at
   * nothing; and a real form inheriting the demonstration's values would file
   * somebody's fiction as an observation.
   */
  function setMode(mode: WeatherMode) {
    setValues(emptyWeatherForm({ mode }));
    setErrors({});
    setSaved(null);
  }

  function update<K extends keyof WeatherFormValues>(
    key: K,
    value: WeatherFormValues[K],
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

    const { payload, errors: localErrors } = validateWeather(values, { panId });
    if (!payload) {
      setErrors(localErrors);
      return;
    }
    setErrors({});

    setSubmitting(true);
    try {
      const stored = await recordWeather(payload);
      setValues(resetWeatherForm(values));
      setSaved(stored);
      onRecorded(stored);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setErrors(caught.fields as WeatherFormErrors);
        setSubmitError(caught.message);
      } else {
        setSubmitError("Unexpected error while recording this weather record.");
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
              Weather is only recorded for pans in the registry. This record
              cannot be written to the database, and nothing entered here would
              be stored.
            </p>
          </Alert>
        </div>
      ) : null}

      <div className="space-y-5 p-5">
        {submitError ? (
          <Alert tone="danger" title="The weather record was not saved">
            {submitError}
          </Alert>
        ) : null}
        {saved ? (
          <Alert
            tone={isDemonstrationMode({ source: saved.source }) ? "demo" : "success"}
            title={
              isDemonstrationMode({ source: saved.source })
                ? "Demonstration forecast recorded"
                : "Weather recorded"
            }
            action={
              <Button variant="ghost" onClick={() => setSaved(null)}>
                Dismiss
              </Button>
            }
          >
            <p>
              Saved against <span className="font-mono">{panId}</span> and
              labelled{" "}
              <strong className="font-semibold">
                {WEATHER_SOURCE_LABELS[saved.source]}
              </strong>
              .{" "}
              {isDemonstrationMode({ source: saved.source })
                ? "It keeps that label in the history below, and no research dataset is built from it."
                : "It will keep that label in the history below."}
            </p>
          </Alert>
        ) : null}

        {errors.form ? <Alert tone="warning">{errors.form}</Alert> : null}

        <SegmentedControl
          name="weather-mode"
          legend="What is this?"
          value={demonstration ? "demonstration" : "actual"}
          onChange={setMode}
          disabled={disabled || submitting}
          options={[
            {
              value: "actual",
              label: "A real reading or forecast",
              description:
                "What the sky is doing, or what a forecast actually said.",
            },
            {
              value: "demonstration",
              label: DEMONSTRATION_LABEL,
              description: DEMONSTRATION_EXPLANATION,
            },
          ]}
        />

        {demonstration ? (
          <Alert tone="demo" title={DEMONSTRATION_NOTICE}>
            <p>
              The rainfall below is fixed and read-only. It was not forecast by
              any service. SaltLens stores the record and shows it under the{" "}
              <strong className="font-semibold">{DEMONSTRATION_LABEL}</strong>{" "}
              label, and{" "}
              <strong className="font-semibold">
                no research dataset is built from it
              </strong>
              , so it can never become the rain a research row was measured
              against.
            </p>
          </Alert>
        ) : (
          <Alert tone="info" title="This record will be labelled as hand-entered">
            <p>
              Nothing on this form is fetched from an outside service. The record
              is stored with the source{" "}
              <strong className="font-semibold">
                {WEATHER_SOURCE_LABELS[values.source]}
              </strong>{" "}
              and SaltLens will not relabel it as an automated result later.
            </p>
          </Alert>
        )}

        <div className="grid gap-x-5 gap-y-5 sm:grid-cols-2">
          <TextField
            label="Observed at"
            name="weather-timestamp"
            value={values.timestamp}
            onChange={(value) => update("timestamp", value)}
            placeholder="Leave blank to record now"
            hint="When the weather was observed. Leave blank to stamp this record at the moment you save it, or supply an earlier time to backdate it."
            error={errors.timestamp}
            disabled={disabled || submitting}
          />
          <TextField
            label="Forecast for"
            name="weather-forecast-for"
            value={values.forecast_for}
            onChange={(value) => update("forecast_for", value)}
            placeholder="Leave blank unless this is a forecast"
            hint={
              demonstration
                ? "Fixed: the demonstration is aimed a few hours ahead of the moment it is saved."
                : "Only for a forecast: the instant it applies to. Leave blank when recording what actually happened."
            }
            error={errors.forecast_for}
            disabled={disabled || submitting || demonstration}
          />
        </div>

        {demonstration ? (
          <ReadOnlyWeatherProvenance source={values.source} />
        ) : (
          <SelectField
            label="Provenance"
            name="weather-source"
            value={values.source}
            onChange={(value) => update("source", value as ManualWeatherSource)}
            disabled={disabled || submitting}
            options={[
              ...MANUALLY_SELECTABLE_SOURCES.map((source) => ({
                value: source,
                label:
                  source === "sensor" && !sensorAvailable
                    ? `${WEATHER_SOURCE_LABELS[source]} — no gateway connected`
                    : WEATHER_SOURCE_LABELS[source],
                disabled: source === "sensor" && !sensorAvailable,
              })),
              {
                value: "weather_api",
                label:
                  providersConnected > 0
                    ? `${WEATHER_SOURCE_LABELS.weather_api} — set by forecast fetch, not by hand`
                    : `${WEATHER_SOURCE_LABELS.weather_api} — no weather API connected`,
                disabled: true,
              },
            ]}
            hint="How this record reached SaltLens. Weather API cannot be chosen here: that label is applied by the API when a connected service actually supplies data, so a typed figure is never filed as a fetched one."
          />
        )}

        {isForecast && !demonstration ? (
          <Alert tone="warning" title="This will be stored as a forecast">
            <p>
              A record with a forecast time describes a prediction, not an
              observation. It is marked as a forecast in the history so it is
              never compared against what actually happened without that being
              visible.
            </p>
          </Alert>
        ) : null}

        <fieldset
          disabled={disabled || submitting || demonstration}
          className="min-w-0"
        >
          <legend className="text-[13px] font-medium text-ink-soft">
            {demonstration ? "Demonstration forecast" : "Values"}
          </legend>
          <p className="mt-1 text-xs text-muted">
            {demonstration
              ? "Fixed values, aimed a few hours ahead of now. Switch to a real reading above to enter your own."
              : "Record what you actually measured, or what the forecast actually says. Leave the rest blank: a record that omits a value is honest, one that guesses it is not."}
          </p>
          <div className="mt-4 grid gap-x-5 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
            {(Object.keys(WEATHER_RANGES) as Array<
              keyof typeof WEATHER_RANGES
            >).map((key) => (
              <NumberField
                key={key}
                name={`weather-${key}`}
                label={WEATHER_RANGES[key].label}
                unit={WEATHER_RANGES[key].unit}
                value={values[key]}
                onChange={(value) => update(key, value)}
                hint={MEASUREMENT_HINTS[key]}
                error={errors[key]}
                required={false}
                step="any"
                min={WEATHER_RANGES[key].min}
                max={WEATHER_RANGES[key].max}
                readOnly={demonstration}
              />
            ))}
          </div>
        </fieldset>

        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button
            type="submit"
            variant="primary"
            disabled={disabled || submitting}
          >
            {submitting ? (
              <Spinner label="Saving…" />
            ) : demonstration ? (
              "Record demonstration forecast"
            ) : (
              "Record weather"
            )}
          </Button>
          <p className="text-[11px] leading-relaxed text-muted">
            Records are stored exactly as entered and cannot be edited
            afterwards. Every record is kept, so the weather history for this pan
            stays complete.
          </p>
        </div>
      </div>
    </form>
  );
}

/**
 * The provenance a demonstration forecast is filed under, stated not chosen.
 *
 * Same reasoning as the observation form's: offering a menu here would let an
 * operator file a typed-in rainfall figure as a forecast a service issued, and
 * would imply the label is a preference rather than a fact about the record.
 */
function ReadOnlyWeatherProvenance({ source }: { source: WeatherFormValues["source"] }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-[13px] font-medium text-ink-soft">Provenance</p>
      <div className="flex items-center rounded-lg border border-demo-line bg-demo-soft px-3 py-2">
        <span className="text-sm font-semibold text-demo">
          {WEATHER_SOURCE_LABELS[source]}
        </span>
      </div>
      <p className="text-xs text-muted">
        Fixed. No service issued this forecast, and the research dataset builder
        never reads it.
      </p>
    </div>
  );
}
