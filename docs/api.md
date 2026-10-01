# API reference

Base URL: `http://localhost:8000` (configurable via `NEXT_PUBLIC_API_BASE_URL`).

Interactive docs (Swagger UI) at `GET /docs`; OpenAPI JSON at `GET /openapi.json`.

## Meta endpoints

| Method | Path           | Description                                    |
| ------ | -------------- | ---------------------------------------------- |
| GET    | `/`            | Service metadata (name, version, links)        |
| GET    | `/api/health`  | Liveness + environment + database connectivity |
| GET    | `/api/modules` | Count + router prefixes for all 18 modules     |

### `GET /api/health`

```json
{
  "status": "ok",
  "app": "SaltLens DSS API",
  "version": "0.1.0",
  "environment": "development",
  "database": "not_configured",
  "python": "3.12.13"
}
```

`database` is `not_configured` when `MONGODB_URI` is unset, `connected` when
the cluster answers a `ping`, or `error` when it does not.

## Module 02 — salt-pan registry

Implemented. `pan_id` is the natural key, stored and matched lower-case.
Registration and edits return `422` with a `detail` array of
`{loc, msg, type}`; the frontend maps those onto form fields.

| Method | Path                            | Description                                     |
| ------ | ------------------------------- | ----------------------------------------------- |
| GET    | `/api/pans/status`              | Module status + live registry counts            |
| POST   | `/api/pans/`                    | Register a pan → `201` with the stored document |
| GET    | `/api/pans/`                    | List pans, filtered, searched and paged         |
| GET    | `/api/pans/{pan_id}`            | Open one pan, case-insensitively                |
| PATCH  | `/api/pans/{pan_id}`            | Edit the operator-declared fields               |
| POST   | `/api/pans/{pan_id}/deactivate` | Withdraw a pan from active operation            |

### `GET /api/pans/status`

Never fails — reporting availability is the point. When persistence is
configured it carries live counts:

```json
{
  "module": "salt-pans",
  "module_number": 2,
  "status": "implemented",
  "endpoints": ["POST   /api/pans/", "..."],
  "registry": { "available": true, "total_active": 12, "total_inactive": 2 }
}
```

Without `MONGODB_URI` it still answers `200`, reporting the problem instead
of hiding it:

```json
{
  "registry": {
    "available": false,
    "detail": "Database not configured. Set MONGODB_URI in backend/.env to enable persistence."
  }
}
```

### `POST /api/pans/`

```json
{
  "pan_id": "pans-2026-a01",
  "pan_name": "Northern evaporation pan",
  "location": "Port Shepstone",
  "latitude": -30.7444,
  "longitude": 30.8071,
  "area_m2": 12500,
  "normal_depth_cm": 15.5,
  "target_harvest_salinity_g_l": 285,
  "risk_thresholds": {
    "flood_depth_cm": 25,
    "dilution_salinity_g_l": 180,
    "critical_rainfall_24h_mm": 50,
    "target_flow_cm": 12,
    "max_observation_age_hours": 24
  }
}
```

`area_m2`, `normal_depth_cm` and `target_harvest_salinity_g_l` must all be
greater than zero.

`risk_thresholds` are **your** limits for this pan, and every field is optional
and optional-to-omit: a field left out is a limit nobody has configured, which is
recorded as null. This project supplies no values for them and has no default —
a number invented here would read as though it meant something. The examples
above show the shape, not recommended figures. A decision rule that needs an
unconfigured limit is refused (`rule_undecided`) rather than advised against an
assumed number; see [`bound_variable`](#get-apidecisionsrulesreference-draft).

`max_observation_age_hours` is the oldest reading that may still support a
recommendation. Beyond it the engine refuses with `insufficient_evidence` rather
than deciding about water that old. `latitude`/`longitude` are optional, but must be supplied
together as a valid WGS84 pair. A duplicate `pan_id` returns `409`
(`duplicate_document`); an unconfigured database returns `503`.

Validation is checked before persistence, so a malformed body is always a
`422` even when MongoDB is unavailable — an infrastructure error never masks
a client mistake.

### `GET /api/pans/`

| Parameter    | Default  | Notes                                              |
| ------------ | -------- | -------------------------------------------------- |
| `pan_status` | `active` | `active`, `inactive` or `all`                      |
| `search`     | —        | Case-insensitive substring over ID, name, location |
| `limit`      | `50`     | 1–200                                              |
| `offset`     | `0`      |                                                    |

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0,
  "total_active": 0,
  "total_inactive": 0
}
```

Every returned pan carries `latest_observation`: a copy of its newest reading,
or `null` until the first observation is recorded. It is never an average, an
estimate or a fallback to the pan's declared design values. See
[Module 03](#module-03--pan-observation).

### `PATCH /api/pans/{pan_id}`

Accepts `pan_name`, `location`, `latitude`, `longitude`, `area_m2`,
`normal_depth_cm`, `target_harvest_salinity_g_l` and `risk_thresholds`. `pan_id`
is rejected — it is immutable because later modules key observations, weather and
outcomes to it. Send `null` for both coordinates to clear the pair.

`risk_thresholds` **replaces the block whole** rather than merging into it, so
a field omitted from an update becomes unconfigured. That is deliberate: it
makes "I removed my flood depth" expressible, and merging would make a field left
out indistinguishable from one left alone. Send `{}` to clear every limit.

### `POST /api/pans/{pan_id}/deactivate`

A soft, one-way lifecycle transition: sets `is_active` to `false` and stamps
`deactivated_at`. The document is kept so anything referencing it stays
intact. Deactivating an already-inactive pan returns `409`
(`already_inactive`). There is no hard delete endpoint in Module 02.

## Module 03 — pan observation

| Method | Path                                 | Purpose                                  |
| ------ | ------------------------------------ | ---------------------------------------- |
| GET    | `/api/observations/status`           | Module status and live observation count |
| POST   | `/api/observations/`                 | Record one reading for a registered pan  |
| GET    | `/api/observations/`                 | List readings, newest first              |
| GET    | `/api/observations/{observation_id}` | Open one stored reading                  |
| POST   | `/api/observations/ingest/sensor`    | Reserved for a gateway — always 501      |

### `GET /api/observations/status`

Never fails. An unconfigured database is reported as
`observations.available = false` rather than raised, because reporting status
is the one thing this endpoint exists to do.

```json
{
  "module": "observations",
  "module_number": 3,
  "status": "implemented",
  "sensor_ingest": {
    "available": false,
    "detail": "No sensor gateway is connected. Readings can only be recorded manually or as imports; data_source='sensor' is refused with 409."
  },
  "observations": { "available": true, "total_observations": 12 }
}
```

### `POST /api/observations/`

```jsonc
{
  "pan_id": "kzn-2026-a01",   // required, must be a registered pan (404 if not)
  "timestamp": "2026-03-14T06:30:00Z", // optional; omitted = stamp at save time
  "salinity_g_l": 281.4,      // optional, 0–500
  "water_depth_cm": 12.5,     // optional, 0–1000; 0 is valid for a dry pan
  "brine_temperature_c": 31.2,// optional, -20–120
  "humidity_percent": 62,     // optional, 0–100
  "ambient_temperature_c": 27,// optional, -50–80
  "data_source": "manual",    // manual | sensor | import; defaults to manual
  "note": "After the morning pump-down."  // optional, ≤ 500 characters
}
```

Returns `201` with the stored reading. Rules:

- **At least one measurement is required** (`422`). A reading with nothing
  measured cannot be charted and is not evidence of the pan's condition.
- **Unmeasured fields stay `null`.** They are never defaulted, averaged or
  carried forward.
- **Zero is a real measurement**, not a blank. A dry pan measures zero depth.
- **`timestamp` may be backdated but never future-dated** (`422`), with five
  minutes of tolerance for client clock skew.
- **`data_source: "sensor"` returns `409`** while `SENSOR_INGEST_ENABLED` is
  `false`. Accepting it would file a provenance claim no instrument made.
- An unregistered `pan_id` returns `404`; a deactivated pan still accepts
  readings, since field observations often arrive after a pan is retired.

Recording a reading also advances the pan's `latest_observation`, and **only
forwards**: a backdated reading is stored and charted but does not overwrite a
newer current state. `latest_observation` survives pan edits and deactivation.

### `GET /api/observations/`

Query: `pan_id`, `start`, `end` (ISO 8601, both bounds inclusive), `limit`
(1–1000, default 200), `offset`.

```json
{
  "items": [ /* newest first */ ],
  "total": 42,
  "limit": 200,
  "offset": 0,
  "earliest_timestamp": "2026-01-04T06:00:00Z",
  "latest_timestamp": "2026-03-14T06:30:00Z"
}
```

`total` counts every reading matching the filter, so a caller can tell that its
window was truncated rather than assuming the response is the whole history.
An unregistered `pan_id` filter returns `404`. `start` later than `end` returns
`422`. Only recorded values are returned: nothing is filled in, interpolated or
aggregated.

There is deliberately **no unique index on `(pan_id, timestamp)`** — imported
historical data can legitimately contain several readings at one instant.

### `POST /api/observations/ingest/sensor`

Reserved, and always returns `501`. The route exists so the ingest contract has
a home and so the absence of a connected instrument is explicit rather than
silently missing. It accepts nothing while no gateway is configured.

## Module 04 — weather data

Weather records are stored per pan and are **append-only**: nothing in this
module updates or deletes a record, so the history an operator or a provider
contributed stays auditable.

The one invariant worth reading twice: **a hand-entered record can never claim
an automated origin.** `source: "weather_api"` is rejected on `POST /api/weather/`
with `422`, and the only code path that writes it is the ingest route, which
takes the value from the registered provider rather than from the request. Until
a provider is connected the registry is empty and the ingest route returns `404`,
so there is no way to produce a record that looks like a successful fetch.

| Field                          | Type               | Notes                                                      |
| ------------------------------ | ------------------ | ---------------------------------------------------------- |
| `pan_id`                       | string             | Must be registered. Matched case-insensitively.            |
| `timestamp`                    | datetime, optional | Observation time. Blank stamps it at save time.            |
| `forecast_for`                 | datetime, optional | Instant a forecast applies to. Blank ⇒ an observation.     |
| `rainfall_mm`                  | float, optional    | 0–1000. `0` is a forecast of no rain, not a missing value. |
| `rainfall_probability_percent` | float, optional    | 0–100.                                                     |
| `ambient_temperature_c`        | float, optional    | −50–80 °C.                                                 |
| `humidity_percent`             | float, optional    | 0–100.                                                     |
| `source`                  | enum                  | `manual` \| `sensor` \| `weather_api`.                        |

At least one value is required. A record with all four null says nothing about
the weather and is refused with `422`.

`forecast_for` drives `is_forecast`, which is derived server-side so the two can
never disagree: a record cannot be filed as a forecast while claiming to have
observed it. `forecast_for` must not precede `timestamp` (a forecast made after
the instant it describes is not a forecast) and must be within 14 days, which
catches a mistyped year.

### `GET /api/weather/status`

```json
{
  "module": "weather",
  "module_number": 4,
  "status": "implemented",
  "manual_entry":  { "available": true,  "detail": "…" },
  "weather_api":   { "available": false, "registered_providers": [], "detail": "No weather API is connected. …" },
  "sensor_ingest": { "available": false, "detail": "…" },
  "weather":       { "available": false, "detail": "Database not configured. …" }
}
```

Never fails. An unavailable database is reported as `weather.available = false`
rather than raised, because reporting unavailability is what the endpoint is
for.

### `GET /api/weather/providers`

Which weather APIs this deployment can actually reach. Needs no database — it is
a deployment fact, not stored data.

```json
{
  "registered": [],
  "count": 0,
  "manual_entry_available": true,
  "detail": "No weather API is connected. Readings are recorded manually or by a sensor; …"
}
```

The empty list is the truthful answer for this prototype, and the UI uses it to
explain why every record is labelled as hand-entered.

### `POST /api/weather/`

```json
{
  "pan_id": "kzn-2026-a01",
  "timestamp": "2026-03-14T06:30:00Z",
  "rainfall_mm": 12.4,
  "rainfall_probability_percent": 70,
  "ambient_temperature_c": 24.5,
  "humidity_percent": 82,
  "source": "manual"
}
```

`201` with the stored record. `timestamp` defaults to now and is preserved
exactly as supplied, so a record can be backdated. Fields left blank are stored
as `null` — never defaulted, averaged or carried forward.

| Status | When                                                                                                                                                                  |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `404`  | `pan_id` is not registered.                                                                                                                                           |
| `409`  | `source: "sensor"` while no weather gateway is connected.                                                                                                             |
| `422`  | `source: "weather_api"`, an out-of-range value, a future `timestamp`, a `forecast_for` before `timestamp` or beyond 14 days, an all-null record, or an unknown field. |
| `503`  | No `MONGODB_URI`.                                                                                                                                                     |

Validation runs before the database handle is resolved, so a malformed payload
returns `422` even when MongoDB is not configured — the same ordering as
Module 03.

### `GET /api/weather/`

Query: `pan_id`, `start`, `end` (ISO 8601, both bounds inclusive), `limit`
(1–1000, default 200), `offset`. Same response envelope as observations, with
`total` again reporting the full match count so a caller can tell its window was
truncated.

Forecasts and observations share one history, because comparing what was
predicted against what happened is the point of keeping both. Each record
reports its own `is_forecast` and `source`, and the UI labels both.

Filtering is on `timestamp`, not `forecast_for`. A forecast retrieved now and
aimed at tomorrow is filed under now, which is when it became part of the
record.

There is deliberately **no unique index on `(pan_id, timestamp)`** — a provider
may legitimately publish more than one reading for an instant.

### `GET /api/weather/{weather_id}`

`200` with one stored record exactly as recorded. `404` if unknown.

### `POST /api/weather/ingest/{provider_name}`

The only path that writes `source: "weather_api"`, and it takes that value from
the registered provider rather than from the request.

Query: `pan_id` (required), `start` and `end` (ISO 8601; default to now and now
+ 48 hours).

```json
{
  "provider": "openweather",
  "source": "weather_api",
  "pan_id": "kzn-2026-a01",
  "requested_start": "2026-03-14T12:00:00Z",
  "requested_end": "2026-03-16T12:00:00Z",
  "stored": 12,
  "rejected": 1,
  "observations": [ /* … */ ]
}
```

`rejected` counts points the provider returned that SaltLens would not store,
such as a forecast aimed at an instant that already passed. They are reported
rather than silently dropped, so a partial ingest stays visible.

| Status | When                                                                                                        |
| ------ | ----------------------------------------------------------------------------------------------------------- |
| `404`  | No provider by that name is registered — and **nothing is stored**.                                         |
| `404`  | `pan_id` is not registered. The provider is never called, so an unknown pan cannot trigger a paid API call. |
| `422`  | `end` earlier than `start`.                                                                                 |
| `502`  | The provider raised. An upstream fault is never reported as an absence of weather data.                     |
| `503`  | The provider is registered but reports itself unavailable.                                                  |

**Connecting a real API is additive.** Implement the `WeatherProvider` protocol
in `app/services/weather_providers.py` and call `provider_registry.register(...)`
at start-up. No schema, route, storage or UI change is required. Registration
refuses a provider whose `source` is not `weather_api`, so a provider cannot file
instrument or operator data under an API label.

## Module 05 — digital twin

The twin is a **join, not a calculation**. For one pan it selects at most three
stored records and copies their values across unchanged. It contains no physics
and no interpolation, because a plausible-looking number nobody measured is worse
than an admitted gap.

### Which record supplies which quantity

| Quantity                       | Source record        | Selection rule                                                                                          |
| ------------------------------ | -------------------- | ------------------------------------------------------------------------------------------------------- |
| `salinity_g_l`                 | Pan observation      | newest by the instant it describes                                                                      |
| `water_depth_cm`               | Pan observation      | newest by the instant it describes                                                                      |
| `brine_temperature_c`          | Pan observation      | newest by the instant it describes                                                                      |
| `ambient_temperature_c`        | Weather observation  | newest **non-forecast** record; the observation's own figure is used only when no weather record exists |
| `humidity_percent`             | Weather observation  | as above                                                                                                |
| `forecast_rainfall_mm`         | Weather **forecast** | newest forecast record                                                                                  |
| `rainfall_probability_percent` | Weather **forecast** | newest forecast record                                                                                  |
| `pan_area_m2`                  | `SaltPan`            | static registration                                                                                     |
| `location`                     | `SaltPan`            | static registration                                                                                     |

Two rules follow from that table and are enforced by the queries, not by
filtering afterwards:

**"Latest" means latest by the instant described, not by insertion order.** A
reading backfilled from last week cannot become the current state just because
it was typed today.

**Observation and forecast are not interchangeable.** An observed rainfall
figure never populates `forecast_rainfall_mm`, and a forecast never supplies an
observed quantity. Presenting either as the other would mislabel the evidence.

### `GET /api/twin/status`

`200` always, including when the database is unavailable (`twin.available:
false`).

### `GET /api/twin/{pan_id}/state`

The pan's current state, assembled fresh. **Reading stores nothing**, so a page
view cannot manufacture history.

```json
{
  "twin_id": "twin-2f1c9a0e",
  "pan_id": "kzn-2026-a01",
  "pan_name": "Northern Evaporation Pan",
  "timestamp": "2026-03-14T12:00:00Z",
  "provenance": "observed",
  "twin_version": "0.1.0",
  "salinity_g_l": 285.4,
  "water_depth_cm": 12.5,
  "brine_temperature_c": 31.2,
  "ambient_temperature_c": 26.5,
  "humidity_percent": 64,
  "pan_area_m2": 12500.0,
  "location": "Port Shepstone",
  "forecast_rainfall_mm": 18.0,
  "rainfall_probability_percent": 80.0,
  "observation": {
    "id": "obs-9a1b",
    "timestamp": "2026-03-14T11:00:00Z",
    "source": "manual",
    "age_seconds": 3600.0
  },
  "weather": { "id": "wx-77c2", "timestamp": "…", "source": "manual", "age_seconds": 1800.0 },
  "weather_forecast": { "id": "wx-78d3", "timestamp": "…", "source": "manual", "age_seconds": 7200.0 },
  "is_complete": true,
  "missing_inputs": [],
  "missing_fields": [],
  "quantities": ["salinity_g_l", "water_depth_cm", "…"]
}
```

`timestamp` is when the state was **assembled**; the three input timestamps and
`age_seconds` say how old the **evidence** is. Those are different facts and the
UI shows both, because a twin assembled this second out of last month's readings
is not current.

A registered pan always returns `200`, even with nothing recorded. In that case
every quantity is `null`, `is_complete` is `false`, and each gap is explained:

```json
{
  "is_complete": false,
  "missing_fields": ["salinity_g_l", "forecast_rainfall_mm"],
  "missing_inputs": [
    {
      "field": "salinity_g_l",
      "reason": "No pan observation has been recorded for this pan, so brine salinity is unavailable.",
      "expected_from": "observation"
    }
  ]
}
```

`404` if the pan is not registered. `503` if persistence is unavailable.

### `POST /api/twin/{pan_id}/refresh`

Reassembles the twin and stores an immutable snapshot. `201`:

```json
{
  "state": { "…": "as above" },
  "stored": true,
  "changed": true,
  "previous_twin_id": "twin-1f0a"
}
```

`changed` reports whether any tracked quantity or contributing record differs
from the previous snapshot, so a change log built on top of it does not fill
with no-op entries. `previous_twin_id` is `null` on a pan's first snapshot.

### `GET /api/twin/{pan_id}/history`

Stored snapshots, newest first. Query: `start`, `end` (ISO 8601), `limit`
(1–1000, default 50), `offset`. Returns the same state objects as `/state`, so
each snapshot keeps the record it was built from and does not drift as newer
records arrive. `total` is the true count regardless of paging.

`404` if the pan is not registered. `422` if `end` precedes `start`.

### What the twin will not do

- **No backfill.** A salinity measured yesterday and absent from today's reading
  is reported as absent. A stale figure under a current timestamp is a
  fabrication.
- **No averaging.** A quantity comes from exactly one record.
- **No derived values.** `brine_volume_m3`, `brine_mass_kg`, `estimated_crust_cm`
  and `confidence` exist on the model and stay `null`. Module 06 computes its own
  brine volume and salt mass for the inputs it is given, but does not write them
  onto a twin: a what-if answer is a scenario, not a measurement of the pan.
- **No client-supplied state.** `refresh` takes no body; the server owns the join.

## Module 06 — What-if rainfall simulator (`/api/simulator`)

A deterministic water and salt balance. Not a machine-learned model, and not a
rainfall forecast: it answers "if *R* millimetres fell on this pan, what would the
salinity be", which is a consequence of the inputs rather than a prediction about
the weather.

### The equations

```
A  = pan area (m²)              R  = rainfall depth (m)
D0 = initial brine depth (m)    C0 = current salinity (g/L)

