"use client";

import { useState } from "react";
import { Button, ButtonRow } from "@/components/ui/Controls";
import { Alert } from "@/components/ui/Feedback";
import {
  NumberField,
  TextAreaField,
  TextField,
} from "@/components/ui/Fields";
import { ApiError } from "@/lib/api";
import {
  buildExperimentCreate,
  EMPTY_EXPERIMENT_FORM,
  type ExperimentCreateResponse,
  type ExperimentFormErrors,
  type ExperimentFormValues,
} from "@/lib/experiment";
import { createExperiment } from "@/lib/experimentApi";

/**
 * The recording half of `/experiments`.
 *
 * A finished run is recorded with everything that produced it: the dataset by
 * its identity, the observations and pans, the date range, the feature set, the
 * target definition, the split, the seed, the model, its hyperparameters, the
 * metrics and the software versions. Fields marked optional may be left blank,
 * but nothing a run depends on may be omitted — the ledger stores a claim that
 * can be re-derived, not a summary that has to be trusted.
 */
export default function ExperimentRecorder({
  onRecorded,
  onCancelled,
}: {
  onRecorded: (outcome: ExperimentCreateResponse) => void;
  onCancelled: () => void;
}) {
  const [values, setValues] = useState<ExperimentFormValues>(EMPTY_EXPERIMENT_FORM);
  const [errors, setErrors] = useState<ExperimentFormErrors>({});
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleChange(
    field: keyof ExperimentFormValues,
    value: string,
  ) {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  async function handleSubmit() {
    const { payload, errors: validationErrors } = buildExperimentCreate(values);
    if (!payload) {
      setErrors(validationErrors);
      return;
    }
    setErrors({});
    setFailure(null);
    setSubmitting(true);
    try {
      const outcome = await createExperiment(payload);
      onRecorded(outcome);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setFailure(caught);
        if (Object.keys(caught.fields).length > 0) {
          setErrors(caught.fields as ExperimentFormErrors);
        }
      } else {
        setFailure(
          new ApiError("Unexpected error while recording the experiment.", {
            status: -1,
          }),
        );
      }
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-x-5 gap-y-4 md:grid-cols-2">
        <div className="flex flex-col gap-4">
          <NumberField
            name="dataset_version"
            label="Dataset version"
            value={values.dataset_version}
            onChange={(value) => handleChange("dataset_version", value)}
            error={errors.dataset_version}
            hint="The version of the dataset the run was built on."
          />
          <TextField
            name="dataset_hash"
            label="Dataset content hash"
            value={values.dataset_hash}
            onChange={(value) => handleChange("dataset_hash", value)}
            error={errors.dataset_hash}
            hint='The Module 07 identity, like "sha256:…".'
            placeholder="sha256:"
          />
          <NumberField
            name="n_observations"
            label="Observations used"
            value={values.n_observations}
            onChange={(value) => handleChange("n_observations", value)}
            error={errors.n_observations}
            hint="Rows in the dataset this run actually used."
          />
          <NumberField
            name="n_pans"
            label="Pans spanned"
            value={values.n_pans}
            onChange={(value) => handleChange("n_pans", value)}
            error={errors.n_pans}
            hint="Distinct pans the data covers."
          />
        </div>

        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <TextField
              name="date_start"
              label="Data starts"
              value={values.date_start}
              onChange={(value) => handleChange("date_start", value)}
              error={errors.date_start}
              placeholder="2025-01-01"
              hint="Inclusive, YYYY-MM-DD."
            />
            <TextField
              name="date_end"
              label="Data ends"
              value={values.date_end}
              onChange={(value) => handleChange("date_end", value)}
              error={errors.date_end}
              placeholder="2026-03-31"
              hint="Inclusive, YYYY-MM-DD."
            />
          </div>
          <TextField
            name="features"
            label="Features, in order"
            value={values.features}
            onChange={(value) => handleChange("features", value)}
            error={errors.features}
            hint="Comma-separated, in the order the model consumed them."
            placeholder="pre_event_salinity_g_l, depth_cm, rainfall_mm"
          />
          <div className="grid grid-cols-2 gap-3">
            <TextField
              name="target"
              label="Target"
              value={values.target}
              onChange={(value) => handleChange("target", value)}
              error={errors.target}
              hint="The dataset target the run predicted."
            />
            <NumberField
              name="target_definition_version"
              label="Definition version"
              value={values.target_definition_version}
              onChange={(value) =>
                handleChange("target_definition_version", value)
              }
              error={errors.target_definition_version}
              hint="Version the label was built from."
            />
          </div>
        </div>

        <div className="flex flex-col gap-4">
          <TextField
            name="model"
            label="Model"
            value={values.model}
            onChange={(value) => handleChange("model", value)}
            error={errors.model}
            hint="As the researcher names it, e.g. random-forest."
          />
          <TextField
            name="train_test_strategy"
            label="Train/test strategy"
            value={values.train_test_strategy}
            onChange={(value) => handleChange("train_test_strategy", value)}
            error={errors.train_test_strategy}
            hint='In words, e.g. "80/20 chronological holdout".'
          />
          <NumberField
            name="random_seed"
            label="Random seed"
            value={values.random_seed}
            onChange={(value) => handleChange("random_seed", value)}
            error={errors.random_seed}
            hint="Pinned every shuffle and initialisation."
          />
          <TextAreaField
            name="notes"
            label="Notes"
            value={values.notes}
            onChange={(value) => handleChange("notes", value)}
            error={errors.notes}
            hint="Free text; part of the identity like everything else."
            rows={2}
          />
        </div>

        <div className="flex flex-col gap-4">
          <TextAreaField
            name="hyperparameters_json"
            label="Hyperparameters"
            value={values.hyperparameters_json}
            onChange={(value) => handleChange("hyperparameters_json", value)}
            error={errors.hyperparameters_json}
            hint='JSON object, e.g. {"n_estimators": 300, "class_weight": "balanced"}.'
            placeholder="{ }"
            rows={2}
          />
          <TextAreaField
            name="metrics_json"
            label="Metrics"
            value={values.metrics_json}
            onChange={(value) => handleChange("metrics_json", value)}
            error={errors.metrics_json}
            hint='JSON object of metric to number, e.g. {"accuracy": 0.94}.'
            placeholder="{ }"
            rows={2}
          />
          <TextAreaField
            name="software_versions_json"
            label="Software & library versions"
            value={values.software_versions_json}
            onChange={(value) => handleChange("software_versions_json", value)}
            error={errors.software_versions_json}
            hint='JSON object, e.g. {"python": "3.12", "sklearn": "1.6.1"}.'
            placeholder="{ }"
            rows={2}
          />
        </div>
      </div>

      {failure ? (
        <div className="mt-5">
          <Alert
            tone={failure.isConnectivity ? "warning" : "danger"}
            title={
              failure.isConnectivity
                ? "The ledger is unavailable"
                : "The experiment could not be recorded"
            }
          >
            <p>{failure.message}</p>
            {failure.isConnectivity ? (
              <p className="mt-1 text-xs">
                Your entries are still on screen. Start the backend, or set{" "}
                <code className="rounded bg-surface/70 px-1 font-mono">
                  MONGODB_URI
                </code>{" "}
                so the API has a database to write to.
              </p>
            ) : null}
          </Alert>
        </div>
      ) : null}

      <div className="mt-6">
        <ButtonRow>
          <Button
            variant="primary"
            onClick={() => void handleSubmit()}
            disabled={submitting}
          >
            {submitting ? "Recording…" : "Record this experiment"}
          </Button>
          <Button onClick={onCancelled} disabled={submitting}>
            Cancel
          </Button>
        </ButtonRow>
        <p className="mt-2 text-xs text-muted">
          Recording the identical experiment again stores nothing new: the ledger
          returns the existing record. Results are never overwritten.
        </p>
      </div>
    </>
  );
}