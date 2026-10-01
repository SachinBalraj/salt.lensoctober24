# SaltLens DSS

**An AI-Driven Digital Twin Decision Support System for Climate-Resilient
Salt-Pan Management** — research prototype (foundation stage).

Repo for the SaltLens academic-paper project ("SaltLens"): a decision-support
system that helps salt-pan operators monitor brine production and anticipate
rain-dilution/overflow risk under climate variability.

## Status: Modules 02 → 12, 15 and 17 built

**Module 02 (salt-pan registration)**, **Module 03 (pan observation)**, **Module
04 (weather data)**, **Module 05 (pan-specific digital twin)** and **Module 06
(what-if rainfall simulator)** are implemented end to end: FastAPI routers over
MongoDB, and a Next.js UI with registry, registration, detail, edit,
observation-entry and weather-entry pages, plus the rainfall what-if panel and
its salinity curve. **Module 07 (research datasets)**, **Module 08 (ML
training)**, **Module 09 (model evaluation)**, **Module 10 (risk prediction)**,
**Module 11 (SHAP explainability)**, **Module 12 (decision-support
engine)** and **Module 15 (actual-outcome recording)** are built as well. Module 09 has a `/evaluation` page carrying the
comparison table, per-column charts, the confusion matrix and the
cross-validation report; Module 15 has its own `/predicted-vs-actual` page, which
is where the three per-event errors are read.

**Module 17 (model registry and deployment)** is built as a backend module at
`/api/models`. A trained version is `experimental`, becomes `validated` only
against a stored evaluation report, and reaches `deployed` only through a request
naming the version, the actor and the reason. Retraining writes a new
`experimental` version and never displaces the deployed one; replacing it
deliberately requires `supersede: true` plus a `supersede_reason`, and both steps
are appended to an unedited history. `POST /api/risk/predict` is answered by a
`deployed` version and by nothing else — no default model, no fallback, no
approximation — and reads its features from the pan's current digital twin state,
in the order the deployed model declared them, so a caller cannot hand the model a
number of its own choosing. There is no frontend page for it; the panel is on the
pan detail page.

**Module 10 (risk prediction)** makes the label vocabulary data rather than a
constant: `POST /api/risk/definitions/` writes a researcher's own classes,
thresholds and required rationales, versioned, and `POST /predict` is answered by
the deployed model version and by nothing else. With nothing deployed it refuses
with `409 no_deployed_model` rather than approximating. The features come from
the pan's current twin state; the row is stored with an `input_snapshot_id` that
hashes it, so the prediction can be checked later against what happened. A model
that reports a class but no probabilities still answers — the class is passed on
with `probability: null` and nothing is substituted for the missing number.

**Module 11 (SHAP explainability)** is built at `/api/explain`, and the position
it takes is that it will report genuine SHAP values or a named refusal, with
nothing in between. Every operational prediction is explained as it is made and
the response carries the explanation's id and status, so an operator is never
shown a risk figure without the option of asking why. The values come from the
`shap` library applied to the registered artifact: `TreeExplainer` in probability
space for a tree classifier, `PermutationExplainer` for anything else with a
`predict_proba`, with `method`, `approximate` and the shap version recorded so an
estimated value is never presented as a computed one. There is no fallback to
correlations, to model coefficients or to a permutation of its own — each of those
produces a ranked list of influences that looks like a SHAP attribution and is not
one. A linear model is deliberately not explained through its decision margin,
because that is a quantity no operator was ever shown.

Three quantities are kept apart and labelled apart: the model's probability (a
level, and not an explanation of itself), a SHAP contribution (a change, in
probability points, against a stated base value), and Module 06's mass-balance
result (an *input* the model read, neither an output nor a contribution).
Contributions are only stored if base value plus contributions equals the stored
probability within `1e-3`, and the residual is reported as `attribution_error`. A
feature the twin could not supply is marked `imputed` and reports no value at all,
because the preprocessor's median is a statistic of the training rows rather than
a measurement of this pan. `GET /api/explain/models/{model_id}/versions/{version}/importance`
is the model-evaluation half: mean absolute contribution per feature over the
held-out rows Module 09 scored, stored under a deterministic id so the same model
version gives the same ranking.