V0 = A × D0                 initial brine volume   (m³)
Vr = A × R                  rainfall volume        (m³)
V1 = V0 + Vr                post-rain volume       (m³)
C1 = C0 × V0 / V1           post-rain salinity     (g/L)
Dilution % = ((C0 − C1) / C0) × 100
```

Instantaneous complete mixing, no salt added or removed during the event.

### `GET /api/simulator/status`

Reports the module as implemented, the model name, `is_ml: false`, the six preset
depths, the disclaimer and the assumption list. Touches no database, so it cannot
fail for want of one.

### `POST /api/simulator/mass-balance`

One rainfall event. All four inputs are required.

```bash
curl -X POST http://localhost:8000/api/simulator/mass-balance \
  -H 'Content-Type: application/json' \
  -d '{"area_m2": 12500, "initial_depth_m": 0.155,
       "initial_salinity_g_l": 285, "rainfall_mm": 20}'
```

```json
{
  "area_m2": 12500.0,
  "initial_depth_m": 0.155,
  "initial_salinity_g_l": 285.0,
  "rainfall_mm": 20.0,
  "initial_volume_m3": 1937.5,
  "rainfall_volume_m3": 250.0,
  "post_rain_volume_m3": 2187.5,
  "initial_salt_mass_kg": 552187.5,
  "post_rain_salt_mass_kg": 552187.5,
  "salt_mass_conserved": true,
  "post_rain_salinity_g_l": 252.42857142857142,
  "salinity_drop_g_l": 32.571428571428584,
  "dilution_percent": 11.428571428571432,
  "dilution_percent_reason": null,
  "post_rain_depth_m": 0.175,
  "is_simulated": true,
  "model_name": "rainfall-dilution-mass-balance-v1",
  "disclaimer": "Physics-based simulation under simplifying assumptions; not an ML prediction.",
  "assumptions": ["Complete mixing: …"],
  "equations": ["V0 = A x D0  —  initial brine volume (m3)", "…"]
}
```

### `POST /api/simulator/rainfall-scenarios`

The same balance swept over the six presets — 0, 5, 10, 20, 50, 100 mm — plus
any custom depths in `rainfall_scenarios_mm`. Custom depths are added alongside
the presets rather than replacing them, so a custom scenario stays comparable on
the same axis, and a depth that repeats a preset is not counted twice. The
response is ordered by rainfall ascending, which is also the order the salinity
falls in, so a chart plots it directly.

```bash
curl -X POST http://localhost:8000/api/simulator/rainfall-scenarios \
  -H 'Content-Type: application/json' \
  -d '{"area_m2": 12500, "initial_depth_m": 0.155,
       "initial_salinity_g_l": 285, "rainfall_scenarios_mm": [12.5]}'
