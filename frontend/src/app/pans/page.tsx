"use client";

import { useEffect, useMemo, useState } from "react";
import PanTable from "@/components/pans/PanTable";
import { Button, ButtonLink } from "@/components/ui/Controls";
import { Alert, EmptyState, LoadingRows } from "@/components/ui/Feedback";
import { Card, CardHeader, StatTile } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  DEMO_DATA_FLAG,
  DEMO_DATA_NOTICE,
  asRegistryView,
  getDemoPans,
  type PanView,
} from "@/lib/demoPans";
import type { PanStatusFilter } from "@/lib/pans";
import { listPans } from "@/lib/pansApi";

const PAGE_SIZE = 25;

const STATUS_OPTIONS: Array<{ value: PanStatusFilter; label: string }> = [
  { value: "active", label: "Active" },
  { value: "inactive", label: "Deactivated" },
  { value: "all", label: "All" },
];

interface RegistryState {
  key: string;
  views: PanView[];
  counts: { total: number; active: number; inactive: number };
  error: ApiError | null;
}

const EMPTY_COUNTS = { total: 0, active: 0, inactive: 0 };

export default function PanRegistryPage() {
  const [status, setStatus] = useState<PanStatusFilter>("active");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<RegistryState | null>(null);

  const requestKey = `${status}|${search}|${page}|${reloadToken}`;

  // Demo records are appended only when the developer flag is set, and are
  // never sent to the API.
  const demoViews = useMemo(() => getDemoPans(), []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await listPans({
          pan_status: status,
          search: search || undefined,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        });
        if (cancelled) return;
        setState({
          key: requestKey,
          views: response.items.map(asRegistryView),
          counts: {
            total: response.total,
            active: response.total_active,
            inactive: response.total_inactive,
          },
          error: null,
        });
      } catch (caught) {
        if (cancelled) return;
        setState({
          key: requestKey,
          views: [],
          counts: EMPTY_COUNTS,
          error:
            caught instanceof ApiError
              ? caught
              : new ApiError("Unexpected error loading the registry.", {
                  status: -1,
                }),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, page, search, status]);

  const views = state?.views ?? [];
  const counts = state?.counts ?? EMPTY_COUNTS;
  const error = state?.error ?? null;
  const loading = state?.key !== requestKey;
  const refresh = () => setReloadToken((token) => token + 1);

  const totalPages = Math.max(1, Math.ceil(counts.total / PAGE_SIZE));
  const isEmpty = !loading && !error && views.length === 0;

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 lg:px-8 lg:py-9">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            Module 02 · Salt-pan registration
          </p>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
            Pan registry
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-muted">
            Production units registered by an operator. Area, normal depth and
            target harvest salinity are the operator-declared design values that
            later modules use as their starting state.
          </p>
        </div>
        <ButtonLink href="/pans/new" variant="primary">
          Register new pan
        </ButtonLink>
      </header>

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Matching" value={counts.total} />
        <StatTile label="Active" value={counts.active} />
        <StatTile label="Deactivated" value={counts.inactive} />
        <StatTile
          label="Records"
          value={counts.active + counts.inactive}
          hint="All registered pans"
        />
      </div>

      <Card className="mt-5">
        <CardHeader
          title="Registered pans"
          description="Deactivating a pan keeps its record and everything that references it."
        />
        <div className="flex flex-wrap items-end gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-[13rem] flex-1">
            <label
              htmlFor="pan-search"
              className="mb-1.5 block text-[13px] font-medium text-ink-soft"
            >
              Search
            </label>
            <input
              id="pan-search"
              type="search"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Pan ID, name or location"
              className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-faint"
            />
          </div>
          <div
            role="group"
            aria-label="Filter by lifecycle status"
            className="flex overflow-hidden rounded-lg border border-line-strong"
          >
            {STATUS_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  setStatus(option.value);
                  setPage(0);
                }}
                aria-pressed={status === option.value}
                className={`px-3 py-2 text-[13px] font-medium transition-colors ${
                  status === option.value
                    ? "bg-accent text-white"
                    : "bg-surface text-ink-soft hover:bg-inset"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <Button variant="ghost" onClick={refresh} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        </div>

        {error ? (
          <div className="p-5">
            <Alert
              tone={error.isConnectivity ? "warning" : "danger"}
              title={
                error.isConnectivity
                  ? "The registry is unavailable"
                  : "The registry could not be loaded"
              }
              action={
                <Button variant="secondary" onClick={refresh}>
                  Try again
                </Button>
              }
            >
              <p>{error.message}</p>
              {error.isConnectivity ? (
                <p className="mt-1 text-xs">
                  Salt-pan records are only ever read from the API. Without a
                  reachable backend the list is empty rather than filled with
                  sample values.
                </p>
              ) : null}
            </Alert>
          </div>
        ) : loading ? (
          <LoadingRows rows={5} />
        ) : isEmpty ? (
          <EmptyState
            title={search || status !== "all" ? "No pans match this view" : "No pans registered yet"}
            description={
              search
                ? `Nothing matches “${search}”. Clear the search or widen the status filter.`
                : status === "inactive"
                  ? "No pan has been deactivated. Deactivated pans keep their record and appear here."
                  : "Register the first salt pan to start building the registry. Every value you enter is operator-declared, not measured."
            }
            action={
              search || status !== "all" ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSearchInput("");
                    setStatus("all");
                  }}
                >
                  Clear filters
                </Button>
              ) : (
                <ButtonLink href="/pans/new" variant="primary">
                  Register new pan
                </ButtonLink>
              )
            }
          />
        ) : (
          <>
            <PanTable
              views={views}
              hrefFor={(panId) => `/pans/${encodeURIComponent(panId)}`}
            />
            {totalPages > 1 ? (
              <div className="flex items-center justify-between border-t border-line px-5 py-3">
                <p className="tabular text-xs text-muted">
                  Page {page + 1} of {totalPages} · {counts.total} matching
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => setPage((value) => Math.max(0, value - 1))}
                    disabled={page === 0}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() =>
                      setPage((value) => Math.min(totalPages - 1, value + 1))
                    }
                    disabled={page >= totalPages - 1}
                  >
                    Next
                  </Button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </Card>

      {demoViews.length > 0 ? (
        <DemoSection views={demoViews} />
      ) : null}
    </div>
  );
}

function DemoSection({ views }: { views: PanView[] }) {
  return (
    <Card className="mt-5 border-dashed border-demo-line">
      <CardHeader
        title="Demo records — not research data"
        description="Fabricated for layout review. Never written to the database."
        actions={
          <span className="rounded-full bg-demo-soft px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-demo">
            Demo data
          </span>
        }
      />
      <div className="px-5 py-3">
        <Alert tone="demo" title="Demo data">
          <p>{DEMO_DATA_NOTICE}</p>
          <p className="mt-1">
            These records exist only in your browser session. The register, edit
            and deactivate actions above never touch them.
          </p>
        </Alert>
      </div>
      <PanTable
        views={views}
        hrefFor={(panId) => `/pans/${encodeURIComponent(panId)}?demo=1`}
      />
      <p className="border-t border-line px-5 py-3 text-[11px] text-muted">
        Turn this section off by unsetting{" "}
        <code className="rounded bg-inset px-1 font-mono">{DEMO_DATA_FLAG}</code>.
      </p>
    </Card>
  );
}
