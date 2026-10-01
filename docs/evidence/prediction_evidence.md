# SaltLens — Prediction Evidence

Actual prediction comparisons that exist in the system on 2026-09-30. **All are demonstration-only,
n = 1 per comparison.** There is no field validation evidence.

## Comparable events

The outcome ledger holds two events; both are `is_demonstration: true`.

| Outcome | Event | Post-event salinity | Comparable? |
|---------|-------|--------------------|-------------|
| `outcome-7fd2fb7015a642fdbe3080542ff81d62` | `smoke-check-2026-09-29` | none recorded | No — no post-event salinity, no rainfall comparison pair |
| `outcome-c20ce3da5dec441eaa5db9cdb55d58dd` | `demo-event-2026-09-29` | 271.2 g/L | Yes |

## The comparable event (`kzn-2026-demo9`)

Pre-event state: salinity 278.5 g/L, depth 18.2 cm. Forecast rainfall 35 mm (`source: demonstration`),
recorded rainfall 32.0 mm. Post-event: salinity 271.2 g/L, depth 21.6 cm.

### Comparison 1 — forecast rainfall vs actual rainfall (mm)

| Predicted | Actual | Signed error |
|-----------|--------|--------------|
| 35.0 | 32.0 | +3.0 |

### Comparison 2 — simulated post-rain salinity from the rainfall that fell, vs actual (g/L)

| Predicted | Actual | Signed error |
|-----------|--------|--------------|
| 236.855 | 271.2 | −34.345 |

MAE = 34.345 g/L, bias = −34.345 g/L (identical because n = 1; a signed underestimate of dilution).

### Comparison 3 — simulated post-rain salinity from the forecast rainfall, vs actual (g/L)

| Predicted | Actual | Signed error |
|-----------|--------|--------------|
| 233.581 | 271.2 | −37.619 |

Reported by the API: `simulation_error.mean_absolute_error = 34.34486`, coverage 50.0 %
(1 comparable / 2 outcomes), `forecast_simulation_error` carries the −37.62 pair.

## What these numbers may and may not mean

- May: demonstrate that the comparison machinery runs end-to-end and computes honest signed errors
  on demo data.
- May **not**: be quoted as any indicator of simulator accuracy for a real pan. n = 1, synthetic
  inputs, identical demonstration source on both sides.

## Machine-readable source of this table

```bash
curl -s 'http://localhost:8000/api/validation/pairs?comparison=simulated_vs_actual_salinity'
curl -s 'http://localhost:8000/api/validation/export?comparison=forecast_vs_actual_rainfall&output_format=csv'
```

## Stage 6 regression metrics (for completeness)

`docs/ML_TRAINING_REPORT.md` reports a Stage 6 demo-trained classifier's held-out metrics — produced
on demo rows, verifies the evaluation machinery only, and is explicitly **not** evidence of field
model skill. This file intentionally does not repeat those numbers; models are `0` and no model was
deployed.