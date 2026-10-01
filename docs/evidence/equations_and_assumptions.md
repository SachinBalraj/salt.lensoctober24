# SaltLens — Equations and Assumptions

All equations reproduced verbatim from the source so a reader can check them by hand.

## 1. Rainfall / brine mass balance (Module 6, `app/services/mass_balance.py`)

The simulator is **arithmetic, not a fitted model** — no parameter was learned from data.

```
V0  = A × D0                            initial brine volume          (m³)
Vr  = A × R                             rainfall volume               (m³)
V1  = V0 + Vr                           post-rain volume              (m³)
C1  = C0 × V0 / V1                      post-rain salinity            (g/L)
dilution % = ((C0 − C1) / C0) × 100
```

Conservation check surfaced in every response:

```
initial salt mass  = C0 × V0 × 1000 / 1000   (g)
post-rain salt mass = C1 × V1 × 1000 / 1000  (g)
salt_mass_conserved ⇔ |post − initial| ≤ 1e-9 × max(1, |initial|)
```

### Stated assumptions (returned with every response as `assumptions`)

- Rainfall is a **single instantaneous depth**; the model does not resolve when it falls, only how
  much total water is added.
- The pan fills uniformly — area × depth is the whole volume.
- Solute is fully mixed; salinity is homogeneous after the event.
- Area, initial depth, initial salinity and rainfall depth are exact inputs.
- Precipitation adds pure water of zero salinity; evaporation between observation and event is ignored.
- Densities are treated as if water (1 kg/L); the relation `C1 = C0 × V0/V1` is a volume-weighted
  dilution, which is the standard first-order model for brine dilution by rainfall.

The disclaimer ships with every result: *a number from this arithmetic must never be read as a
forecast of what the weather will do.*

### Verified worked example (the Stage 8 demonstration, pan `kzn-2026-demo9`)

Inputs: `A = 12000 m²`, `D0 = 0.182 m` (18.2 cm), `C0 = 278.5 g/L`, `R = 35 mm = 0.035 m`.

```
V0  = 12000 × 0.182            = 2184 m³
Vr  = 12000 × 0.035            = 420 m³
V1  = 2184 + 420               = 2604 m³
C1  = 278.5 × 2184 / 2604      = 233.5806… g/L        ✓ matches API (post_rain_salinity 233.58)
dilution % = (278.5 − 233.58)/278.5 × 100 = 16.12 %
```

## 2. Risk criteria (Module 10)

Risk is judged by **versioned criteria set by a researcher**, not by a learned model
(`app/services/risk.py`). For example the demonstration pan carries thresholds:

| Variable | Threshold |
|----------|-----------|
| Flood depth | 50 cm |
| Dilution salinity | 260 g/L |
| Critical rainfall (24 h) | 90 mm |
| Target flow | 1.5 cm |

A risk class is derived by comparing recorded/predicted variables to these thresholds, and the
derived class distribution is reported so the researcher can see what the criteria imply.

## 3. Decision logic (Module 12)

Currently `basis: physics_and_rules`: the four-action vocabulary
`HARVEST NOW | TRANSFER BRINE | PROTECT / DRAIN | WAIT AND RECHECK` is selected from the twin state
against the versioned rule, whose hash (`sha256: 06a32ee5…`) travels with every recommendation.
`ml_result` is `null` until a deployed model exists.

## 4. Measurement relation (physics-derived provenance)

Where a lab instrument reports conductivity and the pipeline must derive salinity, the Stage 5
normalisation used a ~1.25 g/cm³ brine equivalence and marks rows as `PHYSICS_DERIVED` provenance by
design — never as directly measured. Details in `docs/REAL_DATA_ACQUISITION_REPORT.md`.