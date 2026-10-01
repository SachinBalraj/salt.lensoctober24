# Stage 9 — Field Validation (Module 20) and the Dataset Audit

- Date: 2026-09-30
- Scope: the field-validation module (T1) and the dataset-readiness audit (T2) behind
  the training gate, plus their frontend surfaces, tests and live verification.
- Verdicts from earlier stages are inherited unchanged: **ML NOT READY** (Stage 6),
  **ML NOT DEPLOYED** (Stage 7). Stage 9 adds Module 20 (field validation) as a live
  module and does **not** claim that any reported figure is ML output.

---

## 1. What was delivered

**T1 — Field validation (Module 20).** A rainfall event is filed as a frozen, user-reported
record carrying its pre-event reading, post-event reading, actual rainfall (stored separately
from any forecast) and any interventions. On read, the rainfall-only mass balance
(`rainfall-dilution-mass-balance-v1`, Module 6) is replayed from the stored inputs and the
predicted salinity and depth are drawn beside the reported figures as absolute differences.
CSV and PDF export are served by the backend.

**T1b — Event reconciliation (provenance, milestones, audit history).** The read-only audit
of the North Pan event found three things the stored record could not say, and the module
now says each of them rather than leaving it implied:

- **What the instant marks.** `RainfallEvent.event_milestone` is one of `rainfall_start`,
  `rainfall_end`, `event_logged`, `unknown`, defaults to `unknown`, and is never inferred
  from the rainfall time nearby. The North Pan instant is `2026-03-14T18:00:00Z` =
  `14 March 2026, 23:30 IST`, which is correct; nothing establishes which of those four it
  is, so it stays `unknown`.
- **Where each figure came from.** Each `FieldMeasurement` carries `recorded_timezone`,
  `observer`, `evidence_source` and `timestamp_verified`. A reported instant is retained as
  reported; only a stored record corroborates a time.
- **What changed, and why.** `RainfallEvent.corrections` is append-only. `POST
  /api/field-validation/events/{id}/corrections` records a reconciliation with its reason,
  the value that was there before and the value that is there now. No stored figure is edited
  in place and nothing on this path can set `independently_verified`.

`POST /api/field-validation/events/{id}/pre-event-link` reconciles the pre-event snapshot
against a **stored** observation. It requires the original record's own local date/time, the
zone it was written in, the observer, the evidence source and a reason; it refuses a
different pan, an observation dated after the event's rainfall, a declared local time that
does not resolve to the stored instant, and figures that disagree rather than averaging
them. It never creates or backdates an observation.

Every instant is published twice — UTC and the site's local zone — from the single instant
that is stored, so no client can re-convert a time and shift it twice.

**T2 — Dataset-readiness audit.** `GET /api/ml/readiness` reports the genuine labelled-row
count, the distinct-pan count, all stored counts and the training-gate thresholds. With
0 genuine labelled rows the verdict is `not_ready` and the fixed refusal sentence is used.
See `docs/DATASET_READINESS_REPORT.md`.

## 2. Files changed

**Backend (Module 20 + readiness)**
- `backend/app/models/field_validation.py` — `RainfallEvent`, `FieldMeasurement`,
  `FieldIntervention`, `VerificationState`, `InterventionKind`, `InterventionDirection`,
  plus `EventMilestone` and `CorrectionRecord` for reconciliation.
- `backend/app/db/collections.py`, `backend/app/db/indexes.py`, `backend/app/models/__init__.py`
  — register `rainfall_events` (collection, model, indexes).
- `backend/app/schemas/field_validation.py` — HTTP contract; predicted fields are
  `float | None` and a missing prediction is never rendered as zero.
- `backend/app/services/field_validation.py` — status/list/get/comparison/summary, add
  intervention, `record_correction()`, `link_pre_event_observation()`, UTC/IST rendering,
  CSV + PDF export (fpdf2) carrying the milestone, provenance and audit history.
- `backend/app/api/routers/field_validation.py` + `routers/__init__.py` — prefix
  `/api/field-validation`, tag `20-field-validation`, with
  `POST /events/{event_id}/corrections` and `POST /events/{event_id}/pre-event-link`.
- `backend/app/services/ml.py`, `backend/app/api/routers/ml.py` — `dataset_readiness()` and
  `GET /api/ml/readiness`.
- `backend/tests/test_field_validation.py` (new), `test_ml_api.py`, `test_health.py`,
  `test_indexes.py` (updated). `backend/requirements.txt` — `fpdf2>=2.8`.
- `scripts/seed_field_validation.py` — idempotent North Pan reference event, now explicit
  that its snapshots are report claims with no observation links.
