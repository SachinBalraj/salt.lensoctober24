# SaltLens DSS — 5-Minute Demonstration Script

## Purpose

A timed, click-by-click demonstration of the working SaltLens DSS, for reviewers and the research
record. It shows the **real running application** (browser at `:3000`, FastAPI at `:8000`, MongoDB
Atlas) and the system's defining property: it demonstrates a complete operator loop while the ML and
field-data layers — which have no genuine data yet — **refuse honestly instead of inventing numbers**.

Everything shown is either verified software functionality or an explicitly labelled demonstration
value. Nothing on screen is a real measurement.

## Before starting (2 minutes of prep — not part of the 5)

- Start servers if not already up: `scripts/dev.sh` (backend), then start the frontend. Verify:
  - `curl -s http://localhost:8000/api/health` → `"database":"connected"`, `database_health.indexes_ready: true`
  - `curl -s http://localhost:8000/api/pans/` lists `kzn-2026-demo9` ("Demo Pan Delta-9")
  - `http://localhost:3000/dashboard` loads
- Close all extra tabs. Maximise the browser. Mute OS notifications.

## The script (0:00 → 5:00)

### 0:00 – 0:30 · Home, health, honesty contract

**Do:** open `http://localhost:3000/`.
**Say:** "This is the SaltLens home. Three things on screen are deliberate: the health card shows the
database connected with indexes ready; the module map shows what's built; and this banner states the
contract — *'No research results are fabricated anywhere in this system.'*"

**Capture:** home page, health card, module map.

### 0:30 – 1:15 · Pan registry (Module 02)

**Do:** click **Open pan registry** (`/pans`). Click **"Demo Pan Delta-9"** → detail page.
**Say:** "The registry stores real operator-entered pan data — area, normal depth, target harvest
salinity. Here is the demonstration pan. Its location field literally reads *'Demonstration site
(worked example, not a field site)'* — every value we show from now on carries that label."

**Point out on screen:** the pan's area `12000 m²`, normal depth `16 cm`, target harvest salinity
`290 g/L`, and its risk thresholds.

**Capture:** pans list, pan detail.

### 1:15 – 2:05 · Dashboard evidence + simulation (Modules 03, 04, 05, 06)

**Do:** open `http://localhost:3000/dashboard`. Select "Demo Pan Delta-9" in the pan selector.
**Say:** "The dashboard reads stored records only. Three evidence cards show the recorded condition,
the weather forecast, and the assembled digital twin. The simulation below is **pure arithmetic** —
volume and salt conservation — and the curve marks the forecast rainfall depth."

**Point out:** condition salinity `278.5 g/L`, depth `18.2 cm`; forecast `35 mm`; twin state
`provenance: observed`; the demonstration simulation card (mass balance → post-rain `233.58 g/L`).

**Capture:** the full dashboard evidence row + simulation card.

### 2:05 – 2:50 · The recommendation (Module 12) — rule + physics

**Do:** on the dashboard, in the **Recommendation card** click **Analyse**.
**Say:** "Right now the decision engine is rules plus physics — no ML sits in this loop. It issues
**TRANSFER BRINE**, basis `physics_and_rules`, `ml_result: null` — no model result because no model
exists — and the versioned rule hash is shown so the advice is auditable."

**Capture:** RecommendationCard with basis/rule hash/`ml_result: null`.

### 2:50 – 3:15 · Operator decision with validation (Module 14)

**Do:** in the same card, record the operator decision — choose `WAIT AND RECHECK` and file it.
**Say:** "Feedback goes through a closed vocabulary — HARVEST NOW, TRANSFER BRINE, PROTECT / DRAIN,
WAIT AND RECHECK. An invalid action would be rejected with a 422. The trace below the recommendation
now shows advice → decision → outcome as separate, auditable records."

**Capture:** DecisionPanel + TraceCard.

### 3:15 – 3:50 · Outcomes and honest validation (Modules 15, 16)

**Do:** open `/predicted-vs-actual`.
**Say:** "Two demonstration events are in the ledger. Exactly one is comparable — the other never had
a post-event salinity recorded, and the system says so rather than counting it as zero. On the one
comparable demonstration event: forecast vs actual rainfall +3 mm; simulated salinity error
−34.345 g/L. **n = 1, demo data — this is evidence the machinery works, not simulator skill.**"

**Capture:** predicted-vs-actual page (chart + n/coverage labels).

### 3:50 – 4:30 · The ML layer refuses correctly (Modules 08, 09, 11)

**Do:** return to `/dashboard`. In the **Model risk** card click **Predict risk**.
**Say:** "Here is the honesty behaviour. With no trained model, the risk endpoint refuses with the
module's own machine-readable code — nothing is predicted from nothing. The explainability card does
the same: SHAP is computed only from a registered artifact, and there is no artifact. `/evaluation`
and `/experiments` are empty for the same reason."

**Capture:** refusal text on MlRiskCard and ExplainabilityCard.

### 4:30 – 5:00 · Close on state (Modules 08, 17, evidence)

**Do:** open `http://localhost:8000/docs` (Swagger, showing the 91 endpoints / 21 API prefixes).
**Say:** "The whole API surface is here — 91 endpoints across 21 module prefixes. The system state is
fully open: `trained: false`, `evidential_models: 0`, two pans, three observations, all labelled
demonstration. That is the design: an evidence-gated ML layer that will light up — training,
evaluation, explanation, deployment — the moment a genuine field dataset passes the gate; and a
ready field-data sheet to collect it. No field validation is claimed today."

**Capture:** `/docs`, and the module statuses.

## Scripted one-liners (for closing honesty)

- "Every figure on screen is either arithmetic I can show you by hand, or a value explicitly
  labelled 'demonstration'."
- "There are no field measurements and no trained models in this system yet — you saw the gates refuse
  rather than invent."
- "The repository ships the exact commands that reproduce all of this (`docs/evidence/reproducibility.md`)."

## Fallbacks if something goes wrong

| Symptom | Action |
|---------|--------|
| Dashboard empty state "No active salt pans" | Register the demo pan first (`docs/STAGE8_DEMO_GUIDE.md` steps 1–3) |
| Health shows unreachable | Fix `.env` `MONGODB_URI`; uvicorn `--reload` picks code up |
| Recommendation refuses "no recorded state" | Record observation + weather on the pan, refresh |
| Timing running long | Trim §"Home" narration to one sentence; let screens do the talking |

## What this script deliberately does NOT claim

- No simulator-accuracy claim (n = 1, demonstration).
- No ML capability (no model exists).
- No field or operational science result.