**Module 12 (decision-support engine)** is built at `/api/decisions`, and the
position it takes is that **it holds no thresholds and no default action of its
own.** Every action it recommends comes from a rule written through
`POST /api/decisions/rules`, stored with a content hash and a version and
readable back — a default action would be a rule nobody wrote and nobody could
revise without invalidating every recommendation already issued. The action
vocabulary is closed at four (`HARVEST NOW`, `TRANSFER BRINE`, `PROTECT / DRAIN`,
`WAIT AND RECHECK`), and a model contributes an estimate a criterion may read
rather than an action of its own.

The four kinds of evidence stay four kinds. The twin state, the Module 06 mass
balance, the stored model estimate and the rule's own matching are separate fields
with separate identifiers, and every input names where its value came from — they
are never merged into one table, because a simulated salinity sitting beside a
measured one is how a computed figure comes to be read as an observation. The
physics result carries the `run_id` it was read from, so the figure can be
replayed from the stored run. The model estimate is *read, never produced*: the
engine looks up the latest stored operational-risk prediction and copies it,
scoring nothing, so asking for advice cannot train or run a model and make this
module's own figures look like independent evidence.

There is no confidence on a recommendation — no number, no percentage, no
strength. A rule that matched is either decisive or it is not, and a number there
would be read beside the model's own confidence and imply a likelihood nobody
computed. A recommendation without a model estimate is a complete answer, not a
partial one: it carries the project's exact required label, *Physics/rule-based
recommendation — ML prediction unavailable.*, followed by which of the three
reasons applies. A rule that cannot read its input withholds its fallback and
refuses, so a safe-looking default cannot absorb exactly the pans whose evidence
is thinnest. The four refusals — `no_twin_state`, `simulation_inputs_missing`,
`no_rule_defined`, `rule_undecided` — each name what is missing, and none is
worth retrying. The panel is on the pan detail page, showing the action, the
criterion that decided it, and the four evidence blocks under their own headings.

**No research data, model accuracy, SHAP values, sensor readings, weather
observations or evaluation metrics are fabricated anywhere in this system.** In
Module 11 that rule has a specific consequence: an explanation that cannot be
computed is refused with the quantity that is missing — no class probabilities, no
artifact on this host, no reference sample, a snapshot that no longer matches its
hash — and the prediction's own class and probability are returned unchanged. An
attribution is never replaced by a correlation or a coefficient that would render
identically. In Module 12 that rule has a third consequence: a rule whose
criterion variable the engine cannot read withholds its declared fallback and
refuses, because a default that fires precisely when the system knows least is the
worst place to put one.
A measurement the operator did not take stays absent — blank inputs validate
to `null`, are stored as `null`, and the chart draws a gap. Nothing is
defaulted, averaged, interpolated or carried forward. In Module 09 that rule has
a specific consequence: an uncomputed metric is a `MetricReading` with
`computed: false` and `value: null`, and it is rendered as a dash with the
backend's reason attached — never as `0`, and never as a zero-height bar in the
chart. A measured zero renders as zero, because a model that predicts nothing is
a result and not a gap. The one hard-coded UI
chart is labelled `SIMULATED DEMO` and exists only to prove the charting
pipeline works. Optional demo salt pans are opt-in behind
`NEXT_PUBLIC_SALTLENS_DEMO_PANS=true`, prefixed `DEMO-`, carry no
observations, and can never be written to the database.

Provenance is enforced, not suggested. A hand-entered weather record is stored
as `manual` and shown as *Entered by hand*; `source: "weather_api"` is rejected
on manual entry and is written only by the forecast ingest route after a
connected provider returns data. Sensor ingestion is architecture only:
`POST /api/observations/ingest/sensor` reports 501 and `data_source: "sensor"`
is refused with 409 on both modules until a real gateway is connected, so a
reading can never claim a provenance that no instrument produced.

Modules 01, 13, 14 and 16 still sit behind FastAPI routers returning
`status: not_implemented` (HTTP 501). The Mongo connection is optional:
without `MONGODB_URI` the API boots and persistence endpoints — including
everything under `/api/pans`, `/api/observations` and `/api/evaluation` —
return `503` rather than fake data.