- `scripts/reconcile_field_validation.py` — idempotent, additive, `--dry-run` capable; records
  the audit finding once and checks the protected collection counts before and after.

**Frontend**
- `src/lib/fieldValidation.ts`, `src/lib/fieldValidationApi.ts` — types, shaping, API client.
- `src/lib/datasetReadiness.ts`, `src/lib/datasetReadinessApi.ts` — readiness types/client.
- `src/app/field-validation/page.tsx` + `src/components/fieldValidation/`
  (`FieldValidationClient.tsx`, `FieldValidationChart.tsx`) — the dashboard: status tiles,
  predicted-vs-reported charts, event table with flag badges and caveats, export links,
  report-filing form (including the milestone selector).
- `src/components/fieldValidation/FieldEventProvenance.tsx` (new) — the per-event
  provenance surface opened from a row: instants in both zones with how each stands, the
  append-only audit history, the reconciliation form and the pre-event link form.
- `src/components/DatasetReadinessCard.tsx` mounted on the Overview page (`src/app/page.tsx`).
- `src/components/layout/Sidebar.tsx` (nav entry), `src/components/ModuleMap.tsx` (module 20, live).
- Tests: `src/lib/fieldValidation.test.ts`, `src/lib/fieldValidationApi.test.ts`,
  `src/lib/datasetReadiness.test.ts`, `src/components/fieldValidation/fieldValidation.test.tsx`,
  `src/components/fieldValidation/readinessCard.test.tsx`.

## 3. The North Pan reference event (seeded, live)

Seeded for `pan_orginal` via `backend/.venv/bin/python ../scripts/seed_field_validation.py --pan-id pan_orginal`
(starting brine 250 g/L at 20 cm; actual rainfall 90 mm). Live readings:

| Quantity | Value |
|----------|------:|
| Predicted salinity | 172.414 g/L |
| Reported salinity | 20.0 g/L |
| \|Δ\| salinity | 152.414 g/L |
| Predicted depth | 29.000 cm |
| Reported depth | 50.0 cm |
| \|Δ\| depth | 21.0 cm |
| `intervention_affected` | true |
| `unknown_intervention_volumes` | true |
| `independently_verified` | false |
| `verification_state` | reported |
| `event_milestone` | unknown |
| Mean \|Δ\| salinity (all events) | 152.414 g/L |

Section 4 records what the read-only audit of this event could not establish and what the
additive reconciliation did about it.

The gap between prediction and report is consequence of the recorded brine-transfer-out
intervention whose volume was never recorded, and the UI flags exactly that: the rainfall-only
model is not expected to match, the unrecorded volume cannot be estimated, and the figures were
never independently verified. The replay is a physical balance, not an ML prediction.

## 4. Live reconciliation of the North Pan event

Run additively via `backend/.venv/bin/python ../scripts/reconcile_field_validation.py`
(`--dry-run` first). The event was **not** modified: its figures, its flags and every
protected collection are byte-for-byte what they were. What changed is that the record now
says what it cannot say.

| Before (implied) | Now (stated) |
|---|---|
| the instant was just a timestamp | `event_milestone: unknown` — no document establishes whether `23:30 IST` is the rain's start, its end, or when the report was logged |
| the pre-event snapshot had no stated origin | `pre_event.observation_id: null`, `timestamp_verified: false`, and a caveat that the starting brine is reported-only |
| the post-event instant sat beside the event | `post_event.timestamp_verified: false`, retained as reported, not corroborated |
| nothing recorded that the record was examined | `corrections: 1` — `event_milestone` `unknown -> unknown` by `provenance audit`, with the finding in its reason |

Live state confirmed after the run:

```
event_id                 ev-249b575de8234ff09b341e525ef5d4a1
event_milestone          unknown
site_timezone_label      IST (UTC+05:30)
event_timestamp_utc      2026-03-14 18:00:00 UTC
event_timestamp_ist      2026-03-14 23:30:00 IST
pre_event_observation_id None
post_event_timestamp_verified False
correction_count         1
verification_state       reported
```

**The September observation was refused, live.** `obs-8d0efe4005c04caeb30adcae65647dea`
(30 Sep 2026, `250 g/L` at `20 cm`) is the only stored reading of this pan with those
figures, and it is dated after the event:

> Observation obs-8d0efe4005c04caeb30adcae65647dea is dated 2026-09-30 09:10:27 UTC
> (2026-09-30 14:40:27 IST), which is after this event's rainfall at 2026-03-14 14:00:00 UTC
> (2026-03-14 19:30:00 IST). A pre-event reading cannot postdate the rain it starts from, so
> it cannot be this event's starting brine. Nothing was linked.

