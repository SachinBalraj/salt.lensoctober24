# Real Data Acquisition Report

Scope: find legally usable public datasets containing salt-pan salinity, water
depth, rainfall, temperature, humidity and post-rain outcomes; audit and map
them; import only what is genuinely compatible; assess whether a model can be
trained. Nothing was trained, no metric was computed, and no record was
fabricated. Sources were downloaded unmodified into `data/raw/`; derived CSVs
live in `data/normalized/`.

**Verdict: no dataset compatible with SaltLens's schema was found, and none was
imported. The stage ends at `ML NOT READY`.** The reason is structural rather
than a shortage of trying, and it is explained in
[Ingestion and MongoDB status](#6-ingestion-and-mongodb-status).

---

## 1. Datasets identified / downloaded

| # | Source | System measured | Period | Rows | Outcome |
|---|--------|-----------------|--------|------|---------|
| 1 | NOAA ISD Global Hourly | airport weather stations | 2024 | 61,519 | auxiliary; not importable |
| 2 | SALTISolutions Rhine–Meuse Delta 2022 | estuarine salinity/temperature | Jul–Oct 2022 | 259,913 | auxiliary; **not a salt pan** |
| 3 | Nield et al. 2020, Owens Lake temp/humidity | **salt pan** air temperature + RH | 25 Nov – 2 Dec 2016 | 52,911 | **genuine salt pan**, but no brine measurement |
| 4 | Pieck et al. 2026, BEFmate I3pio | salt-marsh dip-well salinity | Mar 2019 – Jul 2021 | 108,616 | auxiliary; **not a salt pan** |
| 5 | USGS NWIS specific conductance | saline drains, Great Salt Lake basin | 2018–2024 | 2,998 | auxiliary; not importable |
| 6 | Lasser & Goehring 2020, Owens Lake subsurface brine | **salt pan** pore-water density + salt mass | 2018 (single campaign) | 596 | **genuine salt pan brine**, but spatial only |

Sources 1 and 2 were already present in `data/raw/` from an earlier pass with no
recorded licence or URL. Sources 3–6 were located and downloaded during this
stage. Source 6 came from the FIDGeo salt-pan collection; see below for how the
dead host was worked around.

**Source 6 in detail.** `https://doi.org/10.1594/PANGAEA.922264` is the
publication series for Lasser & Goehring's Owens Lake subsurface survey, a
companion to the air-temperature series in source 3 (same site, same group, same
natural playa). It contains two distinct measurement types:

| File | Measurement | Rows | Columns of interest |
|------|-------------|------|---------------------|
| `OwensLake_porewater_V2.tab` | pore-water density, 4 replicates + mean + standard error | 153 | 1.0432 – 1.2708 g/cm³ |
| `OwensLake-T27-S_salt-conc_V2.tab` | gravimetric water/sand/salt mass | 153 | salt mass fraction 0.073 – 0.461 |
| `Owens_lake_T32-1-L1_P2_saltconc_V2.tab` | gravimetric water/sand/salt mass | 128 | same layout |
| `Owens_lake_T32-1-L1_P3_salt-conc_V2.tab` | gravimetric water/sand/salt mass | 162 | same layout |

This is the first source in the stage that measures **brine composition inside a
salt pan** rather than air over one. It also shows a coherent vertical structure
(section 5), which is a sanity check that the data are physical and not noise.

**Why FIDGeo was reachable after all.** `https://www.fidgeo.org/2020/037/` fails
at the transport layer (curl reports `http=000`, no response at all — the host
is down or blocked, not denying access). The DOI itself resolves, and its
`Location` chain points at a second, live host:

- `https://doi.org/10.5880/fidgeo.2020.037` → HTTP 200
- → `https://dataservices.gfz.de/10.5880/fidgeo.2020.037/surface-and-subsurface-characterisation-of/metadata/datacite.json` → HTTP 200

So the collection's *metadata* is retrievable from GFZ's own data service, and
the FIDGeo sub-datasets are individually deposited on PANGAEA and downloadable
there. This is recorded because "host unreachable" and "data unobtainable" are
different conclusions, and only the first one was true.

**Still identified but not obtained.**

- **PANGAEA `10.1594/PANGAEA.911239`** (Lasser 2020, chemical characterization of
  salt samples from Owens Lake and Badwater Basin) — sibling of source 6 in the
  same FIDGeo collection, same licence, crust **chemistry** rather than
  concentration or density, so it would not close a salinity gap either.
- **Zenodo 17044261**, Songnen Plain soil salinity (CC-BY-4.0) — soil salinity from
  remote sensing, no time series at pan scale and no outcomes.

The pattern is consistent: real salt-pan data that is open exists, and after
this stage it includes brine density and brine salt mass, but it is still
**a spatial profile from one or two campaigns on a natural playa**, not the
time series of brine salinity, water depth, rainfall and harvest result that
SaltLens reasons about. No public source supplies a post-event harvest outcome
for a managed salt pan, because that is commercial operational data that
operators do not publish.

---

## 2. URLs / licenses

| Source | Publisher | Persistent ID | Licence |
|--------|-----------|---------------|---------|
| NOAA ISD | NOAA NCEI | `https://www.ncei.noaa.gov/access/services/data/v1` | US public domain, no copyright |
| SALTISolutions | 4TU.ResearchData / TU Delft | `https://data.4tu.nl/datasets/abc2eb1f-9291-44bd-9810-2ea0e98ae7eb` | CC BY 4.0 (stated in the dataset's own README) |
| Owens Lake temp/RH | Max Planck Institute, via PANGAEA | `https://doi.org/10.1594/PANGAEA.922231` (series); per-sensor `…922227`, `…922229`, `…922230` | CC BY 4.0, open access, no moratorium |
| BEFmate I3pio | Oldenburg/Göttingen, via PANGAEA | `https://doi.org/10.1594/PANGAEA.989538` | CC BY 4.0, open access |
| USGS NWIS | US Geological Survey | `https://waterservices.usgs.gov/nwis/dv/` | US public domain, no copyright |
| Owens Lake subsurface brine | Max Planck Institute for Dynamics and Self-Organization, via PANGAEA | `https://doi.org/10.1594/PANGAEA.922264` (series); files cite `…922259`, `…922261`, `…922262` | CC BY 4.0, open access |
| FIDGeo salt-pan collection (metadata) | GFZ German Research Centre for Geosciences | `https://doi.org/10.5880/fidgeo.2020.037` → `https://dataservices.gfz.de/10.5880/fidgeo.2020.037/…` | metadata retrieved; data deposited on PANGAEA |

Licence and DOI for the PANGAEA sources were read from the DataCite record via
`ws.pangaea.de/oai`, not from the search result and not assumed. Attribution
requirements for the three CC BY 4.0 sources are recorded per row in
`data/normalized/` (`source_license`, `source_doi`) and must travel with any
export.

---

## 3. Genuine record counts

All counts are measured rows in `data/normalized/`, audited by the existing
pipeline. "Genuine" means the publisher reported the measurement; it does **not**
mean the record is compatible with SaltLens.

| Source | Rows read | Genuine measurements | Usable as a SaltLens pan observation |
|--------|-----------|----------------------|--------------------------------------|
| Owens Lake (salt pan) | 52,911 | 52,911 air temp; 9,769 also RH | **No** — ambient only, no salinity or depth |
| BEFmate salt marsh | 108,616 | 97,039 salinity + water temp | **No** — a salt marsh, not a pan |
| USGS saline drains | 2,998 | 2,998 specific conductance | **No** — no pan, no depth, no rainfall |
| NOAA ISD | 61,519 | 60,953 air temp; 18,420 rainfall | **No** — airport, 15–2000 km from any pan |
| SALTISolutions estuary | 259,913 | 259,884 salinity + temperature | **No** — an estuary |
| Owens Lake subsurface brine (salt pan) | 596 | 153 pore-water density (4 replicates each); 443 salt mass fraction | **No** — no timestamp, no pan, spatial transect only |
| **Total** | **486,553** | | **0 importable** |

**Genuine salt-pan outcomes: 0.** The single outcome row in MongoDB is
`outcome-7fd2fb70…`, recorded during API smoke-testing with
`is_demonstration: true` and no measurement behind it. It is not field evidence
and the `ValidationService` total of 1 must not be read as a validated event.

---

## 4. Variables and missingness

**Salt pan (Owens Lake, 6 sensors, 2-minute interval, 3 sites).** Genuinely a
salt pan — the salt-crust polygon sites of a dry salt flat.
`ambient_temperature_c` 52,911/52,911, range −5.42 to 20.87 °C.
`humidity_percent` 9,769/52,911 (only 2 of 6 sensors carried a hygrometer),
range 10.5 to 84 %. **No salinity, no water depth, no rainfall, no outcome.**

**Salt marsh (BEFmate I3pio).** `sal` 97,039/108,616 (**10.6 % missing**),
28.06–38.8 PSU. `temp` same fill rate, −1.3 to 22.89 °C. `depth_water` is a
constant 0.5 m — the logger's immersion depth, not a water level.

**Estuary (SALTISolutions).** `salinity_psu` 259,884/259,913, 0–39.9 PSU.
`temperature` full. Four of eleven files report `sound_velocity`,
`specific_conductance`, `turbidity` or `obs` as entirely empty.

**USGS.** `specific_conductance_us_cm` 2,998/2,998, 469–6,490 µS/cm. Two sites
(Bear River near Corinne, Goggin Drain), daily, 2018-09-18 to 2024-12-31. Up to
three reporting agencies per row; the normaliser averages them and records
`specific_conductance_n` so a value that is a mean of one is visible.

**NOAA ISD.** `ambient_temperature_c` 60,953/61,519. `precipitation_mm`
18,420/61,519; of those, 63 carry a reported amount with no derivable window
(period code `99`, so the publisher is not saying what span the amount covers),
and are therefore stored without an intensity.

**Salt pan subsurface brine (Owens Lake, 2018).** Two variables, both measured,
neither a salinity in the units SaltLens stores.

`dens_pw` — pore-water density, 153/153 filled, **1.0432 – 1.2708 g/cm³**. Four
replicates per sample are published alongside the mean and a standard error, so
replicate spread is checkable rather than assumed. Stratified by sampling depth
the values separate cleanly:

| Depth below surface | Sites | Density range (g/cm³) |
|---------------------|-------|-----------------------|
| 0 cm (crust surface) | T10-3, T16, T2-5, T27-A | 1.2383 – 1.2708 |
| 45 cm | T25-3 | 1.1049 – 1.1573 |
| 90 cm | T32-1-L1 (P2, P3) | 1.0432 – 1.0614 |

`water_wm` / `salt_direct` / `salt_indirect` — masses in grams, per sample.
Water and indirect salt mass are 100 % filled in two files and 98.8 % in the
third; `salt_direct` is only 123/162 filled in `…P3_salt-conc_V2` (24 % missing),
and where the direct mass is absent the row is left blank rather than treated as
zero salt. `sand` is a further 20–44 g per sample, i.e. the samples are sediment
cores, not clean brine.
`salt_mass_fraction_measured` (`salt_direct / (water_wm + salt_direct)`) is
computed from those masses: 130/153 filled in the T27-S transect, 0.0727 – 0.4611.

**No timestamps exist anywhere in this source.** Every row is a position on a
crust transect (`z` depth, `x` distance from the left ridge), collected in a
single 2018 campaign. The ingestion layer reports
`no_readable_timestamps` and `missing_required_field: timestamp` for all 596
rows, which is correct: there is no time axis to recover, and inventing one from
the campaign year would fabricate an interval.

The salinity gap narrowed but did not close. After this stage there is a source
that measures **brine inside a salt pan** — density and salt mass — but it is a
gravimetric and densitometric characterisation of a natural crust, not a
salinity in g/L and not a series. See section 5 for the derivation boundary that
keeps it that way.

---

## 5. Quality findings

**a. NOAA rainfall is not addable across windows, and the hourly figures are
not physical.** `AA1` is comma-packed `period,amount_mm,condition,trace`, and
stations disagree on the accumulation window: one reported `01/03/24`, another
only `06/12`. The same storm is reported under several overlapping windows at
once, so summing them multiplies rainfall. Measured, per station, 2024:

| Station | Sum across windows | Sum of intensity | Inflation |
|---------|--------------------|------------------|-----------|
| 08430099999 Murcia | 5,658 mm | 1,246 mm | 4.5× |
| 61641099999 | 12,280 mm | 607 mm | **20.2×** |
| 72575024126 | 14,636 mm | 10,624 mm | 1.4× |

Worse, taking the *intuitive* route — keep only the hourly (`01`) rows, the one
window SaltLens actually wants — yields peak intensities of **360 mm/h and
1016 mm/h**, which cannot occur. The 6 h, 12 h and 24 h windows of the same
stations give peaks of 13–53 mm/h, which are plausible. So the hourly rows in
this export are mispacked or faulty while the longer windows are not, and the
structure is internally inconsistent. **The ISD rainfall in these files must not
be used for event totals without cross-validation against the station's
official NOAA climate product.** This is the single most dangerous trap found.

**b. An intermediate fix was itself wrong, which is the point.** Keeping only
`01` silently produced an entirely empty rainfall column for station
16429099999, which reports `06/12` and no hourly rows at all. The failure was
invisible: no error, an empty column. The normaliser now carries the window with
the amount and derives intensity from it, and derives no intensity when the
publisher's own code is `99`.

**c. Name-based alias matching invents units.** `depth` is a SaltLens alias for
`water_depth_cm` and `salinity` for `salinity_g_l`. In SALTISolutions, `depth`
is metres below datum (negative) and `salinity` is PSU. The estuary's positive
fixed-station depths (0.35–5.65) land inside the accepted 0–1000 cm range and
**raise no error at all** — 259,913 rows of metres would have been stored as
centimetres. The normaliser now renames these to `sensor_depth_m` and
`salinity_psu` so they stay unmapped, and keeps any conversion in an explicitly
named `salinity_g_l_estimated` column. PSU→g/L uses 1.05, valid only for
low-salinity water; the native column is always kept.

**d. Publisher short codes and unit suffixes block matching silently.** PANGAEA
writes `TTT [°C]` and `Date/Time`. Dropped to lowercase these are `ttt [°c]` and
`date_time`, neither of which matches the catalogue, so a full week of genuine
salt-pan data was reported as `unmapped_columns` with no complaint. Fixed by an
explicit rename table driven by the publisher's own metadata.

**e. Timestamps carry no timezone.** PANGAEA `.tab` timestamps are naive; the
BEFmate metadata states UTC, the Owens Lake metadata does not. Every affected
file is flagged `assumed_utc`. Left as read, with the flag.

**f1. Replicate columns in a `.tab` header collide, and a CSV cannot show it.**
PANGAEA separates repeated measurements of one quantity with a trailing
qualifier: `Dens pw [g/cm**3] (measure 1)` … `(measure 4)`, `(average)`, and
`Salt [g] (direct)` / `(indirect)`. Splitting the header on the first `[` to
strip units throws the qualifier away, so all five density columns reduce to
`dens_pw` and both salt columns to `salt`. `csv.DictReader` then keeps only the
**last** duplicate and discards the rest without warning — the file still has
the right number of rows and every value looks plausible. The densitometry would
have appeared to be a single measurement, and the direct/indirect salt
cross-check would have vanished. Caught by asserting `len(columns) ==
len(set(columns))` in the test suite; the normaliser now extracts the qualifier
from the full header before stripping units.

**f2. Density and gravimetry are measurements; a g/L salinity would not be.**
The pore-water density and the water/sand/salt masses are read straight from the
files. `salt_mass_fraction_measured` is arithmetic on two of those masses, so it
is a measurement-derived ratio. Turning it into a salinity in g/L is a different
thing and was **not** done: it needs the pore-water density *and* a
density-to-concentration relation for this brine, and the ingestion layer refuses
`PHYSICS_DERIVED` provenance by design. A ~1.25 g/cm³ brine is roughly
25 % NaCl by mass, which is the right order for a working salt pan, but "right
order" is not a licence to publish it as a measured `salinity_g_l`. The column is
therefore named `salt_mass_fraction_measured`, no `salinity_g_l` is written, and
the conversion is left as an explicit decision for a domain owner — who would
have to choose an appropriate relation and accept `PHYSICS_DERIVED` provenance.

**f3. The vertical gradient is the data's own sanity check.** Surface brine at
1.2383–1.2708 g/cm³ against 1.0432–1.0614 g/cm³ at 90 cm (section 4) is what
evaporative concentration does to a playa: dense brine at the crust surface,
dilute pore water at depth. The values are monotone with depth across three
independent sites, and the published standard errors (order 1e-4 to 1e-3) are far
smaller than the between-site spread. This is evidence the density column is
being read correctly, and it is why source 6 is treated as a genuine
measurement rather than a data-quality question. It remains a spatial profile,
so it still says nothing about change over a season.

**g. Duplicate readings.** 1,215 duplicated records in `eem_bot` alone,
5,234–5,297 in the `nma` files, 456 in Owens Lake `T36-3`, and 5–281 in the
NOAA files. Idempotent at the content-address level, so staging keeps one, but
repeated readings indicate logger behaviour that should be understood before
use.

**g. One estuary file is empty.** `04_08_2022_ship_survey_ctd_transect2.csv` has
a header and no data rows, delivered that way by the publisher.

**h. A verified parser is not a validated one.** Finding (a) shows the AA1
structure was confirmed against the publisher's 4-field encoding for all 8,140
non-empty rows, yet the hourly magnitudes remain implausible. Structure
verified, magnitudes not.

---

## 6. Ingestion and MongoDB status

**Nothing was imported. The database is unchanged.**

```
pans 1 · observations 2 · weather 2 · outcomes 1   (unchanged from before this stage)
data/processed/ : empty (only .gitkeep)
```

The normaliser writes to `data/normalized/`; the pipeline was run in `audit` mode
only, which writes nothing. `stage` and `import` were never run, and
`import --commit` was never used.

**Why nothing was compatible.** Every one of the 486,553 rows was rejected by
the existing audit, and after the alias defects in finding (c) were removed, the
*only* remaining error on sources 1–5 was:

```
[error] missing_required_field: 'pan_id' is required and empty on N row(s).
```

Source 6 additionally reports `missing_required_field: 'timestamp'` and
`no_readable_timestamps` on all 596 rows. That one is not a schema mismatch: the
dataset is a spatial crust transect and has no time axis at all, so there is
nothing to recover. Both errors point the same way — these are real
measurements from places that are not SaltLens pans.

This is the correct result, and the reason is architectural. In
`ml/ingestion/catalog.py`, `OBSERVATION_SPEC` and `WEATHER_SPEC` both require
`pan_id`, and **neither has a latitude/longitude field**. SaltLens is entirely
pan-scoped: a weather record is defined as "a reading of the sky over one pan".

So there is no way to store a weather station, a salt marsh, an estuary or a
river gauge without first inventing a `SaltPan` at that location. Creating a pan
per airport and per river gauge to host real measurements would be fabricating
the very entity the measurements are attached to, and would make `area_m2`,
`normal_depth_cm` and `target_harvest_salinity_g_l` invented too. The Owens
Lake data is the closest case — it is genuinely a salt pan — but Owens Lake is a
natural dry playa, and registering it as an operational evaporation pan with a
target harvest salinity would assert something the data does not say.

Recommended fix, not taken here because it changes ingestion behaviour mid-audit:
add location-scoped auxiliary collections, or relax `pan_id` for observations
and add a `site_id` with coordinates. Until then the pipeline's only defence
against a mislabelled third-party row is the missing `pan_id`, which is fragile.

Command used, per source, with `--paths` because `--raw` alone is non-recursive:

```bash
cd backend
PYTHONPATH=.. .venv/bin/python -m ml.ingestion audit \
  --kind observations --raw ../data/normalized --provenance PUBLIC_MEASURED \
  --paths ../data/normalized/<source>/*.csv
```

---

## 7. Actual salt-pan outcomes

**None. Zero events have a measured post-rain state.**

The only outcome in MongoDB is the demonstration smoke record with
`is_demonstration: true` and no salinity, depth or harvest result. It exercises
the API path; it is not evidence.

No public source can supply this column. A `harvest_outcome` is a commercial
operational fact about a specific salt works, and none of the open datasets
above — Owens Lake crust studies, a salt-marsh ecology experiment, an estuary
campaign, national weather and streamflow services — records what a salt
operator did after a storm. This is the hard floor under the whole project: no
amount of data acquisition from public sources will produce a labelled outcome,
so the only route is on-site collection under a fixed protocol.

---

## 8. Field template

The four header-only templates in `data/templates/` were verified to validate
cleanly, which is the thing worth checking — a template that does not load is
worse than none:

```
pans         ACCEPTED (0 errors)   observations  ACCEPTED (0 errors)
weather      ACCEPTED (0 errors)   outcomes      ACCEPTED (0 errors)
```

Their columns: `pans.csv` carries `pan_id, pan_name, location_name, latitude,
longitude, area_m2, normal_depth_cm, target_harvest_salinity_g_l,
risk_thresholds`; `observations.csv` the pre- and post-event state with
`saltinity_g_l, water_depth_cm, brine_temperature_c, humidity_percent,
ambient_temperature_c, note`; `weather.csv` the forecast and observed rainfall
kept apart by `forecast_for`; `outcomes.csv` the event itself with
`forecast_rainfall_mm, pre_event_salinity_g_l, pre_event_depth_cm,
actual_rainfall_mm, post_event_salinity_g_l, post_event_depth_cm,
operator_action, harvest_outcome`.

**Added this stage: `data/templates/README.md`**, a campaign provenance stub.
The gap it closes is real. The required template includes measurement source,
and there is no column for it — an unmapped column is dropped on import, so
putting the instrument serial in the CSV would discard the evidence for the
measurement. Provenance therefore belongs in a per-campaign README keyed by
`pan_id` and instrument. The stub covers people and land access, whether the
site is production pans, an experimental set or a natural flat, per-instrument
serial and calibration date with the PSU→g/L conversion actually applied, the
rainfall gauge and its accumulation window, the unit of every column, the
timezone, the rule used to decide an event occurred, problems during the
campaign, and export permissions.

Protocol and loading instructions are in
[`FIELD_COLLECTION_TEMPLATE.md`](FIELD_COLLECTION_TEMPLATE.md).

---

## 9. Test results

```
backend    1066 passed   (1046 before + 20 new)
frontend    39 files, 816 passed; tsc --noEmit and eslint clean
ruff        clean on all files touched in this stage
```

The 20 new tests are in `backend/tests/test_normalize_sources.py` and target
exactly the silent failures above, because each of those produced no error at
the time:

- a PANGAEA unit suffix must not stop the `datetime` alias matching;
- DOI, licence and site are read from the file's metadata or left empty, never
  constructed;
- a salt-marsh row is labelled so it cannot later be promoted to a pan;
- the NOAA accumulation window travels with the amount, and a 12 h total is not
  stored as an hour of rain;
- period code `99` yields no intensity;
- `9999`, `9999`, `-9999`, `99999`, `M` and empty never become degrees Celsius,
  and `+0084,1` is 8.4 °C;
- a repeated RDB header between parameter groups is not a measurement;
- multi-agency conductance averages and records how many contributed;
- estuarine `depth` is never offered as pan water depth;
- PANGAEA replicate qualifiers yield distinct columns, and the replicate values
  actually reach those columns rather than overwriting each other;
- a file that states its own latitude/longitude is not shadowed by provenance
  coordinates;
- `salt_mass_fraction_measured` is `salt/(water+salt)`, blank when the water mass
  is absent rather than zero, and never emitted as a `salinity_g_l`;
- a subsurface crust transect exposes no timestamp, so it cannot be read as a
  series.

Two fixture bugs in that file were mine, not the parser's: the PANGAEA header
sits after the closing `*/`, and an ISD row missing `SLP` puts rainfall in the
pressure column. Both are now encoded in the fixture builder.

The seven pre-existing Ruff errors in untouched files are unchanged.

---

## 10. ML readiness

**`ML NOT READY`.** Against what the training layer itself requires:

| Requirement | Status |
|-------------|--------|
| Labelled target | **Absent** — 0 outcomes with a `harvest_outcome` |
| Genuine field data | **Absent** — 0 importable pan observations |
| ≥3 independent pans | **Absent** — the only pan is a demonstration pan |
| Leakage-safe split by pan | Not constructible |
| Comparable forecast/actual pairs | **Absent** — the one outcome has no measurement |

This is not a marginal shortfall that a larger public download would fix. Two
independent blockers, either of which is sufficient:

1. **No labels.** Nothing public records a salt-pan harvest result. Training a
   harvest model requires collecting outcomes on site.
2. **No compatible observations.** Even setting labels aside, all 486,553 public
   rows are outside the pan-scoped schema, and admitting them would require
   inventing the pans they attach to.

The module-08 pipeline will refuse this on its own terms, and that refusal is
the correct outcome to report. Nothing was trained, no candidate model was
fitted, no held-out score or SHAP value exists, and none should be quoted.

---

## 11. Scientific limitations

- **The salt-pan brine data is a composition, not a salinity, and not a series.**
  Source 6 fixes "no brine measurement in a salt pan" but only partially. It
  gives density (g/cm³) and gravimetric salt mass fraction at 9 sites in one 2018
  campaign, on a natural playa crust. It gives **no g/L salinity** (that needs a
  density relation, which is a derivation this project refuses), **no water
  depth**, and above all **no time axis** — so it cannot support a time-series
  model even for the single variable it does measure.
- **A natural playa is not an operational salt pan.** Owens Lake 2016 was dry
  crust. Treating it as an evaporation pan would misdescribe the heat and mass
  balance the model relies on.
- **Salinity ranges do not overlap.** The salinity sources span 0–39.9 PSU.
  Working salt pans target tens to a few hundred g/L, and the accepted range is
  0–500 g/L. No public data here exercises the regime SaltLens operates in.
- **PSU and g/L are not interchangeable.** The 1.05 constant is valid for
  seawater; at pan concentrations the density correction diverges and the native
  PSU value must not be converted naively.
- **Rainfall here is unvalidated.** Finding (a). The ISD rainfall cannot be used
  for event totals as it stands, and event total rainfall is the primary
  predictor.
- **No rain-to-outcome linkage anywhere.** Every source is either continuous
  (no events) or event-based (no outcome), so no event window can be constructed.
- **Single-week salt-pan record.** 8 days in 2016, 6 sensors, 3 sites. Even if
  it were brine, it could not support a seasonal model.
- **No timezone on PANGAEA timestamps**, flagged `assumed_utc` and unverified.
- **FIDGeo's own host is still unreachable**, so the collection was reached only
  in part: the DOI resolves and GFZ's `dataservices.gfz.de` serves its metadata,
  and the sub-dataset deposited on PANGAEA (`.922264`) was obtained, but the
  sibling crust-chemistry dataset `.911239` was not retrieved. A dead host is not
  evidence of absent data, and the FIDGeo landing page should be re-checked
  before any claim is made about the collection's full contents.
- **The subsurface brine has no event context.** A 2018 crust transect cannot be
  attached to a rainfall event, so it cannot be used to test the rainfall→brine
  response that is the core hypothesis.
- **Single-site auxiliary records** (one salt marsh, one estuary, two gauges)
  carry no cross-site variance and cannot support a pan-general claim.

---

## 12. Next action

1. **Close out the FIDGeo collection**: retrieve `.911239` (crust chemistry, same
   licence) via PANGAEA and re-check the GFZ `dataservices.gfz.de` endpoint, so
   the salt-pan inventory is complete and the dead `fidgeo.org` host is
   documented rather than assumed. Lower value than item 3 — it is crust
   chemistry, not a salinity series.
2. **Decide the auxiliary-schema question** before any further import. Either add
   location-scoped auxiliary collections, or keep public data in
   `data/normalized/` and reference it from datasets. The alternative —
   minting a `SaltPan` per station — should be recorded as rejected.
3. **Run a field campaign** against `data/templates/` and the protocol in
   [`FIELD_COLLECTION_TEMPLATE.md`](FIELD_COLLECTION_TEMPLATE.md). This is the
   only route to `ML READY`, and it should start before any further public-data
   work, since the outcomes cannot be acquired later.
4. **Validate NOAA rainfall** against the official climate product for any site
   that will be used, and only then derive event totals.
5. **Treat `ML NOT READY` as the reported result** until at least three pans have
   measured pre-event state, gauge rainfall, post-event state and a recorded
   `harvest_outcome`.

---

## File manifest

```
scripts/normalize_sources.py              publisher formats -> long-form CSV
backend/tests/test_normalize_sources.py   20 tests, the silent failures
data/templates/README.md                  campaign provenance stub (new)
data/normalized/                          derived CSVs + per-row provenance
data/raw/aux_salt_pan_owens_lake_subsurface/  source 6, unmodified, sha256-verified
                                           against the publisher's zip
data/raw/                                 unmodified downloads, untouched
```
