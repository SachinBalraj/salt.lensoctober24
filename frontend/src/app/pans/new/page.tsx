"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import PanForm from "@/components/pans/PanForm";
import { Alert } from "@/components/ui/Feedback";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  EMPTY_PAN_FORM,
  type PanFormErrors,
  type SaltPanFormValues,
  validateRegistration,
} from "@/lib/pans";
import { registerPan } from "@/lib/pansApi";

export default function RegisterPanPage() {
  const router = useRouter();
  const [values, setValues] = useState<SaltPanFormValues>(EMPTY_PAN_FORM);
  const [errors, setErrors] = useState<PanFormErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<ApiError | null>(null);

  function handleChange(field: keyof SaltPanFormValues, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  async function handleSubmit() {
    const { payload, errors: validationErrors } = validateRegistration(values);
    if (!payload) {
      setErrors(validationErrors);
      return;
    }
    setErrors({});
    setFailure(null);
    setSubmitting(true);
    try {
      const created = await registerPan(payload);
      router.push(`/pans/${encodeURIComponent(created.pan_id)}?registered=1`);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setFailure(caught);
        setErrors(caught.fields as PanFormErrors);
      } else {
        setFailure(
          new ApiError("Unexpected error while registering the pan.", {
            status: -1,
          }),
        );
      }
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-5 py-7 lg:px-8 lg:py-9">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Module 02 · Salt-pan registration
        </p>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
          Register a new pan
        </h1>
        <p className="mt-1.5 max-w-2xl text-sm text-muted">
          Every value below is operator-declared. The registry stores what you
          enter; it never infers, averages or simulates a measurement.
        </p>
      </header>

      {failure ? (
        <div className="mt-5">
          <Alert
            tone={failure.status === 409 ? "warning" : "danger"}
            title={
              failure.status === 409
                ? "That pan ID is already registered"
                : failure.isConnectivity
                  ? "The registry is unavailable"
                  : "The pan could not be registered"
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

      <Card className="mt-5">
        <CardHeader
          title="Production unit"
          description="Fields marked required must be present; the coordinate pair may be left blank."
        />
        <div className="p-5">
          <PanForm
            values={values}
            errors={errors}
            onChange={handleChange}
            onSubmit={() => void handleSubmit()}
            onCancel={() => router.push("/pans")}
            submitting={submitting}
            panIdLocked={false}
            submitLabel="Register pan"
            idPrefix="register"
          />
        </div>
      </Card>
    </div>
  );
}
