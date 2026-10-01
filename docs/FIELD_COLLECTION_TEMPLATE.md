# Field collection template and protocol

This is the missing half of SaltLens. Every public dataset found during real
data acquisition was either the wrong physical system (a weather station, an
estuary) or lacked the one thing the project actually needs: a recorded
**post-event outcome**. The only way to obtain genuine SaltLens training and
evaluation data is to collect it on site, with a fixed protocol.

The header-only CSVs in `data/templates/` are the exact column sets the
ingestion pipeline accepts. Copy them into `data/raw/<your_dataset>/`,
keep one row per measurement, and run the commands in
[Loading collected data](#loading-collected-data).

## Rules that make the data usable

1. **One row per pan per instant.** A row is a reading about exactly one pan at
   exactly one timestamp. Never one row per visit covering several pans.
2. **Use the canonical column names, never the short aliases.** The catalog
   also accepts `depth`, `salinity`, `rh`, `temp` and similar, and during
   acquisition one of those aliases silently corrupted real data — a
   `depth` column in metres was accepted as `water_depth_cm` with no warning.
   Canonical names plus the unit suffix in the name itself make that class of
   mistake impossible.
3. **Units are part of the column name.** `_g_l`, `_cm`, `_mm`, `_c`, `_percent`,
   `_m2`. Never convert in your head; enter the unit the header demands.
4. **Leave a cell empty when you did not measure it.** An empty cell is stored as
   missing and reported as missing. It is never filled with zero, an average, or
   the last reading. A missing outcome lowers the number of comparable events,
   which is the correct and honest outcome.
5. **`0` is a measurement.** Record `0` for no rain, or `none` for a harvest
   that did not happen. That is different from an empty cell.
6. **Timestamps are UTC, ISO 8601**, e.g. `2024-10-03T14:20:00Z`. Record the
   time you read the instrument, not the time you typed the row.
7. **Instrument and unit provenance go in the notes** column or the dataset
   README. Record the make, model, serial, last calibration date and any
   correction applied.

## The event is the unit of study

A rainfall event links a pre-event state, a forecast, the rain that fell, and a
post-event outcome. The `event_id` is the join key that makes those four records
one story. Use a stable, human-readable form such as `2024-10-03-P03-E01`.

```
   event E01 at pan P03
   |
   +-- pans.csv         P03 registered once: area, design depth, target salinity
   |
   +-- observations.csv PRE-event state, timestamped before the rain starts
   |                   salinity_g_l, water_depth_cm, brine_temperature_c,
   |                   humidity_percent, ambient_temperature_c
   |
   +-- weather.csv      forecast retrieved BEFORE the event, timestamped at
   |                   retrieval and forecast_for set to the valid instant
   |
   +-- weather.csv      actual rain, entered after the event with
   |                   rainfall_mm from a gauge, is_forecast false
   |
   +-- outcomes.csv     POST-event state and what the operator did
                       actual_rainfall_mm, post_event_salinity_g_l,
                       post_event_depth_cm, harvest_outcome,
                       operator_action
```

An event with a pre-event state but no outcome is still worth recording. It is
the record of a prediction that was never graded, and it must not be deleted to
make a report look tidier.

## What to measure, and how

### Salinity — `salinity_g_l`

Measure with a calibrated refractometer or a salinity meter reading in PSU or
g/kg, and **convert to g/L explicitly** before entering it. Seawater is close to
1 PSU ≈ 1 g/kg ≈ 0.99 g/L; a concentrated brine is not, so use the instrument's
own density correction when it reports density. Record the instrument and the
conversion in the notes column. Target the pan's operating range, typically tens
to a few hundred g/L, against a hard accepted range of 0–500 g/L.

A reading of `28500` is rejected. If you get that, the instrument reported ppm,
mg/L or a percentage. Fix the unit, not the validator.

### Water depth — `water_depth_cm`

Measure standing brine depth from the pan surface, not sensor deployment depth
and not the surveyed basin depth. Use a graduated staff or a sounder and record
centimetres. Accepted range 0–1000 cm. A reading of `3.9` on a 40 cm pan almost
certainly means metres, and it will be stored as 3.9 cm unless you convert it.

### Brine temperature — `brine_temperature_c`

In the liquid, away from the wall and the surface. This is *not* ambient air
temperature; record the latter separately. Accepted range −20 to 120 °C.

### Humidity — `humidity_percent`

Relative humidity in the air above the pan, from a hygrometer or a weather
station within a few kilometres. 0–100 %. This is a measured field only if you
measured it. Computing it from air temperature and dew point is a derivation,
and derived values are refused for measurement collections.

### Ambient air temperature — `ambient_temperature_c`

Shaded air temperature at pan height, away from direct sun. −50 to 80 °C.

### Rainfall — `rainfall_mm`

Prefer a tipping-bucket or weighing gauge at the site. If you read a public
forecast or a regional product instead, record which product in the notes, and
record it as a forecast with `forecast_for` set. Forecast and observed rainfall
are different quantities and the schema keeps them apart:

- `rainfall_mm` with no `forecast_for` is a measured or asserted event total.
- `rainfall_mm` with `forecast_for` set is a forecast for that instant.

A forecast of `0` mm is a real prediction of no rain. A missing rainfall figure
is an empty cell. Do not convert one into the other.

### Harvest outcome — `harvest_outcome`

One of exactly `none`, `partial`, `full`, `lost`. A recorded `none` is an
observation that nothing was harvested; an empty cell means the outcome is
unknown. This single column is what turns a measurement set into labelled
research data, and it is the one column that cannot be reconstructed after the
fact.

### Operator action — `operator_action`

Free text, up to 2000 characters: what was actually done. Transferred to another
pan, stopped evaporation, opened the outlet, pumped, nothing.

## Dataset layout on disk

```
data/raw/field_campaign_2024_autumn/
  README.md                 provenance, instruments, calibration, permissions
  pans.csv                  one row per pan, filled once
  observations.csv          every measured reading, all visits
  weather.csv               forecasts and observed rainfall
  outcomes.csv              one row per event
```

`README.md` must state: who collected it, when, with which instruments and
calibration dates, the units of every column, the permissions under which it is
shared, and anything unusual that happened during the campaign.

## Loading collected data

Each file family is audited and validated separately, in this order. `pans` must
land first because every other record references `pan_id`.

```bash
cd <repo>
PY=backend/.venv/bin/python

$PY -m ml.ingestion audit    --raw data/raw/field_campaign_2024_autumn --kind pans
$PY -m ml.ingestion validate --raw data/raw/field_campaign_2024_autumn --kind pans \
                             --provenance FIELD_MEASURED

$PY -m ml.ingestion audit    --raw data/raw/field_campaign_2024_autumn --kind observations
$PY -m ml.ingestion validate --raw data/raw/field_campaign_2024_autumn --kind observations \
                             --provenance FIELD_MEASURED

$PY -m ml.ingestion audit    --raw data/raw/field_campaign_2024_autumn --kind weather
$PY -m ml.ingestion validate --raw data/raw/field_campaign_2024_autumn --kind weather \
                             --provenance FIELD_MEASURED

$PY -m ml.ingestion audit    --raw data/raw/field_campaign_2024_autumn --kind outcomes
$PY -m ml.ingestion validate --raw data/raw/field_campaign_2024_autumn --kind outcomes \
                             --provenance FIELD_MEASURED
```

Read the audit output before validating. If it reports `unmapped_columns`, your
headers do not match the schema: fix the headers rather than letting the
pipeline drop the columns. Only `PUBLIC_MEASURED`, `FIELD_MEASURED` and
`SENSOR_MEASURED` may be stored; `PHYSICS_DERIVED`, `SIMULATED` and `UNKNOWN`
are refused.

Write nothing to the database until every audit is clean. `validate` writes only
to `data/processed/`. Importing is a separate, explicit, later step, and
`import` is dry-run by default so it reports what it would do without doing it.

## Illustrative shape — not data

The block below shows how the records of one event relate. Every value is
invented for illustration and **must not be imported**. It exists to show the
key relationships, not to suggest magnitudes.

```
pans.csv
  P03,Pan 3,North basin,36.7000,-2.9000,5000,40,280,"{}"

observations.csv
  P03,2024-10-03T06:00:00Z,275.4,38.0,19.2,61.0,18.4,pre-event
  P03,2024-10-04T09:00:00Z,,36.5,,,,post-event follow-up

weather.csv
  P03,2024-10-02T18:00:00Z,2024-10-03T12:00:00Z,8.0,70.0,17.9,64.0
  P03,2024-10-04T08:00:00Z,,24.0,,,

outcomes.csv
  P03,2024-10-03-P03-E01,2024-10-04T09:00:00Z,8.0,275.4,38.0,24.0,238.0,34.5,partial harvest,partial
```

Note the relationships: the outcome's `pre_event_*` values repeat the pre-event
observation so the comparison is self-contained; `actual_rainfall_mm` comes from
the gauge, not from the forecast; and the post-event observation is a separate
row rather than an overwrite.