Protected collections were untouched: 42 simulation runs, 39 recommendations, 11 operator
decisions, 1 observation. The named September evidence
(`run-78839794570a407cab9fc511595243b3`, `run-3cb1a300946e4bd4ab978f17f4cf9bf4`,
`rec-56c91da13cd147128b3d92755d445545`, `rec-fdccf49b4fc2471da15a5f95b1b2a39f`,
`decision-4b099249f7284361b0c5e854463cb0e4`) is preserved.

Re-running the script reports "already recorded … skipping" — the audit finding cannot be
appended twice.

## 5. Live verification

- `GET /api/field-validation/status` → `event_count: 1`, `ready: true`.
- `GET /api/field-validation/summary` → `compared_events: 1`, mean diffs as above, caveats present.
- `GET /api/field-validation/export?format=csv` → `text/csv`, 1 308 bytes, 37 columns including
  `event_milestone`, both zones for all four instants, both verification flags and
  `correction_count`.
- `GET /api/field-validation/export?format=pdf` → `application/pdf`, 3 269 bytes; text carries
  `2026-03-14 18:00:00 UTC`, `2026-03-14 23:30:00 IST`, `milestone: unknown`, the milestone
  and uncorroboration caveats, and `audit history (1 entry)` with the reason.
- `POST /api/field-validation/events/{id}/pre-event-link` with the September observation →
  `409` with the chronology refusal quoted above.
- Seed re-run → idempotent ("already seeded … skipping"), never duplicates the historical record.
- `GET /api/ml/readiness` → `not_ready`, 0 genuine rows, reasons + thresholds.
- `http://localhost:3000/field-validation` → 200, renders (SSR initial state verified).
- `http://localhost:3000/` → 200, renders "ML dataset readiness" card and Module 20 in the map.

## 6. Test counts

- Backend: **1219 passed**, `ruff check app tests` clean; `ruff check` on both scripts clean
  after removing unused `noqa: E402` directives. `tests/test_field_validation.py`: **128 passed**.
- Frontend: **871 passed** across 44 files, `tsc --noEmit` and `eslint` clean.
- New coverage from the rainfall-instant resolution: the generic correction route refusing
  `rainfall_timestamp` and naming the resolution route; a filed instant defaulting to
  unsourced and being unable to claim `record_backed`; the resolution moving the instant in
  both zones while leaving the displaced one superseded with `meaning: unknown` and no
  supporting record; the correction entry landing in the append-only history; the instant
  staying `operator_reported` and unobserved; re-posting being a no-op; a conflict closing
  only when the adopted instant is the one it was raised about, leaving the event blocked
  when a second, unrelated disagreement remains; the preserved pre-event link, milestone,
  rainfall figures and unknown transfer volume surviving the correction; a local clock
  reading resolved server-side with its unreadable, absent and self-contradicting zones all
  refused; and the CSV, PDF, caveat and comparison surfaces labelling `19:30 IST` as
  superseded history rather than as a measurement. On the frontend, the superseded block's
  wording and the instants table's "not a measured reading".

## 7. Outstanding limitations (stated, not hidden)

- Every field figure is user-reported; nothing is independently verified by the system.
- The balance models rainfall as a volume addition and does not model evaporation, seepage
  or brine transfer; an event with an intervention is flagged, not adjusted.
- The mean \|Δ\| figures are computed only over events whose prediction and report both exist.
- **The North Pan rainfall instant is resolved, but the rest of its evidence is not.** The
  disagreement about when the rain fell has been settled on evidence: the seed's hardcoded
  `19:30 IST` literal was unsupported by any record and has been superseded, and the
  notebook's `23:30 IST` is now the instant in force. The event therefore reports
  `reconciliation_state: complete`, and the earlier value is retained as superseded history
  with its meaning left `unknown`. What that resolution deliberately does **not** do is
  upgrade the evidence: the instant is `operator_reported`, `rainfall_timestamp_observed`
  is `false`, and `independently_verified` stays `false`, because a document that records a
  time does not measure one. These gaps remain, and the module refuses to guess them:
  - the original rain-gauge record for `19:30 IST` — it does not exist, and nothing backs
    the seed's "manual rain gauge" label;
  - what the `19:30 IST` instant was meant to mark — unknown, and recorded as such;
  - the original pre-event collection date/time and offset;
  - the original field log, gauge export or lab sheet;
  - observer identity or device identity;
  - corroboration for the post-event instant;
  - the brine-transfer volume.
  Nothing was backdated, and the superseded instant is never rendered or exported as a
  measurement.
- ML training stays gated: 0 genuine labelled rows, so operational ML prediction remains
  unavailable and no model is fitted or deployed by this stage.
- The browser verification above was performed by SSR/HTTP and component tests; the
  interactive data load for the field-validation page runs client-side after mount.