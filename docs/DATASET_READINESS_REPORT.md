# Dataset Readiness Report (T2) — why the training gate stays shut

- Date: 2026-09-30
- Route: `GET /api/ml/readiness`
- Verdict inherited unchanged: **ML NOT READY** (Stage 6), **ML NOT DEPLOYED** (Stage 7).
- This report is the dataset half of that verdict: it states, live from the database,
  how many genuine labelled rows exist and why that number fails the training-gate
  thresholds. It does not fit a model, does not register one, and does not forecast
  when the gate will open.

---

## 1. The two numbers that decide the verdict

| Figure | On file | Required (threshold) | Met? |
|--------|-------:|---------------------:|------|
| Genuine labelled rows | 0 | ≥ 80 | no |
| Distinct pans in labels | 0 | ≥ 3 | no |

A "genuine labelled row" is an outcome that carries a measured actual rainfall and a
measured post-event salinity and is **not** a demonstration of the worked-example kind.
The demo rows on file exist to show a screen, not to carry a learned signal, and
counting them would be exactly the kind of invented sufficiency the gate exists to
stop.

## 2. What the database actually holds (live counts)

| Stored category | Count |
|-----------------|------:|
| Registered salt pans | 4 |
| Stored observations | 6 |
| Stored weather records | 5 |
| Outcomes on file (incl. demos) | 2 |
| Research datasets on file | 2 |
| Registered model versions | 0 |
| Completed experiments | 0 |

## 3. Training-gate thresholds (the contract the audit is measured against)

| Threshold | Requirement |
|-----------|------------:|
| Min. genuine labelled rows | 80 |
| Min. rows per split | 20 |
| Min. classes | 2 |
| Min. rows per class | 5 |
| Min. training rows per class | 3 |
| Min. distinct pans | 3 |

## 4. Verdict message

> `Insufficient labelled data for reliable model training.`

Reasons reported by the audit:
- only **0** genuine labelled row(s) are on file; at least **80** are required;
- the labels belong to **0** distinct pan(s); at least **3** are required.

## 5. What stays closed because of this

- Operational ML prediction (Module 8/10) stays unavailable; every prediction surface
  continues to refuse with a named reason.
- No model is fitted, trained, versioned, evaluated or deployed by this audit.
- The field-validation module (Module 20, T1) is **not** a substitute for labelled
  data: its figures are user-reported and the replay is a physical mass balance, not
  a learned model.

## 6. Evidence and reproducibility

- Endpoint: `curl http://localhost:8000/api/ml/readiness`
- Backend tests: `tests/test_ml_api.py` asserts the demo outcome does **not** count and
  the readiness payload is shaped as documented. Full backend suite: **1111 passed**.
- Frontend: `DatasetReadinessCard` on the Overview page renders only the values the API
  sends; its component tests assert the card never invents a figure and shows "Not ready"
  only when the verdict is `not_ready`. Full frontend suite: **849 passed**.