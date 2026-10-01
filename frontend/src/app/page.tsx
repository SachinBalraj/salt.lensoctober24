import Link from "next/link";
import DatasetReadinessCard from "@/components/DatasetReadinessCard";
import HealthStatus from "@/components/HealthStatus";
import ModuleMap from "@/components/ModuleMap";
import SimulatedChartPlaceholder from "@/components/SimulatedChartPlaceholder";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { getApiBaseUrl } from "@/lib/api";

export default function Home() {
  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 lg:px-8 lg:py-9">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Research prototype · foundation stage
        </p>
        <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
          SaltLens DSS
        </h1>
        <p className="mt-1.5 max-w-2xl text-sm text-muted">
          An AI-driven digital twin decision support system for climate-resilient
          salt-pan management. This shell verifies that the frontend, FastAPI
          backend and MongoDB layer are wired together. No research results are
          fabricated anywhere in this system.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted">
          <span>
            Backend API{" "}
            <code className="rounded bg-surface px-1 py-0.5 font-mono text-[11px] text-ink-soft">
              {getApiBaseUrl()}
            </code>
          </span>
          <span className="text-faint">|</span>
          <span>OpenAPI at <code className="font-mono">/docs</code></span>
        </div>
      </header>

      <Card className="mt-6">
        <CardHeader
          title="Module 02 · Salt pans"
          description="Live: register, inspect, edit and deactivate production units."
          actions={
            <Link
              href="/pans"
              className="inline-flex items-center gap-1.5 rounded-lg border border-accent bg-accent px-3.5 py-2 text-[13px] font-medium text-white transition-colors hover:bg-accent-hover"
            >
              Open pan registry
            </Link>
          }
        />
        <p className="px-5 py-4 text-[13px] leading-relaxed text-muted">
          The salt-pan registry is the first module to store real operator data.
          Area, normal depth, target harvest salinity and coordinates are entered
          by an operator and validated in the browser and in the API; nothing is
          inferred or filled in from a simulation.
        </p>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <HealthStatus />
        <SimulatedChartPlaceholder />
      </div>

      <div className="mt-4">
        <DatasetReadinessCard />
      </div>

      <div className="mt-4">
        <ModuleMap />
      </div>
    </div>
  );
}
