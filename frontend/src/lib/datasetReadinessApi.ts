/**
 * HTTP client for the dataset-readiness audit (`GET /api/ml/readiness`).
 *
 * This is the T2 surface: it fetches the audit the ML service computes from the
 * live database — genuine labelled rows, distinct pans, stored counts and the
 * thresholds — and never asks for a fit. A request here cannot start a training
 * run, because the endpoint does not expose one.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type { DatasetReadiness } from "@/lib/datasetReadiness";

export async function fetchDatasetReadiness(): Promise<DatasetReadiness> {
  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}/api/ml/readiness`, {
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Could not reach the SaltLens API at ${getApiBaseUrl()}. Check that the backend is running.`,
      { status: 0 },
    );
  }
  if (!response.ok) {
    throw new ApiError(`Dataset audit failed with status ${response.status}.`, {
      status: response.status,
    });
  }
  return (await response.json()) as DatasetReadiness;
}