# SaltLens — Practical Field-Data Collection Sheet

Purpose: capture **genuine salt-pan measurements** in the form the SaltLens pipeline accepts. This
sheet is intentionally blank; every field maps to the API write schema so a completed sheet can be
entered directly (or via `data/templates/*_blank.csv` → import). When data is entered into SaltLens it
must use `data_source: MANUAL` (the GENUINE track), **never** `demonstration`.

Each page is a table to be filled by hand. Timestamps are UTC `YYYY-MM-DDTHH:MM:SSZ`.

---

## Page 1 — Campaign provenance cover (one per campaign; travels with every record)

| Field | Value |
|-------|-------|
| Campaign id (e.g. `campaign-2026-01-kzn`) | |
| Site / pan(s) covered | |
| Region / latitude / longitude | |
| Campaign start / end | |
| Field supervisor | |
| Data collectors (names + role) | |
| Instruments used (make/model/serial) | |
| Calibration date + standard | |
| Which pages/rows filled on this campaign | |
| Signature / date | |

> The integrity gate reads provenance, not the numbers. An unverifiable sheet cannot be imported as
> genuine data even if the numbers look plausible.

---

## Page 2 — Pan registration (Module 02, one row per pan)

| `pan_id` | `pan_name` | `location` | `latitude` | `longitude` | `area_m2` | `normal_depth_cm` | `target_harvest_salinity_g_l` | `flood_depth_cm` | `dilution_salinity_g_l` | `critical_rainfall_24h_mm` | `target_flow_cm` |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `kzn-2026-01` | | | | | | | | | | | |

`latitude`/`longitude` optional WGS84 decimal degrees. Threshold columns map to `risk_thresholds`.

---

## Page 3 — Observation log (Module 03, one row per reading — brine is the core measurement)

Sample a minimum of three points per pan per visit (inlet / mid-pan / outlet), record each row.

| `timestamp` UTC | pan_id | `water_depth_cm` | `salinity_g_l` | Method* | `brine_temperature_c` | `ambient_temperature_c` | `humidity_percent` | Collector | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 2026-01-...T..:..:..Z | | | | | | | | | |
| | | | | | | | | | |

_*Method codes: `REFRACTO` (optical refractometer, Δ g/L), `HYDROM` (hydrometer + temp correction),
`COND` (conductivity converted to g/L with calibration factor). Record the instrument + factor in the
notes column on first use._

Rules that make a reading genuine:
- Equipment calibrated same day (Page 4) and noted.
- Photograph each reading + pan condition (link in notes).
- No blanks for depth or salinity on the same row (partial rows reduce comparability — see
  `prediction_evidence.md`).

---

## Page 4 — Instrument & calibration log (module 03 provenance)

| Date UTC | Instrument | Standard / reference value | Meter reading | Correction | By whom | Signature |
|---|---|---|---|---|---|---|
| | | | | | | |

---

## Page 5 — Weather log (Module 04, manual daily record)

| `timestamp` UTC | pan_id | `forecast_for` (UTC) | `rainfall_mm` | `rainfall_probability_percent` | `ambient_temperature_c` | `humidity_percent` | Source comment (NWP site / gauge) | By whom |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

Use `source: MANUAL`. If a local rain gauge exists, that is the **actual** rainfall and belongs on
Page 6.

---

## Page 6 — Rainfall event / outcome log (Module 15, one row per event)

| `event_id` | pan_id | `event_date` UTC | `pre_event_salinity_g_l` | `post_event_salinity_g_l` | `pre_event_depth_cm` | `post_event_depth_cm` | `actual_rainfall_mm` | `forecast_rainfall_mm` | `operator_action`* | `harvest_outcome`** | By whom |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | |

_*Action vocabulary (must match Module 12/14): `HARVEST NOW`, `TRANSFER BRINE`, `PROTECT / DRAIN` or
`WAIT AND RECHECK`. **`harvest_outcome`: e.g. `harvested_ok`, `harvest_delayed`, `stock_lost`,
`none`.)_

Post-event salinity must be recorded within a defined window (note the delay) — without it the event
is `not_comparable` in validation, exactly as the support system reports.

---

## CSV templates (same columns, machine-readable; already in the repository)

- `data/templates/pans.csv` → Page 2
- `data/templates/observations.csv` → Page 3
- `data/templates/weather.csv` → Page 5
- `data/templates/outcomes.csv` → Page 6

Headers are the canonical API field names, and the campaign provenance lives in
`data/templates/README.md` (the per-campaign provenance record the evidence gate reads). Optional
columns may be left empty but the file must keep the header row.