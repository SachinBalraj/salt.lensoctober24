/**
 * DEMO DATA — NOT RESEARCH DATA.
 *
 * These records exist only so the salt-pan screens can be reviewed with
 * something on screen. They are fabricated for layout purposes and must never
 * be treated as a measurement, a site record or an input to any model.
 *
 * Safeguards, enforced by `demoPans.test.ts`:
 *
 * 1. The set is empty unless `NEXT_PUBLIC_SALTLENS_DEMO_PANS=true` is set, so
 *    a normal build can never show it.
 * 2. Every identifier is prefixed `DEMO-` so a demo row is obvious in any
 *    screenshot or export.
 * 3. Every record carries `id: null` and is tagged with `origin: "demo"`, which
 *    is a different type from a registry record returned by the API.
 * 4. The forms never read from this module, and `pansApi.ts` has no way to
 *    submit a demo record, so demo data cannot reach the database.
 *
 * Development use:
 *
 * ```bash
 * NEXT_PUBLIC_SALTLENS_DEMO_PANS=true npm run dev
 * ```
 */

import type { SaltPan } from "@/lib/pans";

/** Distinguishes a real registry record from a demo record at the type level. */
export type PanOrigin = "registry" | "demo";

export type PanView = {
  origin: PanOrigin;
  pan: SaltPan;
};

export const DEMO_DATA_FLAG = "NEXT_PUBLIC_SALTLENS_DEMO_PANS";

export const DEMO_DATA_NOTICE =
  "DEMO DATA — fabricated for layout review only. Not a measurement, not a site record, and never written to the database.";

/**
 * Next.js inlines `NEXT_PUBLIC_*` only when it is read as a static member
 * expression, so the flag is read as a literal rather than via a computed key.
 */
export function isDemoDataEnabled(): boolean {
  return process.env.NEXT_PUBLIC_SALTLENS_DEMO_PANS === "true";
}

/** Fixed timestamps keep the preview deterministic across reloads. */
const DEMO_CREATED_AT = "2026-01-05T08:00:00Z";
const DEMO_UPDATED_AT = "2026-01-05T08:00:00Z";

const DEMO_PANS: readonly SaltPan[] = [
  {
    id: null,
    pan_id: "DEMO-a01",
    pan_name: "Demo evaporation pan A",
    location: "Demo site — coastal reach 1",
    latitude: -30.7444,
    longitude: 30.8071,
    area_m2: 12_500,
    normal_depth_cm: 15.5,
    target_harvest_salinity_g_l: 285,
    is_active: true,
    created_at: DEMO_CREATED_AT,
    updated_at: DEMO_UPDATED_AT,
    deactivated_at: null,
    farmer_name: null,
    farmer_whatsapp_masked: null,
    latest_observation: null,
  },
  {
    id: null,
    pan_id: "DEMO-b02",
    pan_name: "Demo concentration pan B",
    location: "Demo site — coastal reach 2",
    latitude: null,
    longitude: null,
    area_m2: 8_200,
    normal_depth_cm: 12,
    target_harvest_salinity_g_l: 310,
    is_active: true,
    created_at: DEMO_CREATED_AT,
    updated_at: DEMO_UPDATED_AT,
    deactivated_at: null,
    farmer_name: null,
    farmer_whatsapp_masked: null,
    latest_observation: null,
  },
  {
    id: null,
    pan_id: "DEMO-c03",
    pan_name: "Demo crystalliser pan C (deactivated)",
    location: "Demo site — inland block",
    latitude: null,
    longitude: null,
    area_m2: 4_600,
    normal_depth_cm: 9.5,
    target_harvest_salinity_g_l: 340,
    is_active: false,
    created_at: DEMO_CREATED_AT,
    updated_at: DEMO_UPDATED_AT,
    deactivated_at: "2026-02-11T10:30:00Z",
    farmer_name: null,
    farmer_whatsapp_masked: null,
    latest_observation: null,
  },
];

/** Demo records for the current session, or an empty list when disabled. */
export function getDemoPans(): PanView[] {
  if (!isDemoDataEnabled()) return [];
  return DEMO_PANS.map((pan) => ({ origin: "demo" as const, pan }));
}

/** Wraps a real registry record so the UI can branch on `origin` uniformly. */
export function asRegistryView(pan: SaltPan): PanView {
  return { origin: "registry", pan };
}