```

Each scenario carries `rainfall_mm`, `label`, `is_standard_preset`,
`rainfall_volume_m3`, `post_rain_volume_m3`, `post_rain_depth_m`,
`post_rain_salinity_g_l`, `salinity_drop_g_l`, `dilution_percent` and
`dilution_percent_reason`.

### What this endpoint will not do

- **No database, and no history.** The balance is fully determined by its four
  inputs, so the module has nothing to persist and no route to fail when Mongo is
  absent.
- **No result in the request.** The body carries inputs only. There is no field
  by which a caller can state a predicted salinity, so every figure on the panel
  came out of these equations.
- **No default for a missing input.** All four are required; `422` names the
  absent ones. A default starting salinity would be a fabricated one.
- **No division by nothing.** A zero area, a zero depth, a negative salinity or a
  negative rainfall depth is refused with `422` and a sentence explaining why,
  rather than producing a confident number from a degenerate balance.
- **No undefined percentage as a zero.** A starting salinity of 0 g/L leaves the
  dilution ratio undefined, so it comes back as `dilution_percent: null` with
  `dilution_percent_reason` set. The post-rain salinity is still reported.
- **No hidden assumptions.** `assumptions`, `equations`, `disclaimer` and
  `is_simulated` are part of every response, not fields the UI chooses to render.

### The assumptions this module depends on

Returned with every result, and rendered beside them:

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

## Module 07 — research datasets (`/api/datasets`)

Implemented. A dataset here is a **claim** about what a model was trained on, and
this module is what makes that claim checkable. It assembles rows out of stored
observations, weather and outcomes, reports on what it assembled before anyone
trains on it, and gives the result an identity that can be re-derived.

| Method | Path                              | Description                                    |
| ------ | --------------------------------- | ---------------------------------------------- |
| GET    | `/api/datasets/status`            | Module status + dataset and source counts      |
| GET    | `/api/datasets/catalog`           | Every feature, target and documented exclusion  |
| POST   | `/api/datasets/`                  | Build → `201` new, `200` already exists         |
| GET    | `/api/datasets/`                  | List manifests, filterable by target, paged    |
| GET    | `/api/datasets/{dataset_id}`      | Open one manifest with its report               |
| GET    | `/api/datasets/{dataset_id}/preview` | The rows as the CSV would write them         |
| GET    | `/api/datasets/{dataset_id}/export`  | The CSV, with both hashes in headers        |
| POST   | `/api/datasets/{dataset_id}/rebuild` | Re-derive and check the stored hash         |

`/status` and `/catalog` are declared before `/{dataset_id}`, so those literal
segments are never read as dataset identifiers.

### `GET /api/datasets/catalog`

Served without touching the database. A researcher should be able to read the
feature set, and what is deliberately not in it, before there is a single
observation to read them from.

Each of the 21 features names its `source_collection`, its `source_field`, the
`selection_rule` that picked the record, its `unit`, its `leakage_class`
(`as_of`, `static` or `future`), its `derivation`, its `scientific_basis` and
`nullable_reason`. The groups are:

| Source | Features |
| ------ | -------- |
| `pan_observations` at the anchor | `current_salinity_g_l`, `water_depth_cm`, `brine_temperature_c`, `observation_ambient_temperature_c`, `observation_humidity_percent` |
| `weather_observations`, observed and at or before the anchor | `weather_ambient_temperature_c`, `weather_humidity_percent`, `recent_rainfall_mm`, `recent_rainfall_record_count` |
| `weather_observations`, forecast in force at the anchor | `forecast_rainfall_mm`, `forecast_rainfall_probability_percent` |
| Previous observation, strictly before the anchor | `salinity_change_g_l`, `depth_change_cm`, `hours_since_previous_observation` |
| `salt_pans`, registered at or before the anchor | `pan_area_m2` |
| Module 06 mass balance, on the forecast in force | `physics_predicted_post_rain_salinity_g_l`, `physics_predicted_dilution_percent`, `physics_prediction_rainfall_source` |
| Calendar, from the anchor instant | `hour_of_day_utc`, `day_of_year`, `season` |

Observation and weather instruments are kept in separate columns and never
averaged together. `recent_rainfall_record_count` exists so a `0.0` rainfall sum
over an empty window cannot be read as a measurement of dry weather.
`physics_prediction_rainfall_source` is a constant column that makes an accidental
switch to recorded rainfall visible in the file itself.

Time features are included only where the physics justifies them: evaporation in
an open pan is driven by the solar cycle, so the hour of day and the annual
position carry real signal. `season` is a calendar quarter and says so — a monsoon
or regional season boundary would be a claim about a specific climate zone that
this project has no measured climatology to support.

The response also carries `leakage_policy`, `leakage_checks`, the `row_keys`, and
`excluded_fields` — the source fields deliberately left out, each with a reason and
a `reason_kind` of `future_measurement` or `row_identifier`. `actual_outcomes.
actual_rainfall_mm` and `actual_outcomes.post_event_salinity_g_l` are among them.

### `POST /api/datasets/`

```json
{
  "name": "post-rain salinity, 24 h horizon",
  "target": {
    "name": "post_rain_salinity_g_l",
    "horizon_hours": 24.0,
    "require_rain_event": false
  },
  "features": null,
  "lookback_hours": 72.0,
  "pan_ids": null,
  "start": null,
  "end": null,
  "include_unlabelled": true,
  "max_source_records": 20000,
  "preview_rows": 10
}
```

The response carries the manifest with its full `report`, whether anything was
written (`created`), a `reused_reason` when it was not, the `preview_columns` and
the `preview_rows`.

`created: false` with a `200` means an identical dataset already existed. The same
specification over the same stored records is the same dataset, and writing a
second document would give one dataset two identifiers.

`422` covers: an unknown target, an unknown feature name, a non-ascending or
misapplied `bin_edges`, a `horizon_hours` at or below zero, `start` later than
`end`, and an empty `features` list. A body with any unexpected field is `422` as
well, which is how the module refuses to accept a measurement.

### The six targets

There is no default. The label column, the kind and the bin-edge requirement are
all server-owned, so two callers asking for the same target cannot produce
differently shaped labels.

| Target | Kind | Label column | Needs `bin_edges` | Needs a rule |
| ------ | ---- | ------------ | ----------------- | ------------ |
| `post_rain_salinity_g_l` | regression | `label_post_rain_salinity_g_l` | no | no |
| `post_rain_depth_cm` | regression | `label_post_rain_depth_cm` | no | no |
| `salinity_change_g_l` | regression | `label_salinity_change_g_l` | no | no |
| `post_rain_salinity_band` | classification | `label_post_rain_salinity_band` | **yes** | no |
| `harvest_outcome` | classification | `label_harvest_outcome` | no | no |
| `operational_risk` | classification | `label_operational_risk` | no | **yes** |

`operational_risk` is the only target whose label is computed rather than
measured, so it is the only one that requires `risk_definition_id` in the build
request. See [Module 10](#module-10--risk-definitions-api-risk).

### The report

Returned with every build and stored on the manifest, because a report that
changes as readings arrive cannot support a reproducibility claim.

`record_count`, `labelled_count`, `unlabelled_count`, `pan_count`, `pan_ids`,
`date_range` (`first_anchor`, `last_anchor`, `span_days`), `missing_values` per
column with the dominant reason and a `coverage_warning`, `distributions` (numeric
with quartiles, or categorical with value counts), `target_distribution`
(`class_counts`, `class_percentages`, `imbalance_ratio`, `degenerate`,
`expected_classes`, `missing_classes`), `duplicates`, `data_sources`,
`leakage_audit`, `overlapping_target_groups`, `warnings` and `generated_at`.

`risk_distribution` is additionally present on a build whose target is
`operational_risk`, and `null` otherwise. It reports the rule's own class balance
(`classes` with each declared class, its count, percent, rank and description,
plus `imbalance_ratio`, `rarest_class`, `unobserved_classes`,
`populated_class_count`, `severely_imbalanced`, `label_basis`, `warnings` and
`note`). It exists because `target_distribution` cannot do this job: its
`expected_classes` come from the target catalogue, which has never heard of a
researcher's severity vocabulary, so a class the rule declared and no row landed
in would be invisible there. The rule's warnings are also appended to the
report's own `warnings`, so the dataset and the rule cannot disagree about whether
it is worth fitting.

`labelled_count + unlabelled_count == record_count` is enforced on the model.
Unmeasured outcomes are kept and counted rather than dropped, so the cost of the
measurement backlog stays visible; `include_unlabelled: false` turns the build into
a training file.

Statistics over no values are `null` with an `empty_reason`, never `0.0`. The
`warnings` list states the things a researcher should decide about — a dataset
below 50 rows, columns missing for more than 40% of rows, and columns holding one
distinct value across every row.

### Leakage

The causal rule, served with every dataset:

> A row is anchored at one pan observation. Every feature is read from a record
> that existed at or before that instant, and the target is read from a
> measurement taken at or after the anchor plus the configured horizon. No
> post-anchor measurement, and no identifier, is ever a feature.

`pan_id` and `anchor_timestamp` are row keys, never features: an identifier in the
training set lets a model memorise per-pan outcomes and then be scored on the same
pans.

The builder bounds every as-of lookup explicitly and then re-checks each row it
produced. A build that fails the audit is **refused with a 500 naming the
offending rows**, not stored with a warning attached — at that point the correct
response is to fix the builder rather than to publish the data.

### Version and hash

`content_hash` is a SHA-256 over the builder schema version, the hashed
specification and the canonical rows. It is the dataset's identity and is what an
experiment should cite. `version` counts the distinct datasets built for a target
name in creation order.

`include_unlabelled` and `max_source_records` are inside the hash, because both
change which rows exist. `name` is not, so renaming a dataset does not make a new
one.

The rows themselves are never stored — a snapshot would go stale the moment an
operator backfilled a reading, and a stale snapshot looks reproducible without
being so. Preview and export re-derive from current records and report the rebuilt
hash beside the recorded one with a `drift` flag.

`POST /{dataset_id}/rebuild` returns `200` when the stored hash still holds, and
`201` when it does not, in which case a successor is stored at `version + 1` with
`supersedes_content_hash` set and the predecessor left readable.

### `GET /{dataset_id}/export`

`text/csv`, with the target name and short hash in the filename. Three headers
carry the provenance, because the body is the file:

| Header | Meaning |
| ------ | ------- |
| `X-Content-Hash` | digest of the rows in this file |
| `X-Recorded-Content-Hash` | digest stored on the manifest |
| `X-Dataset-Drift` | `true` when the two differ |

An empty cell means the value was never recorded; `0` means it was. The
`missing_features` column says which, per row.

## Module 08 — ML training pipeline (`/api/ml`)

Training is **synchronous**. `POST /api/ml/train` fits and scores in the request
and returns the registered model version. There is no job queue and no polling
endpoint: a refused dataset is an answer the caller can act on immediately, and a
background job would only defer the same refusal behind a status that a caller
then has to poll. If that trade-off ever needs revisiting, it is a change to this
module's contract, not a hidden behaviour change.

Three targets are trainable, all classification. A regression target is refused
by name with 422 rather than silently coerced to a classifier.

### `GET /api/ml/`

```json
{
  "module": "ml", "number": 8, "implemented": true,
  "candidates": ["logistic_regression", "decision_tree", "random_forest", "gradient_boosting"],
  "split_strategies": ["chronological", "grouped_by_pan", "random", "per_pan_chronological"],
  "supported_targets": ["harvest_outcome", "operational_risk", "post_rain_salinity_band"],
  "selection_rule": "Highest macro F1 among automatically selectable candidates. Random Forest is always fitted and scored, and never selected unless the request sets allow_random_forest.",
  "insufficient_data_message": "Insufficient labelled data for reliable model training.",
  "trained": false,
  "endpoints": { "train": "POST /api/ml/train", "...": "..." }
}
```

`trained` is `false` until a caller trains through this module; it reports that
this endpoint does not train on being read, not whether a registry is empty.
`GET /api/ml/experiments` is what answers the latter.

### `POST /api/ml/train`

| Field | Type | Default | Notes |
| ----- | ---- | ------- | ----- |
| `dataset_id` | string | required | the stored dataset to fit |
| `name` | string \| null | `null` | an optional human label for the model, stored on the model version and reported on a prediction as `model_name`. Purely for display: nothing resolves a model by it, and `model_id` is always reported beside it |
| `split_strategy` | string | `chronological` | one of the four below |
| `test_size` | float | `0.2` | held-out fraction |
| `random_seed` | int | `20260926` | recorded on the model version |
| `allow_random_forest` | bool | `false` | permits RF to win; see below |

Returns `201` with the reproducibility record: dataset id/version/content hash,
target, feature names, split strategy and details, `candidate_metrics` (every
candidate's held-out scores, not just the winner's), the selected `algorithm`,
its `selection_reason`, seed, library versions, `training_duration_seconds`, and
`artifact_path`.

#### Split strategies

| Strategy | What it answers |
| -------- | --------------- |
| `chronological` | Train on the earliest rows, test on the most recent. The default: the only one that respects the order time imposes on a forecast. |
| `grouped_by_pan` | Hold out whole pans. Answers "does this work on pans it has never seen?" Not stratified — the held-out pans bring whatever balance they bring. |
| `random` | Stratified random holdout. Answers "how well would this fit this population?" Optimistic if rows from one pan are correlated, which they are. |
| `per_pan_chronological` | Cut time within each pan. Every pan contributes to both sides, so this does **not** answer the unseen-pan question. |

A fraction inside `(0, 1)` can still be unusable for a given dataset — 0.999
leaves no training rows. That is 422 with `refusal: "invalid_request"` and a
sentence naming the problem, not a 500.

#### Refusals

All four are 4xx, name themselves in `refusal`, and write no artifact and no
registry row.

| `refusal` | Status | Meaning |
| --------- | ------ | ------- |
| `insufficient_data` | 422 | The data cannot support a model. Fixable by gathering data. |
| `dataset_drift` | 409 | The rows no longer hash to the manifest's value. Rebuild the dataset. |
| `unsupported_target` | 422 | A regression target. This module is classification-only. |
| `invalid_request` | 422 | A split that cannot be made, or an unknown strategy. |

`insufficient_data` carries the required sentence —

```
Insufficient labelled data for reliable model training.
```

— followed by the specific counts that failed, so the answer says what to collect
rather than just that there is not enough. Drift is checked by re-deriving the
rows and comparing the current content hash to the manifest's: a version number
citing training data that has since changed would be a false provenance claim.

#### Random Forest is fitted, scored, and never auto-selected

RF is one of four candidates and is always fitted and scored, so its performance
is visible. It is withheld from automatic selection, because with a 21-feature
tabular dataset it is the candidate that wins most often while being the hardest
to explain and the most likely to fit pan-specific quirks. `allow_random_forest:
true` permits it to be selected deliberately, and the run records that it was.

Selection is macro F1 across the automatically selectable candidates, ties broken
by balanced accuracy and then by fixed candidate order. Macro F1 rather than
accuracy because these targets are heavily imbalanced, and an accuracy figure on
a 95/5 split is a statement about the majority class.

### `GET /api/ml/experiments`

Registered experiments, newest first, optionally filtered by `target`. Returns
`{count, experiments[], available_targets, split_strategies}`. The two extra
lists are included so a client can populate a filter without hardcoding the
vocabulary this module happens to support.

### `GET /api/ml/experiments/{model_id}`

The full stored record for one model version, including candidate metrics and
provenance. 404 when the id is unknown.

### A missing dataset is a 404, not a refusal

Training an id that was never registered is 404 with no `refusal` field. The four
refusals above all mean "this dataset exists and the answer is no"; a nonexistent
id is a different thing, and reporting it as a data refusal would report a
sufficiency judgement that was never made.

## Module 09 — model evaluation (`/api/evaluation`)

This module scores a model that Module 08 already registered. It does not train,
it does not select a different model, and it has no opinion beyond what the
stored record supports.

### `GET /api/evaluation/`

```json
{
  "module": "evaluation", "number": 9, "implemented": true,
  "comparison_columns": ["accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc"],
  "selection_rule": "The selected row is the one Module 8's rule selected: ...",
  "metric_representation": "Every metric is a MetricReading: value, computed, variant, reason.",
  "evaluated": false,
  "endpoints": { "run": "POST /api/evaluation/run", "...": "..." }
}
```

`evaluated` reports whether *this module* has stored a report yet. It is not
derived from the model registry: a trained model that has never been scored
through this module is `false` here, and that is the honest answer.

### The metric reading

Every figure in every response is a reading, never a bare number.

```json
{ "value": 0.874, "computed": true, "variant": null, "reason": null }
{ "value": null, "computed": false, "variant": null, "reason": "ROC-AUC is undefined: the holdout contains one class." }
{ "value": 0.0, "computed": true, "variant": "zero_division", "reason": "Applied the zero_division=0 convention: no rows carried this class." }
```

The three states are distinct on purpose and the contract forbids collapsing them:

| State | `computed` | `value` | Reads as |
| ----- | ---------- | ------- | -------- |
| Measured | `true` | the number | a figure, including a genuine `0.0` |
| Convention | `true` | the number | a figure whose meaning is narrower than it looks — `variant` and `reason` say how |
| Absent | `false` | `null` | no figure exists, and `reason` says why |

`value: 0` with `computed: false` is a contract violation. It is what a client
produces by substituting a default, and it is indistinguishable from a model that
scored zero once it is in a table or a CSV.

### `POST /api/evaluation/run`

| Field | Type | Default | Notes |
| ----- | ---- | ------- | ----- |
| `model_id` | string | required | the registered model version to score |
| `cross_validation_splits` | int \| null | `null` | a fold *count* only, bounded by the server's `MIN_SPLITS`/`MAX_SPLITS` |

`extra="forbid"`, so there is no field in which a caller could submit a metric
alongside the request to be scored. The CV *design* is derived from the model's
split strategy and is never a caller's choice: a caller who could name the design
could shuffle a time-ordered dataset until the score improved.

Returns `201` on a first run and `200` when an existing report for the same
`(model_id, version)` was replaced, with `recomputed: true`. One model version has
one report; re-running replaces it rather than adding a second row that would
leave "how good is this model" with more than one answer.

The response is the full `EvaluationDetailResponse`: the selected model's
`evaluation`, a `candidate_evaluations` entry per fitted classifier, the
`comparison` table, the `cross_validation` report, and a `provenance` block
carrying the dataset id, version and content hash, the feature names, the split
strategy and details, the seed, the library versions, and `evaluated_at`.

#### Refusals

| `refusal` | Status | Meaning |
| --------- | ------ | ------- |
| `dataset_drift` | 409 | The records no longer hash to the value the model was fitted against. Re-train. |
| `artifact_unavailable` | 409 | The fitted model is not on this host. Restore it, or re-train. |
| `not_evaluable` | 422 | The model exists but cannot be scored as it stands. |
| `invalid_request` | 422 | Unknown field, empty `model_id`, or a fold count out of range. |

`dataset_drift` and `artifact_unavailable` are both 409 because neither is fixed
by retrying, and the UI says which of the two applies rather than showing one
generic failure. A missing `model_id` is 404 with no `refusal`, matching Module
08: "this does not exist" is a different answer from "this exists and the answer
is no".

### `GET /api/evaluation/metrics`

Everything stored, plus every registered model — the two lists are separate on
purpose, because a picker built only from scored models would hide a trainable
model behind a decision to score it. A model that exists is not evidence that it
has been evaluated, and `evaluable_models[].evaluated` says which is which.

```json
{ "count": 1, "evaluations": [ /* EvaluationSummary */ ], "evaluable_models": [ /* registered models */ ] }
```

### `GET /api/evaluation/{model_id}/metrics`

One stored report in full, as `POST /run` returns it. 404 when the id is unknown
or has never been scored.

### `GET /api/evaluation/compare?model_id=...`

The stored comparison table for one model version. `model_id` is a query parameter
rather than a path segment because the registry's key is `model_id` and the
version lives on the record — a caller holding an id is not also asked to know
which version it resolved to.

`columns` holds the six metric keys; the model leads the table as a separate
field on each row, not as a seventh column.

```json
{
  "columns": ["accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc"],
  "column_titles": { "f1": "F1 (macro)", "pr_auc": "PR-AUC" },
  "rows": [{
    "model": "logistic_regression", "algorithm": "logistic_regression",
    "model_id": "...", "version": "...",
    "selected": true, "auto_selectable": true,
    "selection_note": "Eligible for selection under the documented macro-F1 rule.",
    "evaluated_rows": 40, "evaluation_set": "held-out test split",
    "classes": ["low", "high"], "class_distribution": { "low": 20, "high": 20 },
    "metrics": { "f1": { "value": 0.874, "computed": true, "variant": null, "reason": null } },
    "cross_validation": null
  }],
  "row_count": 4,
  "selected_algorithm": "logistic_regression",
  "selection_criterion": "The selected row is the one Module 8's rule selected: ...",
  "average_precision_note": "Precision, recall and F1 are macro-averaged. PR-AUC is ...",
  "cross_validation_performed": true,
  "comparable": { "same_row_count": true, "same_evaluation_set": true, "caveats": [] },
  "conventions_in_force": [],
  "does_not": "This table does not rank by any score of its own, does not call a row best, ..."
}
```

Three things in that payload exist to keep the table from being decorative.
`selection_criterion` is Module 08's rule quoted rather than restated, so the two
cannot drift apart. `comparable.caveats` says whether the rows share a
denominator. `does_not` states the limits in the response itself, so a client that
renders only the numbers still receives them.

`column_titles` is the authority on what each column is called. "F1 (macro)" and
"F1" are not the same claim, and a client that keeps its own copy of the headings
will eventually drop the word that says the figure averages over classes.

### `GET /api/evaluation/compare/versions`

The same six columns across model versions. `comparison` is `null` when the rows
do not share a row set and a count, and `not_comparable` then carries the reasons.
The endpoint does not substitute a table of its own: ranking figures from
different held-out sets would report the difference between the datasets as a
difference between the models.

### `GET /api/evaluation/{model_id}/export?format=json|csv`

| `format` | Contents |
| -------- | -------- |
| `json` | the complete stored record |
| `csv` | the comparison table, one row per candidate |

The CSV is flattened from the same readings the API serves, with four columns per
metric — `f1`, `f1_computed`, `f1_variant`, `f1_reason` — so an empty cell is
interpretable from the file alone. There is no second formatting of the numbers
for the export: an export that rounded, or that wrote an absent metric as `0`,
would be a second source of truth for a figure the table already states.

### Reading a row

`evaluated_rows` and `evaluation_set` travel with every row and every figure. A
figure whose denominator is hidden is a figure that will be quoted without it:
four rows scored on nine is not the same claim as four rows scored on nine
thousand, and the difference is not visible in the number.

## Module 10 — risk definitions (`/api/risk`)

This module owns the *label*, not the prediction. It exists because a risk model
is only as defensible as the severity vocabulary it was fitted to, and
`high`/`medium`/`low` written into a training script cannot be revised without
invalidating every model fitted to it, nor cited as anything but a constant. So
the vocabulary is data: a researcher writes their own classes, thresholds and
reasons, and this module versions them.

### `POST /api/risk/predict`

Operational risk prediction, answered by the deployed model version and by nothing
else. See [Module 17](#module-17--model-registry-and-deployment-apimodels) for the
gate; the short version:

```json
{ "pan_id": "pan-salt-01" }
```

`as_of` is optional. Omitted, the prediction is made from the pan's **current
digital twin state**, which is the ordinary operational question. Given, the newest
twin state recorded at or before that instant is used, so a prediction anchored in
the past is made from what the system believed then; an instant earlier than the
pan's first recorded state is refused rather than filled with today's view.

The request carries no features, no window, no model id and no algorithm. Every
column the model declared is read from that twin state, in the model's own order,
so a caller cannot ask for a score from a model it names, cannot hand the model a
number it would otherwise have had to look up, and cannot cause a row to be built
any other way than the one the model was fitted on.

```json
{
  "prediction_id": "pred-9f2c…",
  "pan_id": "kzn-2026-a01",
  "predicted_at": "2026-03-14T12:05:11Z",
  "as_of": "2026-03-14T12:00:00Z",
  "risk_class": "act now",
  "probability": 0.8214,
  "class_probabilities": { "watch": 0.1786, "act now": 0.8214 },
  "defined_classes": ["watch", "act now"],
  "model_id": "model-7c1a…",
  "model_name": "monsoon dilution ranker",
  "model_version": "1.0.0",
  "algorithm": "logistic_regression",
  "deployment_id": "dep-31ab…",
  "deployed_at": "2026-03-14T11:00:00Z",
  "input_snapshot_id": "sha256:9d1f…",
  "twin_id": "twin-2f8b…",
  "twin_as_of": "2026-03-14T12:00:00Z",
  "risk_definition_id": "rd-monsoon-dilution",
  "risk_definition_version": 1,
  "features_used": ["current_salinity_g_l", "water_depth_cm", "forecast_rainfall_mm"],
  "features_absent": ["recent_rainfall_mm", "hours_since_previous_observation"],
  "features_imputed": ["recent_rainfall_mm", "hours_since_previous_observation"],
  "used_physics_prediction": true,
  "input_source": "current_digital_twin_state"
}
```

| Field | Meaning |
| --- | --- |
| `probability` | The model's own figure for `risk_class`, as `predict_proba` produced it. Never derived from the rule and never rounded to a stand-in value. `null` only when the deployed estimator reports no per-class probabilities, in which case the class is still the model's own and `class_probabilities` is empty |
| `model_name` | The researcher's optional label for the model, set at training time. `null` when none was given; `model_id` and `model_version` are always present and are what the registry resolves |
| `predicted_at` | When the model ran. Distinct from `as_of`, which is the instant of the state it read |
| `input_snapshot_id` | The content hash of the stored `input_snapshot`, so a stored figure can be checked against the state it claims to describe. Two predictions from the same state by the same model share an id |
| `features_absent` | Declared features the twin could not supply. They are **imputed by the model's own fitted preprocessor**, never substituted from a neighbouring column, and listed so a reader can see how much of the row was real |
| `used_physics_prediction` | Whether Module 06's mass balance ran for this row, from the twin's brine state and the forecast it carries |

| Situation | Response |
| --- | --- |
| A version is deployed for `operational_risk` and the pan has a twin state | `201` with the class, the probabilities, the model's provenance, the input snapshot id and the risk rule the labels came from |
| Nothing is deployed | `409`, `refusal: no_deployed_model` |
| Deployed for a different target only | `409`, `refusal: no_deployed_model` — a model fitted to salinity is not a risk model, and deployment is per target |
| The pan has no twin state, or none at or before `as_of` | `409`, `refusal: no_twin_state` |
| Every declared feature would be imputed | `409`, `refusal: no_observed_features` |
| The model declares a column measured *after* the anchor | `409`, `refusal: feature_not_knowable_at_prediction_time` |
| The model declares a column this project does not define | `409`, `refusal: feature_mismatch` |
| The model emits a class the rule does not declare | `409`, `refusal: model_not_fitted_to_risk_rule` |
| Persistence not configured | `503` |

Every `201` writes a stored prediction before the response is sent, carrying the
serving `model_id`, `model_version`, `deployment_id` and `deployed_at`, the class,
the probability, the full input snapshot and its content hash — so a stored risk
number can always be traced to the version that produced it and the state it read.
There is no approximate answer: with nothing deployed, this module refuses rather
than falling back to the newest version, the best-scoring version, or any
rule-based approximation.

`GET /api/risk/` reports the gate under `prediction_gate` and
`capabilities.risk_prediction: implemented_gated_on_deployment`.

### `GET /api/risk/variables`

The eleven measurable quantities a rule may reference, each with its unit, source
collection and field, the moment it was knowable (`known_at_anchor`), how it is
derived and what its null means. Served from the same catalogue the write path
validates against, so what is published is what is enforced. `harvest_outcome` is
the only one not knowable at the anchor and is listed in
`retrospective_variables`.

Also returns the shared imbalance policy: `severe_imbalance_ratio` (10.0) and
`rare_class_minimum_rows` (5).

### `POST /api/risk/definitions`

Writes a rule. Request body is the definition draft:

| Field | Meaning |
| ----- | ------- |
| `name` | The rule's identity. Versions are numbered per name. |
| `classes` | `name`, `rank`, `description`. Ranks must be contiguous from 0 and there must be at least two. |
| `criteria` | `variable`, `operator`, `threshold`, `label`, `rationale` (required), optional `unit`/`lower_bound`/`upper_bound`. |
| `combinator` | `highest_rank` (default) or `first_match`. |
| `unmatched_label` | Optional class for rows no criterion matched. Not applied to rows the rule could not read. |
| `operational_rationale` | Why this rule exists for this operation. Required. |
| `authored_by` | Who wrote it. |
| `label_column` | Optional. Defaults to `label_operational_risk`. |

The client never supplies `definition_id`, `version`, `specification_hash`,
`label_basis` or `variables_used`. The server computes the hash from the stored
content, derives the basis from the variables, and reads the version from the
stored history.

Responses:

- **201** — a new version was written.
- **200** — an identical rule already existed; `created: false`,
  `reason: "identical_definition_already_stored"`, and the existing document. A
  version that changes no label is a revision nobody made.
- **422** — a criterion names a variable outside the catalogue, compares in the
  wrong unit, orders a categorical value, assigns an undeclared class, or omits a
  rationale. Each would produce a class distribution that is a fact about the
  mistake rather than about any pan.

There is no update or delete. A rule that can be edited in place leaves every
model fitted to it undescribable.

### Reading rules back

| Endpoint | Returns |
| -------- | ------- |
| `GET /api/risk/definitions` | Stored rules, newest first. Optional `name`, `limit`, `skip`. |
| `GET /api/risk/definitions/{id_or_hash}` | One rule, by `definition_id` **or** `specification_hash` — a manifest cites the hash, so reading back the way a manifest names it is the check that it still describes a rule that exists. |
| `GET /api/risk/definitions/name/{name}/versions` | One rule's whole history, oldest first, so a reader can see what was revised. |
| `GET /api/risk/targets` | How this module meets Module 07: `operational_risk` is the rule-backed target. |

`GET /api/risk/definitions/{definition_id}/distribution` — see below.

### `POST /api/risk/definitions/{definition_id}/distribution`

The endpoint the module exists for. Derives this rule's labels across the stored
records and reports their shape **before anything is fitted**.

Body: `horizon_hours` (required), plus optional `pan_ids`, `start`, `end`,
`lookback_hours`, `max_source_records` and `require_rain_event` — the same
selection the Module 07 build request takes, so a distribution describes exactly
the rows a build from the same definition would produce.

The rows are produced by the ordinary Module 07 builder with an `operational_risk`
target pointing at this definition, so these are the same labels, with the same
per-row provenance and the same causal audit, a dataset built from this definition
would carry. Asking for the distribution is not a shortcut around the audit.

Response: the definition, the distribution, `record_count`,
`is_usable_for_training`, `truncated_collections` and `trained_model` — which is
always `null`, so a reader does not have to wonder whether asking triggered a fit.
Nothing is stored.

The distribution reports `classes` (every declared class, including one nothing
landed in, with count, percent, rank and description), `labelled_count`,
`unlabelled_count`, `imbalance_ratio` (null when a declared class holds no rows,
because a ratio against zero is not a number), `rarest_class`,
`most_common_class`, `unobserved_classes`, `populated_class_count`,
`severely_imbalanced`, `label_basis`, `warnings` and `note`.

`is_usable_for_training` is false in four separate ways, because they are four
different problems: nothing was labelled; a declared class received no row, so
nothing can be measured against it; everything landed in one class; or the split
is too lopsided or too thinly populated for a per-class figure to mean anything.
An unobserved class disqualifies on its own — a 50/50 split between two of three
declared classes passes every numeric test, which is exactly the case where the
report would look healthy while being silent on a third of the rule's vocabulary.

### Building a dataset from a rule

`operational_risk` is a Module 07 target. A build names the rule:

```json
{
  "target": {
    "name": "operational_risk",
    "horizon_hours": 24.0,
    "risk_definition_id": "risk-…"
  },
  "features": ["current_salinity_g_l", "water_depth_cm"]
}
```

The definition is resolved once and passed into the build together with the
target, so the rule that was checked against the request is provably the rule
that labels the rows. Every row then records `risk_definition_id`,
`risk_definition_version`, `risk_specification_hash`, `risk_label_basis`,
`risk_source_variables`, `risk_matched_criteria` and `risk_unlabelled_reason`,
and those columns are written to the exported CSV next to the label.

Refusals:

- `operational_risk` with no `risk_definition_id` — **422**. The module supplies
  no severity vocabulary, so a build without a rule would have a class
  distribution that is a fact about this module rather than about the pans.
- A rule reference on a measured target — **422**. A measured label is not
  attributable to a rule that did not produce it.
- A rule reading `harvest_outcome` without `allow_retrospective_label: true` —
  **422**, naming the variable. A retrospective label that looks prospective will
  be read as a forecast, and the build is the last place that can be caught.
  Acknowledged, the dataset is labelled `retrospective` throughout.

The causal audit changes shape for a derived label: "the target was measured at
or after the horizon" is meaningless for a label no measurement produced, so it is
replaced by a per-variable check that every variable the rule read was knowable at
the anchor.

A fallback class is withheld on any row the rule could not fully read. The
fallback means "no criterion fired", which is only a statement the rule can make
about a row it managed to read; assigning it to rows whose data was too thin to
evaluate would put an assumed severity exactly where one is least defensible.
Those rows stay unlabelled and say so.

## Module 11 — SHAP explainability (`/api/explain`)

Implemented. Values come from the `shap` library applied to the registered
artifact, or the response is a named refusal. There is no fallback to
correlations, to a model's coefficients, or to a permutation of this module's
own — each of those produces a ranked list that looks like a SHAP attribution
and is not one.

### `GET /api/explain/`

Module status: whether the module is implemented, whether anything has been
explained, the explained output, the stored-explanation count, and the quantity
contract in prose.

```json
{
  "module": "explain",
  "number": 11,
  "implemented": true,
  "explained": true,
  "stored_explanations": 12,
  "explained_output": "probability",
  "quantity_contract": "Three quantities, reported apart and labelled apart. …",
  "note": "Values come from the shap library applied to the registered artifact, or there is no response. It will not estimate an importance by correlation …"
}
```

### `POST /api/explain/predictions/{prediction_id}`

Explains one stored prediction from its own stored input row, and stores the
attribution against the prediction and the model version. **201** when a new
explanation was stored, **200** when a stored one was returned or refreshed — one
explanation per prediction, enforced by a unique partial index, because the
arithmetic is deterministic and a second row would be a second answer to a
question already answered.

```json
{
  "explanation_id": "exp-7d1c…",
  "prediction_id": "pred-5a72…",
  "pan_id": "kzn-2026-a01",
  "model_id": "model-e658…",
  "model_version": "v1.0.0",
  "model_name": null,
  "algorithm": "logistic_regression",
  "kind": "local",
  "created_at": "2026-09-27T05:41:00.331992Z",
  "target_class": "act now",
  "input_snapshot_id": "sha256:d3bc…",
  "twin_id": "twin-fcfd…",
  "twin_as_of": "2026-09-27T05:41:00.324955+00:00",
  "quantities": {
    "model_probability": {
      "label": "Model probability",
      "value": 0.9973736271460203,
      "unit": "probability",
      "note": "The model's own output for the predicted class…"
    },
    "shap_contribution": {
      "label": "SHAP contribution",
      "unit": "probability points",
      "base_value": 0.6666628775119087,
      "explained_output": "probability",
      "target_class": "act now",
      "note": "Each value is how far that one feature moved the probability…"
    },
    "physical_simulation": {
      "label": "Physical simulation result",
      "unit": "as named by each quantity",
      "results": { "physics_predicted_post_rain_salinity_g_l": 231.869 },
      "note": "Module 06's mass balance for this pan and forecast, as held in the input row. It is an input the model read, not an output of the model, and it is not a SHAP contribution."
    }
  },
  "model_probability": 0.9973736271460203,
  "base_value": 0.6666628775119087,
  "attribution_error": 1.11e-15,
  "method": "permutation_explainer",
  "approximate": true,
  "shap_version": "0.52.0",
  "background": {
    "source": "training_split_of_the_fitted_dataset",
    "rows": 96,
    "width": 4,
    "train_rows": 96,
    "holdout_rows": 24,
    "selection": "first_rows_of_each_split_in_split_order"
  },
  "attributions": [
    {
      "feature": "current_salinity_g_l",
      "feature_value": 280.4,
      "shap_value": 0.24884615396727303,
      "direction": "increases_risk",
      "source": "twin.estimated_salinity_g_l",
      "source_kind": "digital_twin_state",
      "imputed": false
    },
    {
      "feature": "physics_predicted_post_rain_salinity_g_l",
      "feature_value": 231.86923076923077,
      "shap_value": -0.07634010352341893,
      "direction": "decreases_risk",
      "source": "module_06.mass_balance",
      "source_kind": "physical_simulation",
      "imputed": false
    }
  ],
  "top_increasing": [ "…positive contributions, largest first, at most 5…"],
  "top_decreasing": [ "…negative contributions, largest first, at most 5…"],
  "is_simulated": false
}
```

Field notes:

- `shap_value` is in **probability points**, not probability, and carries its
  sign. `base_value` plus the contributions equals `model_probability`; the
  residual measured is `attribution_error`, and an attribution that does not
  reconcile within `1e-3` is refused rather than stored.
- `method` is `tree_explainer` (exact) or `permutation_explainer` (estimated).
  `approximate` is always present, so an estimated value is never presented as a
  computed one.
- `attributions` carries **one row per declared feature, in the model's own
  order**, including a feature whose contribution is exactly zero. `top_increasing`
  and `top_decreasing` are the two sign-ranked lists the UI shows; anything they
  cap out is still in `attributions`.
- A feature the twin could not supply has `source_kind: "imputed"`,
  `imputed: true` and **`feature_value: null`**. The preprocessor's training
  median is not a measurement of this pan, so no value is reported beside it.
- `background` is the reference distribution the values were measured against. It
  travels with the artifact; it is never chosen at explain time.
- `is_simulated` is always `false`. There is no code path that produces a
  simulated attribution, and the flag exists so that a future one could not be
  introduced quietly.

### `GET /api/explain/predictions/{prediction_id}`

The stored explanation, without computing one. **404** `no_such_prediction`;
**409** `explanation_not_stored` when the prediction exists but has no stored
attribution — a different answer from "cannot be computed", because a client that
cannot tell them apart retries a request that cannot succeed.

### `GET /api/explain/models/{model_id}/versions/{version}/importance?rows=25`

The global half: the mean absolute contribution per feature over the **held-out**
rows Module 09 scored, one row per feature, ranked, stored under an id derived
from the model version so the same version gives the same ranking.

`rows` is a budget, not a promise: the response reports `rows_explained`, the
count actually used, which is smaller when the holdout split is. `422` above 50.

```json
{
  "explanation_id": "exp-global-model-e658…-v1.0.0",
  "kind": "global",
  "explained_output": "probability",
  "rows_explained": 24,
  "explained_classes": { "act now": 16, "watch": 8 },
  "method": "permutation_explainer",
  "approximate": true,
  "shap_version": "0.52.0",
  "background": { "source": "held_out_split_scored_by_module_09", "rows": 24, "…": "…" },
  "measure": {
    "label": "Mean absolute SHAP contribution",
    "value": null,
    "unit": "probability points",
    "note": "…a ranking of influence on this model's probabilities — not a causal claim, and not a model coefficient."
  },
  "features": [
    { "feature": "current_salinity_g_l", "mean_abs_shap": 0.23681588347229196, "rank": 1 },
    { "feature": "water_depth_cm", "mean_abs_shap": 0.1411383444964889, "rank": 2 }
  ]
}
```

### `GET /api/explain/models/{model_id}/versions/{version}/explanations`

The stored explanations for one model version, local and global, newest first:
id, kind, prediction, pan, target class, explained output, base value,
attribution error, method, `approximate`, shap version, rows explained,
`created_at` and `is_simulated`.

### Refusals

Each names the quantity that is missing rather than reporting a failure. The body
is `{ "detail", "refusal", "model_id", "prediction_id" }`.

| Refusal | Status | What is missing |
| --- | --- | --- |
| `no_such_prediction` | 404 | The prediction. |
| `no_such_model_version` | 404 | The registered model version, so the number has no model to be attributed to. |
| `explanation_not_stored` | 409 | A stored attribution. `POST` will compute one. |
| `artifact_unavailable` | 409 | The fitted model file on this host. Refitting a stand-in would attribute a number to a model that never produced it. |
| `no_reference_distribution` | 409 | The reference sample. A Shapley value is defined against one. |
| `model_reports_no_probability` | 409 | A probability to decompose. The class is passed on unchanged; the decision margin is not substituted. |
| `input_snapshot_mismatch` | 409 | A row that hashes to the recorded snapshot id. |
| `class_not_in_artifact` | 409 | The predicted class among the artifact's classes. |
| `feature_mapping_unavailable` | 409 | The fitted pipeline's output-column names, without which one-hot columns cannot be grouped back to features. |
| `attribution_does_not_reconcile` | 409 | Arithmetic: the contributions and the probability do not describe the same computation. |

A `shap_computation_failed` notice is also possible on the *prediction* response,
where an unexpected error in this module is reported as
`explanation_status: "unavailable"` rather than failing the prediction. The
prediction's own class and probability are unaffected.

### Relationship to Module 10

`POST /api/risk/predict` explains every prediction it makes and returns
`explanation_status`, `explanation_id`, `explanation_refusal` and
`explanation_refusal_detail` alongside the class and probability. Explainability
is an *addition* to that answer and never a condition of it.

## Module 12 — decision-support engine (`/api/decisions`)

Implemented. A recommendation is a claim that a specific pan, in a specific
recorded state, under a specific written rule, warranted a specific action. This
module holds no thresholds and no default action of its own: every action it
recommends comes from a rule that was written through this API, is stored with a
content hash and a version, and can be read back.

The action vocabulary is closed at four values — `HARVEST NOW`,
`TRANSFER BRINE`, `PROTECT / DRAIN`, `WAIT AND RECHECK`. Which one a pan gets is
a function of the rule. A model contributes an estimate that a criterion may read;
it never contributes an action.

| Method | Path                                        | Description                                 |
| ------ | ------------------------------------------- | ------------------------------------------- |
| GET    | `/api/decisions/`                           | Module status, action vocabulary, refusals  |
| GET    | `/api/decisions/variables`                  | The closed set of readable variables        |
| POST   | `/api/decisions/rules`                      | Write a rule version → `201`, `200` on reuse |
| GET    | `/api/decisions/rules`                      | List rules, newest first                    |
| GET    | `/api/decisions/rules/reference-draft`      | A writable rule with no threshold of its own |
| GET    | `/api/decisions/rules/{rule_id}`            | Open one rule version                       |
| GET    | `/api/decisions/rules/name/{name}/versions` | One rule's full history, oldest first      |
| POST   | `/api/decisions/advise`                     | Recommend an action, with its evidence      |
| GET    | `/api/decisions/{pan_id}/latest`            | The most recent recommendation for a pan    |
| GET    | `/api/decisions/log`                        | The advice log, newest first                |

### `GET /api/decisions/`

The action vocabulary, the catalogue and the refusal codes are all reachable
from here as data, so a client renders them from the server rather than keeping
its own copy of a list that can drift.

### `GET /api/decisions/variables`

The closed set of variables a criterion may name, and where each one's value
comes from. A rule naming anything else is refused with 422. This is what keeps a
rule from reading a quantity the twin cannot supply.

| Variable                              | Source         | Unit |
| ------------------------------------- | -------------- | ---- |
| `current_salinity_g_l`                | twin           | g/L  |
| `current_depth_cm`                    | twin           | cm   |
| `forecast_rainfall_mm`                | twin           | mm   |
| `rainfall_probability_percent`        | twin           | %    |
| `target_harvest_salinity_g_l`         | registration   | g/L  |
| `simulated_post_rain_salinity_g_l`    | simulation     | g/L  |
| `simulated_salinity_drop_g_l`         | simulation     | g/L  |
| `simulated_post_rain_depth_cm`        | simulation     | cm   |
| `predicted_risk_class`                | prediction     | —    |
| `predicted_risk_probability`          | prediction     | —    |
| `flood_depth_cm`                      | registration   | cm   |
| `dilution_salinity_g_l`               | registration   | g/L  |
| `critical_rainfall_24h_mm`            | registration   | mm   |
| `target_flow_cm`                      | registration   | cm   |

The last four are the operator's own configured limits, read from the pan's
`risk_thresholds` rather than measured. They are catalogued so a rule can name one
as its **bound** — see `bound_variable` below. Each can be null, because a pan may
have no limit configured for it, and a rule naming a null limit is reported as
unevaluable rather than compared against a default.

### `GET /api/decisions/rules/reference-draft`

A complete, writable rule that **contains no threshold of its own**. Every
criterion names its bound with `bound_variable`, so the comparisons are made
against limits the operator configures per pan:

```json
{
  "variable": "simulated_post_rain_salinity_g_l",
  "operator": "lte",
  "bound_variable": "target_harvest_salinity_g_l",
  "action": "HARVEST NOW",
  "rationale": "Harvest once the salinity the mass balance predicts ... is at or below the target this operator set for the pan."
}
```

It is served as a **draft** and is never written into storage on startup. A default
rule that appeared by itself could not be revised without every recommendation
already issued silently changing its basis. `POST` it, or edit it first:

```bash
curl -s localhost:8000/api/decisions/rules/reference-draft \
  | curl -s -X POST localhost:8000/api/decisions/rules \
      -H 'content-type: application/json' -d @-
