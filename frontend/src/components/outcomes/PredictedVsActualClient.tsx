"use client";

import { useState } from "react";
import PanScopeSelect from "@/components/outcomes/PanScopeSelect";
import PredictedVsActualView from "@/components/outcomes/PredictedVsActualView";

/**
 * The client half of `/predicted-vs-actual`.
 *
 * The page itself is a server component, so the only reason this wrapper exists
 * is the pan selector, which is a `useState` filter. Splitting it this way keeps
 * the view itself free of the scope state and lets it be rendered directly from
 * a pan's page if that is ever wanted.
 */
export default function PredictedVsActualClient() {
  const [panId, setPanId] = useState("");

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 lg:px-8 lg:py-9">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            Module 15 · Actual-outcome recording
          </p>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
            Predicted vs actual
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-muted">
            How far the mass-balance prediction was from what each pan really did.
            Every figure below is measured against a recorded event. Events whose
            measurement is missing are left out of every mean rather than counted
            as zero error.
          </p>
        </div>
        <PanScopeSelect value={panId} onChange={setPanId} />
      </header>

      <div className="mt-6">
        <PredictedVsActualView panId={panId || undefined} />
      </div>
    </div>
  );
}
