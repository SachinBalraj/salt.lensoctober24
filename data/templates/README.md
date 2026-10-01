# Field campaign <name> — <start date> to <end date>

<!--
Fill this in before the first row is entered. It is the only place where
measurement provenance is recorded: the CSV headers in pans.csv /
observations.csv / weather.csv / outcomes.csv have no column for an instrument
serial, because an unmapped column is dropped on import and a dropped column
loses the evidence for a measurement. Provenance therefore lives here, keyed by
the pan_id and instrument, not in a column that gets thrown away.
-->

## People

- Collected by:
- Site contact / land access permission:
- Landowner or operator agreement (reference, date):

## Site

- Location name:
- Latitude, longitude (decimal degrees, WGS84):
- Operating company / salt works:
- Are these production salt pans, an experimental set, or a natural salt flat?
  State which. SaltLens reasons about managed evaporation pans; a natural
  playa is a different physical system even when both are called "salt pans".

## Pans

| pan_id | pan_name | area_m2 | normal_depth_cm | target_harvest_salinity_g_l | registered on |
| ------ | -------- | ------- | --------------- | --------------------------- | ------------- |

One row per pan in `pans.csv`; this table is the human-readable copy. Leave
`normal_depth_cm` and `target_harvest_salinity_g_l` empty if they are not
known. An invented target is worse than a blank one, because the harvest rule
reads it.

## Instruments

| instrument | make / model | serial | measures | unit as read | calibration date | PSU→g/L conversion applied |
| ---------- | ------------ | ------ | -------- | ------------- | ---------------- | -------------------------- |

State the conversion actually used. A refractometer reading PSU is not g/L; a
concentrated brine in particular is not close. If a reading was corrected for
density, say so and give the factor.

## Rainfall source

- Gauge / product used for `actual_rainfall_mm`:
- Where it is sited relative to the pans:
- Accumulation window it reports (1 h, 24 h, ...):
- Forecast product used for `forecast_rainfall_mm`, and where it is recorded
  when a forecast is revised:

A forecast and an observation are different quantities. `rainfall_mm` with no
`forecast_for` is an event total; `rainfall_mm` with `forecast_for` set is a
forecast. Never fill the actual from the forecast.

## Units

Confirm the unit of every column you enter, in the unit the header demands:

- `salinity_g_l` — g/L, converted explicitly from the instrument reading
- `water_depth_cm` — standing brine depth in cm, not sensor depth, not basin depth
- `brine_temperature_c` — in the liquid
- `ambient_temperature_c` — shaded air, at pan height
- `humidity_percent` — measured, not derived from dew point
- `rainfall_mm` — mm
- `area_m2` — m²

## Timestamps

- Timezone recorded by the instruments:
- Timestamps in these files are UTC, ISO 8601 (`2024-10-03T14:20:00Z`).
  If the instrument logged local time, convert on entry and say so here.

## What counts as an event

State the rule used to decide that a rainfall event occurred, and what the
window is. Without it, one operator's events and another's are not the same
unit and the dataset cannot be graded consistently.

## Known problems during the campaign

Record instrument failures, logger clock drift, missed visits, and any
correction applied afterwards. An unrecorded correction is a fabricated value.

## Permissions and sharing

- May these rows be stored in this system?
- May they be exported, published or cited? By whom, under what terms?
- Personal data, worker names or GPS detail that must be redacted before export?

## Import log

Record the commands run and their verdicts, so a later reader can tell which
rows were audited and what the audit said:

```
$PY -m ml.ingestion audit    --raw data/raw/<campaign> --kind pans
$PY -m ml.ingestion validate --raw data/raw/<campaign> --kind pans --provenance FIELD_MEASURED
...
$PY -m ml.ingestion import   --processed data/processed --kind pans --commit
```

Nothing is written to the database without `--commit`, and `import` reports
what it would do before it does it.
