"use client";

import { useEffect, useState } from "react";
import { fetchHealth, type BackendHealth } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  ok: "bg-active-soft text-active border-active-line",
  error: "bg-danger-soft text-danger border-danger-line",
  unknown: "bg-caution-soft text-caution border-caution-line",
};

const DB_STYLES: Record<string, string> = {
  connected: "bg-active-soft text-active border-active-line",
  not_configured: "bg-caution-soft text-caution border-caution-line",
  error: "bg-danger-soft text-danger border-danger-line",
  unknown: "bg-inset text-ink-soft border-line-strong",
};

function badge(label: string, style?: string) {
  return (
    <span
      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${style ?? ""}`}
    >
      {label}
    </span>
  );
}

export default function HealthStatus() {
  const [health, setHealth] = useState<BackendHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const h = await fetchHealth();
        if (active) {
          setHealth(h);
          setError(null);
        }
      } catch (e) {
        if (active) {
          setHealth(null);
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    }
    void poll();
    const id = window.setInterval(() => void poll(), 10_000);
    return () => {
      active = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <section className="rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="border-b border-line px-5 py-4">
        <h2 className="text-sm font-semibold text-ink">Backend connectivity</h2>
      </div>
      <div className="p-5">
        {error ? (
          <div className="space-y-2">
            <p className="text-sm font-medium text-ink">Backend unreachable</p>
            {badge("api: down", STATUS_STYLES.error)}
            <p className="text-xs text-muted">{error}</p>
          </div>
        ) : health ? (
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-faint">Status</dt>
              <dd className="mt-1.5">{badge(health.status, STATUS_STYLES[health.status])}</dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-faint">API version</dt>
              <dd className="tabular mt-1.5 text-sm text-ink">{health.version}</dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-faint">Environment</dt>
              <dd className="mt-1.5 text-sm text-ink">{health.environment}</dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-faint">Database</dt>
              <dd className="mt-1.5">{badge(health.database, DB_STYLES[health.database])}</dd>
            </div>
            <div className="col-span-2 sm:col-span-4">
              {health.database === "not_configured" ? (
                <p className="text-xs leading-relaxed text-muted">
                  MongoDB is not configured — the API runs but the salt-pan
                  registry returns 503 rather than showing sample rows. Set{" "}
                  <code className="rounded bg-inset px-1 font-mono">
                    MONGODB_URI
                  </code>{" "}
                  in backend/.env.
                </p>
              ) : (
                <p className="text-xs text-muted">MongoDB connection: {health.database}</p>
              )}
            </div>
          </dl>
        ) : (
          <p className="text-sm text-muted">Connecting to backend…</p>
        )}
      </div>
    </section>
  );
}
