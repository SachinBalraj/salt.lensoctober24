"use client";

import { useEffect, useState } from "react";
import type { SaltPanListResponse } from "@/lib/pans";
import { listPans } from "@/lib/pansApi";

/**
 * The pan selector for the predicted-against-actual view.
 *
 * Kept separate from the view so the view itself takes a plain `panId` and can
 * be rendered straight from a server component. The pan list is read from the
 * registry; if it cannot be reached the selector simply offers "All pans", which
 * is the honest state rather than a fabricated list of choices.
 */
export default function PanScopeSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (panId: string) => void;
}) {
  const [pans, setPans] = useState<SaltPanListResponse["items"]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await listPans({ pan_status: "active", limit: 200 });
        if (cancelled) return;
        setPans(response.items);
        setFailed(false);
      } catch {
        // Not fatal: the comparison itself does not depend on this list.
        if (cancelled) return;
        setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-w-[14rem]">
      <label
        htmlFor="outcome-pan-scope"
        className="mb-1.5 block text-[13px] font-medium text-ink-soft"
      >
        Scope
      </label>
      <select
        id="outcome-pan-scope"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink"
      >
        <option value="">All pans</option>
        {pans.map((pan) => (
          <option key={pan.pan_id} value={pan.pan_id}>
            {pan.pan_name} ({pan.pan_id})
          </option>
        ))}
      </select>
      {failed ? (
        <p className="mt-1 text-[11px] text-muted">
          The pan registry is unavailable, so only “All pans” can be selected.
        </p>
      ) : null}
    </div>
  );
}