## Repository layout

```
.
├── backend/     FastAPI + Python services (auth, twin, ML, MongoDB)
├── frontend/    Next.js 16 (App Router) + TypeScript + Tailwind + recharts
├── ml/          training pipelines, feature engineering, notebooks, artifacts
├── data/        raw / processed / exports (git-ignored contents)
├── docs/        architecture, module map, API reference
├── scripts/     dev.sh (run both), setup.sh (one-time install)
└── tests/       integration / accepted-criteria tests (next stage)
```

## Prerequisites

- Node.js ≥ 20 and npm
- `uv` (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- (optional, later) MongoDB Atlas account for persistence

## Quick start

```bash
# 1) one-time setup: backend venv + all deps, frontend deps, .env files
bash scripts/setup.sh

# 2) run both dev servers (frontend :3000, backend :8000)
bash scripts/dev.sh

# 3) verify
curl http://localhost:8000/api/health        # backend alive
open http://localhost:3000                    # frontend shell
```

Per-subprocess hot-reload:
```bash
make dev            # both, with reload
make backend        # backend only (uvicorn --reload :8000)
make frontend       # frontend only (Next dev :3000)
```

## Tests & checks

```bash
make test           # backend pytest + frontend vitest, typecheck, lint & build
```
Backend: `pytest` (auto-discovers `backend/tests`, no Mongo required).
Frontend: `vitest` + `tsc --noEmit` + `eslint` + `next build`.

## Module 02 — salt-pan registry

```bash
# UI
open http://localhost:3000/pans          # registry: filter, search, page
open http://localhost:3000/pans/new      # register a pan

# API
curl http://localhost:8000/api/pans/status
curl http://localhost:8000/api/pans/?pan_status=all
```

Registering a pan requires a `pan_id`, `pan_name`, `location`, a positive
`area_m2`, `normal_depth_cm` and `target_harvest_salinity_g_l`; an optional
WGS84 `latitude`/`longitude` pair. These are **operator-declared design
values, not measurements**. `pan_id` is immutable, deactivation is a soft and
one-way lifecycle transition, and there is no hard delete. Contracts are in
[`docs/api.md`](docs/api.md).

To review the UI with clearly-labelled demo rows:

```bash
cd frontend && NEXT_PUBLIC_SALTLENS_DEMO_PANS=true npm run dev
```

## Module 03 — pan observation

```bash
# UI: open a pan, then record a reading on its detail page
open http://localhost:3000/pans

# API
curl http://localhost:8000/api/observations/status
curl "http://localhost:8000/api/observations/?pan_id=kzn-2026-a01"
```

An observation carries a `pan_id` that must be registered, an optional
`timestamp` (blank stamps the reading at save time, a supplied value backdates
it), up to five measurements, a `data_source` and an optional `note`. At least
one measurement is required.

```jsonc
POST /api/observations/
{
  "pan_id": "kzn-2026-a01",
  "salinity_g_l": 281.4,
  "water_depth_cm": 12.5,     // 0 is a valid reading for a dry pan
  "brine_temperature_c": 31.2,
  "humidity_percent": 62,
  "data_source": "manual"
}
```

Recording a reading advances the pan's `latest_observation`, and only forwards:
a backdated reading is kept in the history but does not overwrite a newer
current state. The trend chart is four small multiples (salinity, depth, brine
temperature, humidity) over real timestamps, with gaps preserved. Contracts
are in [`docs/api.md`](docs/api.md).

## Module 04 — weather data

```bash
# UI: open a pan, then record weather on its detail page
open http://localhost:3000/pans

# API
curl http://localhost:8000/api/weather/status
curl http://localhost:8000/api/weather/providers
curl "http://localhost:8000/api/weather/?pan_id=kzn-2026-a01"
```

A weather record carries a `pan_id` that must be registered, an optional
`timestamp` (the moment it was observed, or when the forecast was retrieved), an
optional `forecast_for` (the instant a forecast applies to), up to four values,
and a `source`. At least one value is required.

```jsonc
POST /api/weather/
{
  "pan_id": "kzn-2026-a01",
  "forecast_for": "2026-03-15T06:00:00Z",  // omit when recording what happened
  "rainfall_mm": 12.4,                     // 0 is a forecast of no rain, not a blank
  "rainfall_probability_percent": 70,
  "ambient_temperature_c": 24.5,
  "humidity_percent": 82,
  "source": "manual"
}
```

Leave `forecast_for` blank when recording what actually happened, and the record
is marked as an observation. Fill it in and the record is marked as a forecast,
which is what lets a researcher compare a prediction against what followed
without the two being silently merged.

**Every record keeps its source and the UI says so on every row.** A hand-entered
figure is stored as `manual` and displayed as *Entered by hand*; SaltLens will not
relabel it as an automated result. The two automated sources are guarded
structurally rather than by convention:

- `source: "weather_api"` is **rejected with 422** on manual entry. Only
  `POST /api/weather/ingest/{provider_name}` writes that value, and it sets it
  itself after a registered provider has actually returned data.
- `source: "sensor"` is refused with 409 while no weather gateway is connected.

Records are append-only, so a backdated entry is kept exactly as entered and
never overwrites a newer one. Blank values stay `null` end to end and the trend
chart breaks the line at the gap rather than bridging it.

### Adding a real weather API

The forecast path is built and deliberately empty. Implement the `WeatherProvider`
protocol in `backend/app/services/weather_providers.py`, then register it:

```python
from app.models.enums import WeatherSource
from app.services.weather_providers import (
    ForecastWindow,
    ProviderForecastPoint,
    WeatherProviderError,
    provider_registry,
)

class OpenWeatherProvider:
    name = "openweather"
    source = WeatherSource.WEATHER_API

    async def available(self) -> bool: ...
    async def fetch_forecast(self, pan, window: ForecastWindow): ...

provider_registry.register(OpenWeatherProvider())
```

No schema, route, storage or UI change is needed. The provider appears in
`GET /api/weather/providers`, a **Fetch forecast** button appears on the pan
page, and the stored records are labelled *Weather API* because the server said
so. Until a provider is registered the registry is empty, `GET /api/weather/providers`
says so plainly, and the ingest route answers 404 rather than returning a
plausible-looking forecast. Contracts are in [`docs/api.md`](docs/api.md).

## Module 05 — pan-specific digital twin

```bash
# UI: open a pan; the twin appears at the foot of its detail page
open http://localhost:3000/pans

# API
curl http://localhost:8000/api/twin/status
curl http://localhost:8000/api/twin/kzn-2026-a01/state
curl http://localhost:8000/api/twin/kzn-2026-a01/history
curl -X POST http://localhost:8000/api/twin/kzn-2026-a01/refresh
```

The twin is the pan's latest known state, **joined from records that already
exist** — not calculated. Three inputs, one static:

| Quantity | Comes from |
| --- | --- |
| `salinity_g_l`, `water_depth_cm`, `brine_temperature_c` | newest pan observation |
| `ambient_temperature_c`, `humidity_percent` | newest non-forecast weather record |
| `forecast_rainfall_mm`, `rainfall_probability_percent` | newest weather forecast |
| `pan_area_m2`, `location` | the pan's registration |

Selection is "newest by the instant the record describes", not newest by
insertion order, so a reading backfilled from last week cannot become the current
state just because it was typed today. Observation and forecast are kept apart by
the query itself: an observed rainfall figure never fills `forecast_rainfall_mm`,
and a forecast never supplies an observed quantity.

**The twin does not invent.** A quantity no record supplied stays `null`, is named
in `missing_fields`, and carries a sentence in `missing_inputs` saying which
record would fill it. Nothing is carried forward from an older reading and nothing
is averaged — a stale figure wearing a current timestamp is a fabrication. A
measured `0` stays distinct from an unmeasured quantity.

A registered pan with nothing recorded still returns a complete response, with
every quantity marked unavailable. That is a normal day-one state, not an error.

**Assembly time is not evidence age.** `timestamp` is when the state was
assembled, and each contributing record carries its own `timestamp` and
`age_seconds`. The UI shows the ages next to the values, because a twin assembled
this second out of last week's readings is not current, and the assembly time
alone would imply otherwise.

### Snapshots

`GET /state` stores nothing, so opening a page cannot manufacture history.
`POST /refresh` writes an immutable snapshot and reports `changed`, so a change
log built on the history contains real changes rather than no-op refreshes.
`GET /history` returns the snapshots, each keeping the record it was built from.

The derived fields `brine_volume_m3`, `brine_mass_kg`, `estimated_crust_cm` and
`confidence` stay `null` in the twin. Module 06 computes its own brine volume and
salt mass for the inputs it is given, but does not write them onto a twin: a
what-if answer is a scenario, not a measurement of the pan.

## Module 06 — what-if rainfall simulator

A deterministic water and salt balance. **This is physics, not machine
learning**, and it is labelled that way on every screen that shows a result.

```bash
# API
curl http://localhost:8000/api/simulator/status

curl -X POST http://localhost:8000/api/simulator/mass-balance \
  -H 'Content-Type: application/json' \
  -d '{"area_m2":12500,"initial_depth_m":0.155,
       "initial_salinity_g_l":285,"rainfall_mm":20}'

curl -X POST http://localhost:8000/api/simulator/rainfall-scenarios \
  -H 'Content-Type: application/json' \
  -d '{"area_m2":12500,"initial_depth_m":0.155,
       "initial_salinity_g_l":285,"rainfall_scenarios_mm":[12.5]}'
```

### The balance

```
V0 = A × D0              initial brine volume   (m³)
Vr = A × R               rainfall volume        (m³)
V1 = V0 + Vr             post-rain volume       (m³)
C1 = C0 × V0 / V1        post-rain salinity     (g/L)
Dilution % = ((C0 − C1) / C0) × 100
```

`A` pan area (m²), `D0` initial brine depth (m), `C0` current salinity (g/L),
`R` rainfall depth (mm at the API, m inside the balance). Instantaneous complete
mixing; no salt added or removed during the event.

Worked example — a 12,500 m² pan holding 0.155 m of 285 g/L brine with 20 mm of
rain: V0 = 1937.5 m³, Vr = 250 m³, V1 = 2187.5 m³, C1 = 252.43 g/L, dilution
11.43%.

### The panel

On a pan's detail page. Rainfall scenarios are 0, 5, 10, 20, 50 and 100 mm plus
any custom depth, and one sweep resolves all of them and charts rainfall against
predicted salinity. Every point on the curve is a depth that was actually
computed, and the x axis is ticked at those depths so a smooth line cannot
suggest the gaps between samples were computed too.

### What this module will not do

- **No database and no history.** The balance is fully determined by its four
  inputs, so there is nothing to persist and no route that fails when Mongo is
  absent.
- **No result in the request.** The body carries inputs only; there is no field
  by which a caller can state a predicted salinity. The equations exist once, on
  the server, and the frontend has no copy of them to drift from.
- **No default for a missing input.** All four are required and `422` names the
  absent ones. A default starting salinity would be a fabricated one, so a pan
  with no recorded reading asks for a figure rather than borrowing the harvest
  target.
- **No division by nothing.** A zero area, zero depth, negative salinity or
  negative rainfall is refused with `422` and a sentence explaining why.
- **No undefined percentage as a zero.** A starting salinity of 0 g/L leaves the
  dilution ratio undefined, so it returns `dilution_percent: null` with a reason.
  The post-rain salinity is still reported.

### The assumptions, which are not hidden

Returned with every result and rendered beside them, never in a collapsed
disclosure:

> **Physics-based simulation under simplifying assumptions; not an ML
> prediction.**

- **Complete mixing** — the rain and the brine reach one uniform salinity
  instantly, so a single concentration describes the whole pan.
- **No evaporation** during the event; rainfall is the only water that moves.
- **No drainage** — water leaves the pan by no route while the event runs.
- **No overflow** — the pan is deep enough to hold every drop, so nothing spills
  and no brine is lost over the wall.
- **No seepage** through the pan floor.
- **No brine transfer** — brine is neither pumped in nor drawn off, so the pan
  keeps exactly the salt it started with.
- **No crystallization** during the calculation; salt stays dissolved.
- **Rainfall as one instantaneous depth** — total depth, not timing or intensity.

The salt the pan holds is reported before and after the event and is the same
number. That is what makes the drop in salinity arithmetic rather than salt loss,
and it is the one check a reader can do by eye.

## Module 15 — actual-outcome recording

What actually happened, measured against what the mass balance predicted. This
is the module Module 16 validates the model with, so its whole design is aimed at
one failure: a record that flatters the model.

```bash
# UI
open http://localhost:3000/predicted-vs-actual   # error across every pan
# and, on a pan's page, the "Actual outcome" panel

# API
curl http://localhost:8000/api/outcomes/status

curl -X POST http://localhost:8000/api/outcomes/ \
  -H 'Content-Type: application/json' \
  -d '{"pan_id":"kzn-2026-a01","event_id":"storm-2026-03-14",
       "actual_rainfall_mm":40,"forecast_rainfall_mm":30,
       "pre_event_salinity_g_l":280,"pre_event_depth_cm":20,
       "post_event_salinity_g_l":255,"post_event_depth_cm":24,
       "harvest_outcome":"partial"}'

curl 'http://localhost:8000/api/outcomes/comparison'
```

### The three comparisons

| Comparison                  | Predicted from         | Compared against             |
| --------------------------- | ---------------------- | ---------------------------- |
| `simulation_error`          | the rain that fell     | measured post-event salinity |
| `forecast_simulation_error` | the forecast rain      | measured post-event salinity |
| `rainfall_forecast_error`   | the forecast rain      | the rain that fell           |

The first separates "was the chemistry right" from "was the forecast right",
which a single error figure cannot do. The prediction is computed on the server
by the Module 06 balance, and the `SimulationRun` behind it is stored with the
outcome, so any error on screen can be re-derived from the record.

`GET /api/outcomes/comparison` reports the mean absolute error, the bias (mean
signed error, which separates a biased model from a merely noisy one), the
largest absolute error, and the compared/not-comparable counts — per comparison.

### What this module will not do

- **No prediction, error or accuracy in the request.** The body carries
  measurements only, and the schema sets `additionalProperties: false`, so a
  client cannot assert a result it would then be graded on.
- **No filling in a measurement nobody took.** An event with no recorded
  post-event salinity gets `null` errors, a named `missing_input`, and
  `is_comparable: false`. It is excluded from every mean, because counting it as
  `0` would claim the model was exactly right on an event nobody watched.
- **No undefined percentage as a zero.** Percentage error against a measurement
  of `0` is `null` with a reason. The absolute error is still reported.
- **No aggregate without its denominator.** A mean never appears without the
  number of events it covers and the number excluded, on the API and on screen.
- **No silent second record of one event.** There is no update route, and a
  repeat of `(pan_id, event_id)` is refused with `409`; a unique index backstops
  concurrent writes. Record a correction under a new `event_id`.
- **No borrowing another pan's evidence.** An explicit `references` entry must
  resolve and belong to the same pan, or the write is refused.
- **No numbers without a source.** An unreachable backend produces a stated
  "unavailable", and the counts are omitted rather than rendered as `0`.

## Configuration & secrets

Secrets are configured through **local, git-ignored files only**; `.env*` and
`.env.local` files are never committed. Templates are committed as `.env.example`.

| File (committed)       | Copy to (git-ignored)  | Contents                                          |
| ---------------------- | ---------------------- | ------------------------------------------------- |
| `backend/.env.example` | `backend/.env`         | FastAPI host/port, JWT secret, `MONGODB_URI`      |
| `frontend/.env.example`| `frontend/.env.local`  | `NEXT_PUBLIC_API_BASE_URL` (backend origin)       |

```bash
cp backend/.env.example backend/.env           # then edit MONGODB_URI if needed
cp frontend/.env.example frontend/.env.local
```

| Variable                     | Default                     | Purpose                                    |
| ---------------------------- | --------------------------- | ------------------------------------------ |
| `BACKEND_HOST`/`BACKEND_PORT`| `0.0.0.0` / `8000`          | where FastAPI listens                      |
| `FRONTEND_ORIGIN`            | `http://localhost:3000`     | CORS-allowed browser origin                |
| `JWT_SECRET_KEY`             | *(empty → nothing verifies)* | **required in production**                 |
| `MONGODB_URI`                | *(unset → no persistence)*  | MongoDB Atlas connection string            |
| `DATABASE_NAME`              | `saltlens`                  | default MongoDB database                   |
| `RATE_LIMIT_ENABLED`         | `true`                      | per-IP in-memory throttle; keep on         |
| `RATE_LIMIT_PER_MINUTE`      | `600`                       | general per-IP ceiling                     |
| `RATE_LIMIT_HEAVY_PER_MINUTE`| `120`                       | tighter ceiling for compute-heavy paths    |
| `NEXT_PUBLIC_API_BASE_URL`   | `http://localhost:8000`     | backend base URL the browser calls         |
| `NEXT_PUBLIC_SALTLENS_DEMO_PANS` | *(unset → off)*         | opt-in, browser-only demo rows for layout review |
| `SENSOR_INGEST_ENABLED`       | `false`                   | must stay `false`: no sensor gateway is connected |

There is deliberately no environment variable for weather API providers.
Whether a forecast can be fetched is decided by what is registered in the
provider registry, not by a switch that could disagree with reality. See
[Adding a real weather API](#adding-a-real-weather-api).

### Security posture and the production guard

The API is **open access**: no route requires authentication yet, and no
identity or role is enforced anywhere. This is a deliberate (and currently the
only) posture; Module 1 authentication remains a placeholder. The counter
measures that do exist run at the edges rather than in a session layer:

- **No insecure secret.** `JWT_SECRET_KEY` defaults to empty, meaning no token
  could ever be trusted; the old bundled placeholder is also treated as
  insecure. At startup the process (in `APP_ENV=production`) refuses to start
  without a strong secret, an `https://` `FRONTEND_ORIGIN`, a configured
  `MONGODB_URI`, and rate limiting enabled. In development the same checks log
  warnings instead of blocking.
- **Per-IP rate limiting** is on by default (an in-memory fixed window, see
  `RateLimitMiddleware`), with a tighter budget for compute-heavy endpoints.
  The limiter carries no `X-Forwarded-For` trust, so a real deployment in front
  of a proxy should set the backend to accept proxied requests deliberately.
- **Generic 500s hide internals.** Unhandled errors log full details server-side
  and return a fixed message; health endpoints never echo private Mongo text.
- **Exported prose is escaped.** Free-text cells written to CSV (experiment
  notes, evaluation/validation reasons) are prefixed so a cell cannot be read as
  a spreadsheet formula. Dataset CSV cells are intentionally *not* sanitized:
  they are measurements another module parses back, and rewriting them would
  change the science.

The frontend exposes only `NEXT_PUBLIC_*` variables to the browser; no secret,
token, or `MONGODB_URI` is ever sent to a client.

> Secrets rule: **any real credential lives only in untracked `.env` files.**
> `.gitignore` blocks `backend/.env`, `frontend/.env.local`, and `.env*.local`
> at the repo root. See `docs/architecture.md` for the full policy.

## Module roadmap

`01 auth · 02 pans · 03 observations · 04 weather · 05 twin · 06 simulator ·
07 datasets · 08 ml-training · 09 evaluation · 10 risk · 11 explanations(shap) ·
12 decisions · 13 dashboard · 14 feedback · 15 outcomes · 16 validation ·
17 model-registry · 18 export`

Modules 02 → 09, 15 and 17 are built; the rest are wired FastAPI routers
returning `not_implemented` placeholders. Implementation order and contracts are
documented in `docs/modules.md` and `docs/api.md`. Planned frontend routes
mirror these modules 1:1.

## Research integrity notes

- Mass-balance/simulation outputs are explicitly tagged `SIMULATED` until
  validated against pan measurements.
- SHAP/SHAP-variants, precision/recall, MAE/RMSE, and risk scores must come
  from a *trained* model run on *real imports* — never from constants.
- The dashboard auto-polls `NEXT_PUBLIC_API_BASE_URL` for health; a live
  readout confirms the wire works end to end.

## Docs

- `docs/architecture.md` — stack, decisions, flow
- `docs/modules.md` — the 18 modules and their planned endpoints
- `docs/api.md` — API reference (OpenAPI at `/docs`)
