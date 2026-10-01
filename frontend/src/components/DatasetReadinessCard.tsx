"use client";

import { useEffect, useState } from "react";
import { Badge, Card, CardHeader, DataPoint } from "@/components/ui/Primitives";
import { Alert } from "@/components/ui/Feedback";
import { ApiError } from "@/lib/api";
import {
  countRows,
  headlineFigures,
  isReady,
  thresholdRows,
  type DatasetReadiness,
} from "@/lib/datasetReadiness";
import { fetchDatasetReadiness } from "@/lib/datasetReadinessApi";

/**
 * The T2 dataset-readiness audit shown on the Overview page.
 *
 * It renders the report the backend computed — verdict, the two figures an
 * operator can weigh it against, the stored counts, the thresholds, and the
 * reasons — as a flat table. Nothing here simulates a model, and no number on
 * this card was invented in the browser.
 */
export default function DatasetReadinessCard() {
  const [report, setReport] = useState<DatasetReadiness | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    let active = true;
    void fetchDatasetReadiness()
      .then((next) => {
        if (active) {
          setReport(next);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (active) {
          setReport(null);
          setError(
            caught instanceof ApiError
              ? caught
              : new ApiError("The dataset audit could not be loaded.", { status: 0 }),
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <Card>
      <CardHeader
        title="ML dataset readiness"
        description="The audit behind the training gate: genuine labelled rows are measured outcomes, and the counts below are what the database holds — none of them are forecasts."
        actions={
          report ? (
            isReady(report) ? (
              <Badge tone="active">Ready</Badge>
            ) : (
              <Badge tone="caution">Not ready</Badge>
            )
          ) : null
        }
      />
      <div className="px-5 py-4">
        {error ? (
          <Alert tone="danger" title="Dataset audit unavailable">
            <p>{error.message}</p>
          </Alert>
        ) : report ? (
          <div className="space-y-4">
            <p className="text-[13px] leading-relaxed text-muted">{report.message}</p>
            {report.reasons.length > 0 ? (
              <ul className="space-y-1">
                {report.reasons.map((reason) => (
                  <li key={reason} className="text-[12px] text-caution">
                    — {reason}
                  </li>
                ))}
              </ul>
            ) : null}

            <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
              {headlineFigures(report).map((figure) => (
                <div
                  key={figure.label}
                  className="rounded-lg border border-line bg-inset px-3.5 py-3"
                >
                  <dt className="text-[11px] uppercase tracking-wide text-faint">
                    {figure.label}
                  </dt>
                  <dd className="tabular mt-1 text-xl font-semibold text-ink">
                    {figure.value}
                    <span className="ml-2 text-xs font-normal text-muted">
                      required {figure.required}
                    </span>
                  </dd>
                </div>
              ))}
            </dl>

            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
                  In the database
                </p>
                <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2.5">
                  {countRows().map((row) => (
                    <DataPoint
                      key={row.key}
                      label={row.label}
                      value={report.counts[row.key]}
                      mono
                    />
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
                  Training-gate thresholds
                </p>
                <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2.5">
                  {thresholdRows().map((row) => (
                    <DataPoint
                      key={row.key}
                      label={row.label}
                      value={report.thresholds[row.key]}
                      mono
                    />
                  ))}
                </div>
              </div>
            </div>

            <p className="border-t border-line pt-3 text-[12px] leading-relaxed text-muted">
              {report.note}
            </p>
          </div>
        ) : (
          <p className="text-sm text-muted">Reading the dataset audit…</p>
        )}
      </div>
    </Card>
  );
}