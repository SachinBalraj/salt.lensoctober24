# SaltLens — Dataset Provenance and Licences

## Sources actually ingested

| Source | Publisher | URL | Licence | Status |
|--------|-----------|-----|---------|--------|
| NOAA ISD | NOAA NCEI | `https://www.ncei.noaa.gov/access/services/data/v1` | US public domain, no copyright | Stage 5: normalised into `data/normalized/` |
| USGS NWIS | US Geological Survey | `https://waterservices.usgs.gov/nwis/dv/` | US public domain, no copyright | Stage 5: normalised into `data/normalized/` |

Every normalised row carries `source_license` and `source_doi` columns so the licence travels with
the datum into any dataset. No DOI is claimed where none exists.

## Import status (the honest picture)

- **486 553 rows** were normalised in Stage 5 (`docs/REAL_DATA_ACQUISITION_REPORT.md`).
- **0 rows were importable** as SaltLens training datasets: public-hydrology measurements do not carry
  salt-pan brine salinity or depth in the units Module 07 requires, so rows fail the metric and
  provenance checks by design. No weakened import was forced through.
- The `data/templates/README.md` stub defines the per-campaign provenance record that a real field
  campaign must fill in.

## Storage on the research cluster (live, 2026-09-30)

| Collection | Rows | Provenance |
|------------|------|-----------|
| Salt pans | 2 | 1 earlier demo pan + `kzn-2026-demo9` (demonstration) |
| Observations | 3 | all `data_source: demonstration` |
| Weather | 3 | all `source: demonstration` |
| Outcomes | 2 | both `is_demonstration: true` |
| Model versions | **0** | — |
| Experiments | **0** | — |

There are **no field-collected genuine observations** anywhere in the system. This is a fact, not a
gap to be papered over: `docs/FIELD_COLLECTION_TEMPLATE.md` defines how genuine data would be
captured, and the evidence gate defines the conditions under which it would be accepted.

## Licence of this repository's own artefacts

No external copyrighted data is redistributed in this work. The demonstration values are worked
examples generated for the exercise and are not measurements of any real pan.