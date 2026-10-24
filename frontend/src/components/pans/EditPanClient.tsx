"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import PanForm from "@/components/pans/PanForm";
import { Button } from "@/components/ui/Controls";
import { Alert, EmptyState, Skeleton } from "@/components/ui/Feedback";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  EMPTY_PAN_FORM,
  panToFormValues,
  type PanFormErrors,
  type SaltPan,
  type SaltPanFormValues,
  validateUpdate,
} from "@/lib/pans";
import { getPan, updatePan } from "@/lib/pansApi";

interface LoadedState {
  key: string;
  pan: SaltPan | null;
  error: ApiError | null;
}

export default function EditPanClient({ panId }: { panId: string }) {
  const router = useRouter();
  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<LoadedState | null>(null);
  const [values, setValues] = useState<SaltPanFormValues>(EMPTY_PAN_FORM);
  const [errors, setErrors] = useState<PanFormErrors>({});
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [dirty, setDirty] = useState(false);

  const requestKey = `${panId}|${reloadToken}`;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await getPan(panId);
        if (cancelled) return;
        setState({ key: requestKey, pan: loaded, error: null });
        setValues(panToFormValues(loaded));
      } catch (caught) {
        if (cancelled) return;
        setState({
          key: requestKey,
          pan: null,
          error:
            caught instanceof ApiError
              ? caught
              : new ApiError("Unexpected error loading this pan.", { status: -1 }),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, panId]);

  const loading = state?.key !== requestKey;
  const pan = state?.pan ?? null;
  const loadError = state?.error ?? null;

  function handleChange(field: keyof SaltPanFormValues, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setDirty(true);
    setErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  async function handleSubmit() {
    // `panHasFarmerName` is passed because the name is not sensitive and does
    // round-trip through the form, so clearing it has to be an explicit empty
    // string rather than an omission. The number is never returned and is handled
    // the other way round: omitted unless typed.
    const { payload, errors: validationErrors } = validateUpdate(values, {
      panHasFarmerName: (pan?.farmer_name ?? "") !== "",
    });
    if (!payload) {
      setErrors(validationErrors);
      return;
    }
    setErrors({});
    setSaveError(null);
    setSubmitting(true);
    try {
      await updatePan(panId, payload);
      router.push(`/pans/${encodeURIComponent(panId)}?edited=1`);
    } catch (caught) {
      const failure =
        caught instanceof ApiError
          ? caught
          : new ApiError("Unexpected error while saving.", { status: -1 });
      setSaveError(failure);
      setErrors(failure.fields as PanFormErrors);
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto w-full max-w-4xl px-5 py-7 lg:px-8 lg:py-9">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="mt-3 h-7 w-56" />
        <Skeleton className="mt-6 h-96 w-full" />
      </div>
    );
  }

  if (loadError || !pan) {
    return (
      <div className="mx-auto w-full max-w-3xl px-5 py-12 lg:px-8">
        <Card>
          <EmptyState
            title={
              loadError?.status === 404
                ? "Pan not found"
                : "This pan could not be loaded for editing"
            }
            description={
              loadError?.message ??
              `No registered pan has the ID “${panId}”.`
            }
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <button
                  type="button"
                  onClick={() => router.push("/pans")}
                  className="rounded-lg border border-line-strong bg-surface px-3.5 py-2 text-[13px] font-medium text-ink-soft hover:bg-inset"
                >
                  Back to registry
                </button>
                {loadError && loadError.status !== 404 ? (
                  <Button
                    variant="primary"
                    onClick={() => setReloadToken((token) => token + 1)}
                  >
                    Try again
                  </Button>
                ) : null}
              </div>
            }
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-5 py-7 lg:px-8 lg:py-9">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Module 02 · Salt-pan registration
        </p>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
          Edit {pan.pan_name}
        </h1>
        <p className="mt-1.5 text-sm text-muted">
          Pan ID{" "}
          <code className="rounded bg-inset px-1.5 py-0.5 font-mono text-[13px] text-ink-soft">
            {pan.pan_id}
          </code>{" "}
          cannot be changed. Observations, weather, twin states and outcomes are
          all keyed to it.
        </p>
      </header>

      {pan.is_active ? null : (
        <div className="mt-5">
          <Alert tone="warning" title="This pan is deactivated">
            You can still correct its recorded values. Deactivation is a
            one-way transition in Module 02, so it cannot be undone here.
          </Alert>
        </div>
      )}

      {saveError ? (
        <div className="mt-5">
          <Alert
            tone={saveError.status === 409 ? "warning" : "danger"}
            title={
              saveError.isConnectivity
                ? "The registry is unavailable"
                : "The changes could not be saved"
            }
          >
            <p>{saveError.message}</p>
            {saveError.isConnectivity ? (
              <p className="mt-1 text-xs">
                Your edits are still on screen and have not been discarded.
              </p>
            ) : null}
          </Alert>
        </div>
      ) : null}

      <Card className="mt-5">
        <CardHeader
          title="Edit pan information"
          description="Clear both coordinate fields to remove the coordinate pair from a pan."
        />
        <div className="p-5">
          <PanForm
            values={values}
            errors={errors}
            onChange={handleChange}
            onSubmit={() => void handleSubmit()}
            onCancel={() => {
              if (dirty && !window.confirm("Discard your unsaved changes?")) {
                return;
              }
              router.push(`/pans/${encodeURIComponent(panId)}`);
            }}
            submitting={submitting}
            panIdLocked
            submitLabel="Save changes"
            idPrefix="edit"
            recordedFarmerNumber={pan.farmer_whatsapp_masked}
          />
        </div>
      </Card>
    </div>
  );
}