```

A pan with one of the referenced limits unconfigured will not be advised: the
criteria that need it are unevaluable and the fallback is withheld. That is the
intended behaviour, and it is why no limits are filled in here.

### `POST /api/decisions/rules`

A rule is a ranked list of criteria, written as:

```json
{
  "name": "wet-season harvest ladder",
  "authored_by": "an agronomist",
  "operational_rationale": "Turn the physics and, where a model has spoken, its estimate into one of four actions an operator can carry out this afternoon, and record which rule version said so.",
  "actions": [
    { "action": "WAIT AND RECHECK", "rank": 0, "description": "Nothing decisive yet.", "priority": "low" },
    { "action": "TRANSFER BRINE", "rank": 1, "description": "Still too strong to sell.", "priority": "medium" },
    { "action": "HARVEST NOW", "rank": 2, "description": "At or below the harvest salinity.", "priority": "high" },
    { "action": "PROTECT / DRAIN", "rank": 3, "description": "Too deep to hold the rain.", "priority": "high" }
  ],
  "criteria": [
    {
      "variable": "simulated_post_rain_salinity_g_l",
      "operator": "lte",
      "threshold": 250.0,
      "unit": "g/L",
      "action": "HARVEST NOW",
      "rationale": "The brine is at or below the harvest salinity and can be taken."
    },
    {
      "variable": "simulated_post_rain_salinity_g_l",
      "operator": "gte",
      "threshold": 296.0,
      "unit": "g/L",
      "action": "TRANSFER BRINE",
      "rationale": "The brine is above what the buyer will take."
    },
    {
      "variable": "simulated_post_rain_depth_cm",
      "operator": "gt",
      "threshold": 20.0,
      "unit": "cm",
      "action": "PROTECT / DRAIN",
      "rationale": "The pan is deeper than it can hold, so water must be released."
    }
  ],
  "combinator": "highest_rank",
  "unmatched_action": "WAIT AND RECHECK",
  "uses_ml": false
}
```

> **The figures in the example above are illustrative.** `296.0` and `20.0` are
> placeholders chosen to show the shape of a rule. They are not thresholds this
> project recommends, and nothing in the code uses them. In a real rule, prefer
> `bound_variable` (below) so the bound comes from the operator's configuration
> rather than from a number typed into a rule.

A criterion takes its bound in one of two ways, and **exactly one** of them:

- `threshold` — a literal. Numeric for a numeric variable, a quoted class name for
  a categorical one.
- `bound_variable` — a catalogue variable to read the bound from at evaluation
  time. Used with the four operator-configured limits above, this is what lets a
  rule carry no threshold of its own:

  ```json
  {
    "variable": "simulated_post_rain_salinity_g_l",
    "operator": "lte",
    "bound_variable": "target_harvest_salinity_g_l",
    "action": "HARVEST NOW",
    "rationale": "Harvest once the predicted salinity reaches the target this operator set for the pan."
  }
  ```

  The bound is resolved per pan, so one written rule serves pans that disagree
  about their targets, and the recommendation records both the resolved figure and
  the field it came from (`criteria[].threshold` and `criteria[].bound_variable`).
  A bound that cannot be resolved — an unconfigured limit — makes the criterion
  unevaluable and withholds the fallback, exactly as a missing input does.

Supplying both, or a `bound_variable` naming an uncatalogued variable, a
categorical variable, or a variable in a different unit from the one being
compared, is refused with 422 at write time rather than producing a comparison
nobody intended.

A rule is a ranked list of criteria. Each criterion names a catalogue variable,
an operator, a bound and the action it recommends, with a `rationale` a human
wrote. `combinator` says how a rule resolves when several criteria match:
`highest_rank` takes the most severe action, `first_match` takes the first that
fired. `rank` is severity, and a rule's ranks must run 0 to *n*-1 with no gaps
or repeats, so two actions can never claim the same precedence and `highest_rank`
always has a unique answer. `unmatched_action` is the action for a pan where none matched, and it is
withheld rather than applied when a criterion's variable could not be read — see
the `rule_undecided` refusal below.

`operational_rationale` is the author's own statement of what the rule is for,
and it is stored on every version. It is a claim to be read, not a justification
the engine evaluates.

**Versions are immutable and content-hashed.** Posting a rule whose criteria are
identical to a stored version returns `200` with `created: false` and
`reason: "identical_rule_already_stored"` rather than `201`, because a rule
that reads the same as one already stored is the same rule and versioning it
again would make the history longer without making it more honest. A changed
criterion allocates the next version and records `supersedes_rule_id`.

A rule is refused, not stored, when it names an uncatalogued variable, states a
unit the catalogue contradicts, orders a categorical variable, gives a numeric
variable a string threshold, declares `uses_ml` inconsistently with whether any
criterion reads the prediction, or lists two criteria for one variable without
resolving the order. The refusal arrives at write time rather than as an unusable
input on the day it mattered.

### `POST /api/decisions/advise`

The body is a pan and, optionally, an instant, a rule version and a what-if
rainfall depth. There is no way to send features, an action or a model: those are
the server's to read, and a recommendation assembled from caller-chosen values
would be indistinguishable from one assembled from the twin, the stored model and
the written rule.

| Field          | Type     | Notes                                                     |
| -------------- | -------- | --------------------------------------------------------- |
| `pan_id`       | string   | Required; matched case-insensitively, stored lowercased    |
| `as_of`        | datetime | Omit for the pan's current twin state                     |
| `rule_id`      | string   | Omit to apply the most recently written rule              |
| `rainfall_mm`  | number   | A what-if depth used instead of the twin's forecast        |

The seven contract fields are `recommendation`, `reason`, `inputs_used`,
`simulation_result`, `ml_result`, `rule_version` and `timestamp`. Around them
are the provenance fields that make the answer checkable: `basis`, `notice`,
`twin`, `matched_criteria`, `rule_id`, `rule_name`, `rule_version_number`,
`specification_hash` and `recommendation_id`.

**Four kinds of evidence, four separate fields.** The twin state, the mass
balance, the stored model estimate and the rule's own matching are never merged
into one inputs table. Each carries its own identifier and its own source, and
every entry in `inputs_used` names where its value came from. This is the
structural reason a simulated salinity cannot be read as a measured one.

**The mass balance is Module 06's, run and stored.** The result carries
`run_id`, so the arithmetic can be replayed from the stored `SimulationRun`
rather than taken on trust, and `is_ml: false` is present explicitly so no
consumer has to infer that no model was involved. Its assumptions and disclaimer
travel with the number.

**The model estimate is read, never produced.** The engine looks up the latest
stored prediction for the pan's operational-risk target and copies it. It does
not score anything: asking for advice does not train, deploy or run a model, and
a model that was fitted for the first time by this request would make the
module's own figures look like independent evidence. `probability` and
`confidence` are copied as the model produced them, and are `null` for a
class-only model with `probability_is_null_reason` saying so.

**There is no confidence on the recommendation.** A rule that matched is either
decisive or it is not. There is no recommendation-level `confidence`, no
percentage and no strength in the response, because such a number would be read
alongside `ml_result.confidence` and imply a likelihood nobody computed.

**A recommendation without a model estimate is a complete answer.** It carries
`basis: "physics_and_rules"`, `ml_result: null`, and a `notice` beginning with
the project's exact required label:

> Physics/rule-based recommendation — ML prediction unavailable.

The rest of the notice says which of the three reasons applies: no prediction is
stored, the rule declares no prediction variable so the estimate was not read, or
the model reported a class with no probability.

**A stale estimate is flagged, not hidden.** `matches_recommendation_snapshot`
is false when the prediction was made from a different twin state than this
recommendation is based on. The estimate is still shown, because the rule read
it, and the mismatch is stated rather than left for a reader to discover.

### `GET /api/decisions/{pan_id}/latest` and `GET /api/decisions/log`

`latest` returns `found: true` with the recommendation, or `found: false` with
`advice: null` for a pan nobody has asked about yet — a normal state, and one a
client needs to tell apart from a failure. `log` is the advice log, ordered by
issue time, narrowed by `pan_id` and `action`. An `action` outside the closed
vocabulary is a 422 rather than an empty list, because a caller who typed
`HARVEST` instead of `HARVEST NOW` should be told rather than shown a
confidently empty log.

### Refusals

| Code                      | Status | Meaning                                                                |
| ------------------------- | ------ | ---------------------------------------------------------------------- |
| `no_twin_state`           | 409    | The pan has no state, or none at or before `as_of`; the physics starts from the twin |
| `simulation_inputs_missing` | 409  | The mass balance needs area, a water depth, a salinity and a rainfall depth |
| `no_rule_defined`         | 409    | No rule has been written, and this module holds no default of its own    |
| `rule_undecided`          | 409    | Every input was read, no criterion matched, and no fallback could be supported |
| `insufficient_evidence`   | 409    | An input the rule needs could not be read at all |

Each refusal body carries the `code`, so a client can branch without matching on
prose. All five describe a state of the system rather than a mistake in the
request, so none of them is worth retrying.

`insufficient_evidence` covers two causes, and the `detail` names which input was
missing in each case:

- an **unreadable input** — most often an operator limit left unconfigured. No
  value is substituted for it, any criterion needing it is not compared, and the
  declared fallback is withheld rather than absorbing a pan whose evidence is
  thin;
- **evidence too old** — the newest reading is older than the operator's
  `max_observation_age_hours`. The check runs before the physics, so a rule is
  never evaluated "correctly about the wrong hour".

The staleness cause is raised only when `max_observation_age_hours` is configured.
A pan with no limit set is **not** refused on age — this project has no basis for
a freshness window — and the recommendation says so in its `notice` rather than
letting the absence of a refusal read as a check that passed.

The two are kept apart from `rule_undecided` because the remedy differs, and the
difference tells the operator where to look: an unread input is a gap in the pan's
configuration or records, while a rule that read everything and matched nothing is
a gap in the rule. Reporting both the same way sends them to the wrong place.

## Module 14 — operator feedback (`/api/feedback`)

Implemented. A decision is a record of what a person did with a recommendation, not
a verdict on whether they were right: the module never scores the operator, and
nothing here is an observation. It exists so the trace does not end at the advice.
There is no update or delete route.

A recommendation is decided **once**. A second decision for the same
`recommendation_id` is refused with `409`, and a unique index on
`operator_decisions.recommendation_id` is the backstop for two operators deciding
the same advice at the same moment. That race is why the index exists: the
service's own pre-check is two separate operations from its insert, so two
concurrent requests can both see the advice as undecided. Both paths raise the
same code, so a client branches once.

| Method | Path                        | Description                                 |
| ------ | --------------------------- | ------------------------------------------- |
| GET    | `/api/feedback/status`      | Module status + live counts                 |
| POST   | `/api/feedback/`            | Record one decision → `201` with the record |
| GET    | `/api/feedback/`            | List decisions, filtered and paged          |
| GET    | `/api/feedback/{decision_id}` | Open one decision                         |

### `POST /api/feedback/`

`recommendation_id` is the only identifier the client supplies. The pan and the
recommended action are read from the stored recommendation, so a record cannot be
filed against a different pan than the advice it answers.

| Field               | Type                                          | Notes                              |
| ------------------- | --------------------------------------------- | ---------------------------------- |
| `recommendation_id` | string                                        | Required; must resolve             |
| `decision`          | `accepted \| modified \| rejected`             | Required                           |
| `actual_action`     | closed action vocabulary \| null               | Required for modified and rejected |
| `operator_id`       | string \| null                                | ≤ 64 chars                         |
| `notes`             | string \| null                                | ≤ 2000 chars                       |
| `timestamp`         | ISO-8601                                      | Optional; defaults to now          |

`actual_action` is what was **really done**, which is not always what was advised.
Omitted with an accepted decision, it is recorded as the recommended action
rather than left null, so an export never has to ask what acceptance meant.
A modified or rejected decision must name its own action, because the record
exists to say how the operator departed from the advice. The schema sets
`additionalProperties: false`, so a client cannot attach its own action vocabulary.

### The record is self-describing

What was advised, and by which rule, is **copied onto the decision at the moment it
is recorded** rather than left as a join to the recommendation:

```json
{
  "decision_id": "decision-…",
  "recommendation_id": "rec-…",
  "pan_id": "pan-demo-01",
  "decision": "modified",
  "recommended_action": "HARVEST NOW",
  "actual_action": "WAIT AND RECHECK",
  "rule_name": "Operator-configured harvest and flood rule",
  "rule_version": 2,
  "specification_hash": "…",
  "is_demonstration": true,
  "demonstration_sources": ["observation_data_source", "weather_forecast_source"],
  "operator_id": "m.ndlovu",
  "notes": "Waited: the reading is a worked example, not field data.",
  "timestamp": "…",
  "created_at": "…",
  "recommendation": { "…": "the live join, for the deeper trace" }
}
```

The copy exists because recommendations are versioned, superseded and pruned. A
record that kept only a foreign key would lose the meaning of the advice — and
with it the only means of telling an acceptance apart from a coincidence — the
moment the document it points at disappeared.

`is_demonstration` is copied from the recommendation's twin for the same reason,
and an operator making a real decision about a worked example is a record worth
keeping. What must not happen is that decision exported beside real ones with
nothing to tell them apart, so an export reads the flag rather than inferring it
from source strings.

The field is nullable, and `null` is a **third value, not `false`**: it means the
record does not say, because it predates the flag or because the advice is gone.
Reads resolve it against the live recommendation when that is still available, and
report `null` when it is not. `false` would be a positive claim about provenance
that no surviving document supports, which is precisely what the field exists to
stop.

### A missing recommendation does not invalidate a decision

`recommendation` in the response is a live join for the deeper trace — the twin,
observation, simulation and rule identifiers behind the advice — and it is `null`
when that document no longer exists. That costs the reader the extra trace
identifiers and nothing else: the disposition, the action taken, the rule version
and the provenance are all on the decision itself.

A decision outlives the advice it answers, and a restore or retention policy can
take the advice away. Refusing the read would let a document the decision does not
own destroy the record of a person's judgement, and would fail the **whole
listing** with a `409` describing none of the other decisions in it, when a single
unaffected row is all that is wrong.

### Refusals

| Code                          | Cause                                          |
| ----------------------------- | ---------------------------------------------- |
| `recommendation_unknown`      | No stored recommendation has that ID           |
| `recommendation_not_a_decision` | It named no action — there is nothing to decide |
| `decision_already_recorded`   | One disposition per recommendation, by check or by index |

## Module 15 — actual-outcome recording (`/api/outcomes`)

Implemented. An outcome is the ground truth Module 16 validates the model
against, so it is append-only: there is no update or delete route. A second entry
for the same `(pan_id, event_id)` is refused with `409` rather than overwriting
the first measurement, because one rainfall event measured twice is still one
event, and counting it twice would quietly distort every mean. A unique index on
`(pan_id, event_id)` is the backstop for two concurrent writes. To correct a
wrong measurement, record the event afresh under a new `event_id`.

| Method | Path                         | Description                                   |
| ------ | ---------------------------- | --------------------------------------------- |
| GET    | `/api/outcomes/status`       | Module status + live counts                   |
| POST   | `/api/outcomes/`             | Record one event → `201` with its comparisons |
| GET    | `/api/outcomes/`             | List recorded events, filtered and paged      |
| GET    | `/api/outcomes/comparison`   | Aggregate error over the comparable events    |
| GET    | `/api/outcomes/{outcome_id}` | Open one event                                |

`/comparison` is declared before `/{outcome_id}` in the router, so the literal
segment is never read as an outcome identifier.

### `POST /api/outcomes/`

The body carries measurements only:

| Field       | Type     | Notes                              |
| ----------- | -------- | ---------------------------------- |
| `pan_id`    | string   | Required; must be a registered pan |
| `event_id`  | string   | Required, ≤ 64 chars               |
| `timestamp` | ISO-8601 | Optional; defaults to now          |
| `forecast_rainfall_mm`   | number \| null                      | 0 – 1000                           |
| `actual_rainfall_mm`     | number \| null                      | 0 – 1000                           |
| `pre_event_salinity_g_l` | number \| null                      | 0 – 500                            |
| `post_event_salinity_g_l`| number \| null                      | 0 – 500                            |
| `pre_event_depth_cm`     | number \| null                      | 0 – 1000                           |
| `post_event_depth_cm`    | number \| null                      | 0 – 1000                           |
| `harvest_outcome`        | `none \| partial \| full \| lost`   | Omitted ≠ `"none"`                 |
| `operator_action`        | string \| null                      | ≤ 2000 chars                       |
| `references`             | object \| null                      | Optional explicit links            |

`references` takes any of `observation_id`, `weather_forecast_id`,
`simulation_run_id` and `recommendation_id`. Each must resolve and belong to the
same pan (`404` otherwise), which is what stops an outcome being graded against
another pan's state. Omitted links are resolved from the event's own timestamp
instead.

An absent measurement and a measurement of zero are different things and are
stored differently. There is no `predicted_*` field, no error field and no
accuracy field, and the schema sets `additionalProperties: false`, so a client
cannot assert a prediction or an error of its own.

The response carries the three comparisons, the four link snapshots and the two
flags the frontend reads:

```json
{
  "outcome_id": "…",
  "pan_id": "kzn-2026-a01",
  "event_id": "storm-2026-03-14",
  "has_actual_measurement": true,
  "is_comparable": true,
  "simulation_error": {
    "predicted": 233.33333333333334,
    "actual": 255.0,
    "predicted_from": "actual_rainfall_mm",
    "predicted_from_label": "the rainfall that actually fell",
    "unit": "g/L",
    "signed_error": -21.666666666666657,
    "absolute_error": 21.666666666666657,
    "percentage_error_percent": -8.496732026143787,
    "percentage_error_reason": null,
    "is_comparable": true,
    "missing_inputs": [],
    "missing_fields": []
  }
}
```

Each comparison block repeats `predicted_from_label` and `unit` so the UI never
has to infer which quantity a number is in, and a link block is always present
with a `summary` explaining what the event was graded against, resolved or not.

### The three comparisons

| Comparison                  | Predicted by                      | Compared against    |
| --------------------------- | --------------------------------- | ------------------- |
| `simulation_error`          | mass balance on actual rainfall   | post-event salinity |
| `forecast_simulation_error` | mass balance on forecast rainfall | post-event salinity |
| `rainfall_forecast_error`   | — (forecast itself)               | actual rainfall     |

The prediction always comes from the server-side mass balance. The `SimulationRun`
behind it is adopted when its stored inputs match the event, and a fresh
reproducible run is persisted when they do not, so every error can be
re-derived from the record alone.

### What a missing measurement does

Nothing is filled in. A `null` measurement yields `null` errors, a `missing_inputs`
entry naming the field and the reason, and `is_comparable: false`. The model
validator at Module 16 excludes such events from its means; a value of `0.0`
would instead have claimed the model was exactly right.

`percentage_error_percent` is `null` when the actual is `0`, with a
`percentage_error_reason`, because the percentage is a ratio against a figure
that does not exist.

### `GET /api/outcomes/comparison`

Returns, for each of the three comparisons: `mean_absolute_error`,
`mean_absolute_error_reason`, `bias`, `bias_reason`, `largest_absolute_error`,
`coverage_percent`, and the `compared` / `not_comparable` counts the means were
drawn from.

```json
{
  "pan_id": null,
  "total_outcomes": 1,
  "simulation_error": {
    "label": "Salinity error, predicted from the rainfall that actually fell",
    "unit": "g/L",
    "compared": 1,
    "not_comparable": 0,
    "coverage_percent": 100.0,
    "mean_absolute_error": 21.666666666666657,
    "mean_absolute_error_reason": null,
    "bias": -21.666666666666657,
    "bias_reason": null,
    "largest_absolute_error": 21.666666666666657
  },
  "events_missing_actual_salinity": 0,
  "events_with_operator_action": 1
}
```

Where no event was comparable the mean is `null` and `mean_absolute_error_reason`
says so — never `0.0`. The counts are part of the statistic, not metadata: a mean
over two of twenty events is meaningless on its own.

## Module 16 — research validation (`/api/validation`)

Implemented. This is the module the whole pipeline leans on: every historical
prediction is graded against the *recorded* outcome, by the same rule it was made
under, so a metric can never claim more than the data measured. There are no
write routes — this is an audit surface, and reading it must not be able to
mutate it.

The five comparison families share one shape: a family is either `continuous`
(means over error) or `classification` (agreement over predicted vs. actual
labels):

| `comparison`                                  | kind             | Pair source                                   |
| --------------------------------------------- | ---------------- | --------------------------------------------- |
| `forecast_vs_actual_rainfall`                 | continuous      | forecast vs. measured rainfall, mm             |
| `simulated_vs_actual_salinity`                | continuous      | simulated salinity vs. measured, g/L           |
| `forecast_simulated_vs_actual_salinity`       | continuous      | salinity from the forecast vs. the measured    |
| `predicted_risk_vs_outcome`                   | classification   | risk rule re-applied to what actually happened |
| `recommended_vs_operator_action`              | classification   | advice given vs. the operator's disposition    |

| Method | Path                                                | Description                                        |
| ------ | --------------------------------------------------- | -------------------------------------------------- |
| GET    | `/api/validation/status`                            | Module status + registry counts                    |
| GET    | `/api/validation/overview`                          | All five families' totals, counts, gating           |
| GET    | `/api/validation/pairs?comparison=...`              | Every event in one family, compared or not          |
| GET    | `/api/validation/export?comparison=...`             | Download one family's rows as CSV or JSON           |

All routes accept optional `pan_id` (case-insensitive match, `[A-Za-z0-9._-]{1,64}`)
and `limit` (`1..1000`, default `1000`). `export` takes `output_format=csv|json`.
The four read routes are declared in one router with matching prefixes, so the
router registry reports the same endpoint list for the module no matter which
prefix the host mounts it under.

### Metrics are gated, not zero-filled

Each metric is a small object: `value`, `computed`, `reason`. When the data
cannot support the statistic the value is `null` and `computed` is `false`, with
the `reason` recording exactly what would have been missing — a mean over a
single event for RMSE, a constant set for R-squared, a class never predicted for
its precision. A value of `0.0` is reserved for a genuine zero. The sample sizes
travel with the figures (`n_compared`, `n_excluded`), and every excluded event
carries one of the fixed reasons: no stored prediction, no actual measured, not
evaluated by the in-force rule, no recommendation was in force, the action
dimension is not scored, or the event falls outside the chosen pan.

### `GET /api/validation/overview`

Reports scope totals plus each family block. A continuous block:

```json
{
  "key": "forecast_simulated_vs_actual_salinity",
  "title": "Forecast-simulated salinity vs actual salinity",
  "kind": "continuous",
  "unit": "g/L",
  "n_compared": 1,
  "n_excluded": 1,
  "excluded_reasons": [
    { "reason": "The post-event salinity was not recorded.", "count": 1 }
  ],
  "mean_absolute_error": { "value": 21.7, "computed": true, "reason": null },
  "root_mean_squared_error": {
    "value": null, "computed": false,
    "reason": "RMSE needs a spread of errors; a single comparable event gives only one."
  },
  "r_squared": { "value": null, "computed": false, "reason": "..." },
  "mean_bias_error": { "value": -21.7, "computed": true, "reason": null }
}
```

A classification block adds `predicted_distribution`, `actual_distribution`,
`accuracy`, `precision`, `recall`, `f1`, `roc_auc`, `pr_auc` (percent scale,
`computed` gating used), `per_class` rows (`label`, `support`, `predicted`,
`precision`, `recall`, `f1`), `confusion_matrix` (`labels`, `matrix[actual][predicted]`,
`classes_absent_from_evaluation`) when classes exist, and the `definition_id`,
`definition_name`, `definition_version` of the rule the classification was graded
under. `roc_auc`/`pr_auc` are `computed=false` for the action dimension, which has
no probability ranking.

### `GET /api/validation/pairs`

Newest first; each row is one event with its stored input, its measured actual,
the pair values, `compared` (`true`/`false`) and, for excluded events, the
`reason`. `limit` caps recorded events considered. An unknown `comparison` is
rejected with `422` — one client's typo must not silently broaden the report.

### Refusals the endpoints share

| Input problem          | Status | Body                                                  |
| ---------------------- | ------ | ----------------------------------------------------- |
| Unknown `comparison`   | 422    | Standard validation detail listing the accepted keys  |
| Unknown `output_format`| 422    | Standard validation detail listing `csv`, `json`       |
| Malformed `pan_id`     | 422    | Standard validation error                             |
| Backend unavailable    | 503    | `{ "detail": "..." }` with the registry reason        |

## Module 17 — model registry and deployment (`/api/models`)

This module is where a trained model stops being a file and becomes something an
operational system may be asked to trust with a real decision. It holds the
registry of model versions, the lifecycle that says which one is in service, and
the refusal to move that answer without somebody saying so.

A model version is not replaced when it is improved. Retraining writes a *new*
version, in `experimental`, and leaves the deployed one exactly where it is.

### The lifecycle

```
experimental ──▶ validated ──▶ deployed ──▶ retired   (terminal)
```

| Status | Who sets it | What it means |
| --- | --- | --- |
| `experimental` | `POST /api/ml/train`, always | Fitted, not measured, not in service |
| `validated` | `POST /api/models/{model_id}/validate` | A stored evaluation report exists and was read by a person |
| `deployed` | `POST /api/models/{model_id}/deploy` | The version answering operational requests for its target |
| `retired` | `POST /api/models/{model_id}/retire`, or by an acknowledged replacement | Out of service, permanently |

Transitions run forward only, one step at a time, and the table is closed: a status
added to the enum later is refused by default rather than quietly becoming
deployable. Both `validated` and `deployed` require a stored evaluation report, and
the report's id is written to the history entry.

### Reading the registry

`GET /api/models/` lists every version with its status, its stored evaluation and
its target definition:

```json
{
  "models": [ ... ],
  "total": 3,
  "deployed": [ ... ],
  "statuses": {"experimental": 1, "validated": 1, "deployed": 1, "retired": 0}
}
```

`GET /api/models/deployed` answers the question this module exists to make
answerable — *which model is answering operational requests right now* — without
paging through the registry. It reports the same four statuses in `statuses`, so
one renderer serves both.

`GET /api/models/status` is the module overview: the endpoint list, the lifecycle
table, the deployed set named outright, and a `guarantees` list stating each rule
above in the words of the module itself. A client reading only that response
learns the gate exists.

### One model record

`GET /api/models/{model_id}` returns everything a deployment decision rests on:

| Field | What it is |
| --- | --- |
| `model_id`, `version`, `algorithm`, `dataset_id`, `dataset_version` | Identity of the version and what it was fitted to |
| `trained_at`, `evaluated_at` | When each happened |
| `status` | The lifecycle position |
| `validated_at`/`_by`, `deployed_at`/`_by`, `retired_at`/`_by`, `lifecycle_reason` | Who decided each step and why |
| `superseded_by_model_id` / `superseded_by_version` | Set when a replacement retired this version |
| `hyperparameters`, `feature_names`, `split_strategy` | The configuration, reproduced from storage rather than restated |
| `target_definition` | The full target spec, including the risk rule's id, version and specification hash |
| `evaluation` | Module 09's report, with metrics as `MetricReading` objects |
| `blocked_reason` | Why this version cannot be deployed yet, when it cannot |

Metrics are the readings themselves — `value`, `computed`, `variant`, `reason` — and
`absent_metrics` names the ones that were not computed. A metric the pipeline could
not compute arrives as `value: null` with its reason. It is not filled with `0.0`
here, and it is not dropped, because a filled-in cell reads as a measurement
nobody made.

### Changing a version's status

Every lifecycle request names the **exact version**, an **actor** and a **reason**:

```json
POST /api/models/model-3f9a…/deploy
{ "version": "v1.0.0", "reason": "June season, beats v1.0.0 on PR-AUC", "actor": "s.rao" }
```

`version` is required rather than defaulted to the newest, because the newest
version is the one a retraining run has just written, and defaulting to it is how a
deployment ends up aimed at a model nobody looked at. `reason` is required and
non-empty, and enforced in three places — the request schema, the deployment
history record, and the model version — so it cannot be lost on the way to storage.

All three transitions answer `200` with the updated record and the history entry
the change appended.

### Replacing a deployed model

Deploying a second version for a target that already has one is refused with
`409` and the incumbent named, including the exact request that would make the
replacement deliberate:

```json
{
  "detail": "supersede_unacknowledged model-3f9a… v1.0.0 is already deployed …",
  "refusal": "supersede_unacknowledged",
  "model_id": "model-77c1…",
  "version": "v2.0.0",
  "deployed_incumbent": {
    "model_id": "model-3f9a…", "version": "v1.0.0", "algorithm": "gradient_boosting",
    "deployed_at": "2025-06-02T09:14:00Z", "deployed_by": "s.rao",
    "lifecycle_reason": "June season"
  }
}
```

To proceed, resend with `supersede: true` **and** a `supersede_reason`:

```json
{
  "version": "v2.0.0", "reason": "Second half season", "actor": "s.rao",
  "supersede": true, "supersede_reason": "v2.0.0 wins on PR-AUC over three months"
}
```

The incumbent is retired first, with that reason recorded against it and a
`superseded_by` pointer set, and then the new version is deployed. Both steps
appear in the history. `supersede: true` without a reason is still refused:
retiring a working model without recording why is the silent replacement this
module exists to prevent.

At most one version per target holds `deployed`. The service refuses a second, and
a unique partial index on `(target)` where `status = "deployed"` makes the cluster
refuse it too — the index is the backstop, not a substitute for the refusal.

### `GET /api/models/{model_id}/history`

The append-only record of what happened to this model version, in order: each
entry's `action`, `from_status`, `to_status`, `actor`, `reason`, `evaluation_id`,
`selection_criterion` and, for a displacement, the `superseded_*` pair. Nothing in
the collection is ever updated or deleted, so the history cannot be edited into a
decision that was never made. A history document that claims a step the lifecycle
does not allow — a validated step with no evaluation, a step that does not change
the status, half a substitution naming a model but no version — is refused by the
model, not stored.

### Refusals

| `refusal` | Status | When |
| --- | --- | --- |
| `no_such_model_version` | 404 | The `model_id` and `version` do not name a registered version |
| `not_evaluated` | 409 | No stored evaluation report, naming the endpoint that produces one |
| `illegal_transition` | 409 | The step is not permitted from the version's current status, naming the permitted ones |
| `supersede_unacknowledged` | 409 | A deployed incumbent would be displaced without `supersede` and a reason |
| `no_deployed_model` | 409 | *(Module 10)* Nothing is deployed for the requested target |
| `no_twin_state` | 409 | *(Module 10)* The pan has no twin state, or none at or before the requested `as_of` |
| `no_observed_features` | 409 | *(Module 10)* Every declared feature would have been imputed, so the output would describe the training set rather than the pan |
| `feature_not_knowable_at_prediction_time` | 409 | *(Module 10)* The model declares a column measured after the anchor |
| `feature_mismatch` | 409 | *(Module 10)* The model declares a column this project does not define, or one the pipeline excludes |
| `model_not_fitted_to_risk_rule` | 409 | *(Module 10)* The model's label space and the risk rule's have diverged |
| `risk_rule_unavailable` | 409 | *(Module 10)* The rule the model was fitted to will not resolve, or the fitted dataset cannot be read |
| `artifact_unavailable` | 409 | *(Module 10)* The registered artifact is missing or unloadable on this host |

Every refusal body carries `refusal`, `detail` and the `model_id` it concerns, so a
client can branch on the code and still show a person a sentence that says what to
do next.

## Module 19 — research experiment ledger (`/api/experiments`)

Implemented. Every completed run is recorded with everything that produced it —
the dataset by Module 07 identity (`dataset_version` and `dataset_hash`), the
observations and pans it spanned, the date range, the feature set, the target and
its definition version, the train/test strategy, the seed, the model, its
hyperparameters, the metrics and the software versions — and that record is what
another researcher re-derives the result from.

Three absences are deliberate and load-bearing.

**There is no update and no delete.** An experiment is a claim to have measured
something; editing or removing it would quietly rewrite the claim that was made.
Correcting a run means recording a new experiment, and the old one stays.

**Results are never overwritten.** The identity is `content_hash` — `sha256` over
a canonical `sorted-keys-stable-json` encoding of everything the researcher
submitted, computed by the service and never accepted from the request — so
re-posting an identical experiment returns the stored record with
`created=false` and HTTP 200 rather than writing a second row. Two runs that
differ in any recorded respect (including `notes`) hash differently and are two
experiments. A unique index on `research_experiments.content_hash` makes the
cluster refuse the duplicate even if the idempotent write were bypassed.

**The manifest is read off the record, never stored separately.** `GET
/.../{id}/manifest` serialises exactly the bytes the hash was computed over, so
verifying the ledger is re-deriving `content_hash` from `record` — the two cannot
drift.

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET  | `/api/experiments/status` | Module status; `experiments.available=false` instead of a 5xx when the database is down |
| POST | `/api/experiments/` | Record one finished run; 201 on new, 200 `created=false` on identical re-post |
| GET  | `/api/experiments/?model=&target=&dataset_hash=&limit=&offset=` | The ledger, newest first |
| GET  | `/api/experiments/export?output_format=csv\\|json&...` | Download the screened ledger |
| GET  | `/api/experiments/{experiment_id}` | One experiment, exactly as recorded |
| GET  | `/api/experiments/{experiment_id}/manifest` | The self-contained reproducibility document |
| GET  | `/api/experiments/{experiment_id}/export?output_format=csv\\|json` | One experiment's results |

### `POST /api/experiments/`

The request carries the fields of a finished run; nothing derived — no
`experiment_id`, `content_hash`, `timestamp` or `manifest_version` — may be
supplied, and unknown keys are refused (`extra="forbid"`). `dataset_hash` must be
the Module 07 form `sha256:<64 lowercase hex>`. Nested facts
(`hyperparameters`, `metrics`, `software_versions`) are objects keyed by name.
`date_start` must not be later than `date_end`.

The response is `{created, reason, experiment}` where `experiment` is the stored
`ExperimentRead` (with `experiment_id` `exp-...`, `content_hash`, `short_hash`,
`timestamp` and `manifest_version`). `reason` is present exactly when
`created=false`, saying the record was already in the ledger.

### Refusals

| `refusal` / outcome | Status | When |
| --- | --- | --- |
| Validation error | 422 | Any field malformed — a bad hash pattern, a backwards date range, a missing feature |
| Identical re-post | 200 | The experiment was already recorded; the stored row is returned with `created=false` |
| Index collision (race) | 200 | A concurrent insert claimed the same hash first; the winner's row is returned with `created=false` |
| Write retries exhausted | 409 | Pathological in-flight contention for the same content hash |
| Unknown `experiment_id` | 404 | `GET /{id}` and `/manifest` and `/export` for a hash no row carries |
| Unknown `output_format` | 422 | `export` accepts only `csv` or `json` |

### `GET /api/experiments/{experiment_id}/export`

`json` streams the reproducibility manifest — the file a researcher files as the
record of the run. `csv` is the metrics table, one metric per row, for pasting
into a results table.

### `GET /api/experiments/export`

Downloads the whole screenable ledger, one experiment per row. In the CSV form
the nested facts (`features`, `hyperparameters`, `metrics`,
`software_versions`) are encoded per row as JSON strings so a widely shaped
ledger stays rectangular. The JSON form carries the full `ExperimentRead`
records with keys sorted. Both accept the same `model`/`target`/`dataset_hash`
filters and a `limit` (default 1000). The endpoint is declared before
`/{experiment_id}` so the literal path is never captured as an experiment
identifier.

## Module placeholder endpoints

Every not-yet-built module router exposes `GET <prefix>/` with the same
contract:

```json
{
  "module": "explain",
  "module_number": 11,
  "status": "not_implemented",
  "description": "SHAP explainability for a trained model.",
  "planned_endpoints": ["POST /api/explain/...", "..."]
}
```

These are scaffolding. The listed endpoints are **planned** and return 501
until their module is built; nothing is returned that could be mistaken for a
real measurement.

## CORS

The backend allows `FRONTEND_ORIGIN` (default `http://localhost:3000`) as the
single browser origin mirroring `NEXT_PUBLIC_API_BASE_URL`.
