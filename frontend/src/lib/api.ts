export function getApiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
}

export type BackendHealth = {
  status: string;
  app: string;
  version: string;
  environment: string;
  database: string;
  python: string;
};

/**
 * A failure the operator can act on.
 *
 * `fields` carries per-field messages when the backend rejected a specific
 * value, so a form can attach the message to the right input instead of
 * showing one opaque banner.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly fields: Record<string, string>;

  constructor(
    message: string,
    {
      status,
      code = null,
      fields = {},
    }: { status: number; code?: string | null; fields?: Record<string, string> },
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }

  /** True when the registry is unreachable rather than rejecting the input. */
  get isConnectivity(): boolean {
    return this.status === 0 || this.status === 503 || this.status === 502 || this.status === 504;
  }
}

/**
 * Whether asking the same question again could give a different answer.
 *
 * One rule for every surface that shows a failed request, because the two obvious
 * answers are both wrong somewhere. Offering a retry for a refusal -- no model
 * deployed, no class probabilities, an artifact not on this host -- invites an
 * operator to press a button whose answer cannot change, and implies the
 * application is broken rather than that a deployment decision is outstanding.
 * Withholding it for a connectivity failure does the same in the other direction:
 * the request never reached the server, so nothing about it has been decided yet,
 * and it is the one case where pressing again genuinely might work.
 *
 * So: a transport failure or a server-side error is retryable, and a refusal about
 * the state of the system is not.
 */
export function canRetry(error: ApiError): boolean {
  return error.isConnectivity || error.status >= 500;
}

export async function fetchHealth(): Promise<BackendHealth> {
  const res = await fetch(`${getApiBaseUrl()}/api/health`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`Backend health request failed: ${res.status}`);
  }
  return res.json() as Promise<BackendHealth>;
}
