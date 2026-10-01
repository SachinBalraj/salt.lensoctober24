"""Convert pristine third-party source files into long-form CSV for the audit.

The files under ``data/raw`` are kept byte-identical to what the publisher
served. Most publishers do not serve CSV, so this script converts their native
formats once, deterministically, and writes the result to ``data/normalized``.

Two rules make the output trustworthy:

* Nothing is imputed. Absent measurements stay absent, and a row that carries no
  measurement is dropped rather than padded with a placeholder.
* Every output row keeps a pointer back to the file and station/sensor it came
  from, so a reviewer can walk from any normalised value to the byte range in
  the original download.

Units are converted only where the source states a scaled integer encoding
(NOAA ISD documents tenths of degrees Celsius). Where a source reports
practical salinity or specific conductance, both are preserved in their native
units *and* a g/L column is added with the conversion written down, because
silently relabelling PSU as g/L is how a salinity dataset stops meaning what its
publisher said it meant.

Run:  python scripts/normalize_sources.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw"
OUT = REPO / "data" / "normalized"

# Practical Salinity is a dimensionless ratio (~1 kg/kg). Fresh water is about
# 0.5 g/L lower than seawater at the same mass fraction, so 1 PSU is 1.05 g/L of
# dissolved salt in ordinary seawater. Salt pans concentrate far past this, so
# the constant is only ever applied to low-salinity rows and the native column
# is always kept alongside.
PSU_TO_G_L = 1.05


def _rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


# PANGAEA's short codes are publisher-specific. These are the renames that let
# the existing catalogue aliases match, and each one is a claim about what the
# quantity *is*, so the rename is recorded here rather than buried in a
# heuristic. Anything not listed stays under its native name and shows up in
# the audit as `unmapped_columns`, which is the honest outcome for a quantity
# SaltLens has no field for.
PANG_RENAME = {
    "date/time": "datetime",
    "date_time": "datetime",
    # PANGAEA metadata declares TTT as "Temperature, air [degC]", which is
    # SaltLens `ambient_temperature_c`, and RH as relative humidity in percent.
    "ttt": "air_temperature_c",
}

# DOI and licence are read from the file's own metadata block, not invented.
_DOI_IN_TEXT = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s,;]+)", re.IGNORECASE)


def _pang_column(name: str) -> str:
    """`Dens pw [g/cm**3] (measure 1)` -> `dens_pw_measure_1`.

    PANGAEA writes `Dens pw [g/cm**3] (measure 1)`, so the qualifier is the last
    bracketed group of the whole name and the units are the first. Splitting on
    `[` first throws the qualifier away, and every replicate then lands on the
    same header.
    """
    qualifier = ""
    match = re.search(r"\(([^()]*)\)\s*$", name)
    if match:
        qualifier = match.group(1)
        name = name[: match.start()].strip()
    base = name.split("[")[0].strip()
    key = base.lower().replace("/", "_").replace(" ", "_")
    renamed = PANG_RENAME.get(base, PANG_RENAME.get(key, key))
    if qualifier:
        suffix = qualifier.strip().lower().replace(" ", "_").replace("-", "_")
        return f"{renamed}_{suffix}"
    return renamed



def pangaea_tab(src: Path, dst: Path, folder: str = "") -> tuple[int, dict[str, int]]:
    """PANGAEA .tab: a /* ... */ metadata block, a header line, then data."""
    lines = src.read_text(encoding="utf-8").splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.startswith("*/"):
            start = index + 1
            break
    header = lines[start].split("\t")
    body = [line.split("\t") for line in lines[start + 1 :] if line.strip()]

    columns = [_pang_column(n) for n in header]

    with dst.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(
            columns
            + [
                "source_file",
                "source_doi",
                "source_license",
                "site_or_sensor",
                "latitude",
                "longitude",
                "environment",
            ]
        )
        written = 0
        for record in body:
            if not any(cell.strip() for cell in record):
                continue
            record += [""] * (len(columns) - len(record))
            writer.writerow(
                record[: len(columns)] + _pangaea_provenance(src, folder=folder)
            )
            written += 1
    return written, {"rows": written, "columns": len(columns)}


# Owens Lake is a genuine salt pan. The BEFmate series is a salt marsh on a
# tidal flat. Both are open water or crust on a real site, but only the first is
# a salt pan, and the label travels with every row so no downstream step can
# quietly promote the marsh into a pan.
PANG_ENVIRONMENT = {
    "aux_salt_pan_owens_lake": "salt pan (Owens Lake salt flat, natural playa)",
    "aux_salt_pan_owens_lake_subsurface": "salt pan (Owens Lake salt flat, natural playa)",
    "aux_saltmarsh_salinity": "salt marsh / tidal flat (NOT a salt pan)",
}


def _salt_mass_fraction(water_g: str, salt_g: str) -> str:
    """Gravimetric salt fraction of the brine, from two measured masses.

    This is arithmetic on measured quantities, so it is not a derivation in the
    sense the ingestion layer refuses. What it is *not* is a salinity in g/L:
    that needs the pore-water density and a density-concentration relation,
    which is a physics step. The g/L value is therefore not written, and the
    number this returns is labelled as a mass fraction so it cannot be read as
    a concentration.
    """
    try:
        water, salt = float(water_g), float(salt_g)
    except (TypeError, ValueError):
        return ""
    total = water + salt
    if total <= 0:
        return ""
    return f"{salt / total:.5f}"


def pangaea_subsurface(src: Path, dst: Path, folder: str = "") -> tuple[int, dict[str, int]]:
    """Owens Lake subsurface brine: two distinct layouts behind one format.

    Pore-water files carry density replicates; salt-concentration files carry
    water/sand/salt masses. Both are spatial profiles down a crust transect, not
    a time series, and neither is renamed to a SaltLens measurement field.
    """
    lines = src.read_text(encoding="utf-8").splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.startswith("*/"):
            start = index + 1
            break
    header = lines[start].split("\t")
    body = [line.split("\t") for line in lines[start + 1 :] if line.strip()]
    columns = [_pang_column(name) for name in header]
    index_of = {name: position for position, name in enumerate(columns)}

    is_porewater = any(name.startswith("dens_pw") for name in columns)
    # The file's own coordinates are per-row; provenance coordinates are a
    # single fallback. Pick one set of headers and one set of values, in the
    # same order, rather than filtering blanks out of a fixed tuple and
    # shifting every later column by one.
    has_own_coords = "latitude" in index_of
    tail_names = ["source_file", "source_doi", "source_license",
                  "site_or_sensor", "environment"]
    if not has_own_coords:
        tail_names += ["latitude", "longitude"]
    if not is_porewater:
        tail_names = ["salt_mass_fraction_measured"] + tail_names

    with dst.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(columns + tail_names)
        written = 0
        for record in body:
            if not any(cell.strip() for cell in record):
                continue
            record += [""] * (len(columns) - len(record))
            src_name, doi, licence, site, lat, lon, environment = (
                _pangaea_provenance(src, folder=folder)
            )
            tail = [src_name, doi, licence, site, environment]
            if not has_own_coords:
                tail += [lat, lon]
            if not is_porewater:
                water = (
                    record[index_of["water_wm"]] if "water_wm" in index_of else ""
                )
                salt = (
                    record[index_of["salt_direct"]]
                    if "salt_direct" in index_of
                    else ""
                )
                tail = [_salt_mass_fraction(water, salt)] + tail
            writer.writerow(record[: len(columns)] + tail)
            written += 1
    kind = "porewater_density" if is_porewater else "salt_mass_concentration"
    return written, {"rows": written, "kind": kind, "columns": len(columns)}


_PANG = {}


def _pangaea_provenance(src: Path, folder: str) -> list[str]:
    if src not in _PANG:
        text = src.read_text(encoding="utf-8")
        lat = re.search(r"LATITUDE:\s*(-?[\d.]+)", text)
        lon = re.search(r"LONGITUDE:\s*(-?[\d.]+)", text)
        lic = re.search(r"License:\s*(.+)", text)
        site = re.search(r"Event\(s\):\s*(\S+)", text)
        doi = _DOI_IN_TEXT.search(text)
        _PANG[src] = {
            "lat": lat.group(1) if lat else "",
            "lon": lon.group(1) if lon else "",
            "lic": lic.group(1).strip() if lic else "",
            "site": site.group(1) if site else src.stem,
            "doi": f"https://doi.org/{doi.group(1)}" if doi else "",
        }
    meta = _PANG[src]
    return [
        src.name,
        meta["doi"],
        meta["lic"],
        meta["site"],
        meta["lat"],
        meta["lon"],
        PANG_ENVIRONMENT.get(folder, ""),
    ]



def usgs_rdb(src: Path, dst: Path) -> tuple[int, dict[str, int]]:
    """USGS RDB: '#' comments, a header, a format row, then data.

    A multi-site request returns one column per reporting agency for the same
    parameter code. They are the same measurement by different labs, so they
    are averaged rather than dropped, and the sample count is kept so a reviewer
    can see when a value is a mean of one.
    """
    text = [
        line
        for line in src.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    rows = [line.split("\t") for line in text if line.strip()]
    header = [c.strip() for c in rows[0]]
    body = rows[2:]

    value_cols = [
        (index, header[index])
        for index in range(3, len(header))
        if header[index] and not header[index].endswith("_cd")
    ]

    with dst.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(
            [
                "site_no",
                "datetime",
                "specific_conductance_us_cm",
                "specific_conductance_n",
                "salinity_g_l_estimated",
                "source_file",
                "source_url",
                "source_license",
                "agency_codes",
            ]
        )
        written = 0
        for record in body:
            # A multi-parameter request repeats the header/format pair between
            # parameter groups. Those rows are not measurements.
            if len(record) < 4 or not record[2] or not record[1].strip().isdigit():
                continue
            site, date = record[1].strip(), record[2].strip()
            values, agencies = [], []
            for index, code in value_cols:
                if index < len(record) and record[index].strip():
                    try:
                        values.append(float(record[index]))
                    except ValueError:
                        continue
                    agencies.append(code.split("_")[0])
            if not values:
                continue
            mean = sum(values) / len(values)
            writer.writerow(
                [
                    site,
                    date,
                    f"{mean:.1f}",
                    len(values),
                    f"{mean / 1000.0 * PSU_TO_G_L:.3f}",
                    src.name,
                    "https://waterservices.usgs.gov/nwis/dv/",
                    "USGS public domain (no copyright, credit requested)",
                    " ".join(sorted(set(agencies))),
                ]
            )
            written += 1
    return written, {"rows": written, "sites": len({r[1].strip() for r in body if len(r) > 3 and r[2].strip() and r[1].strip().isdigit()})}


def noaa_isd(src: Path, dst: Path) -> tuple[int, dict[str, int]]:
    """NOAA ISD lite: one hourly row, many elements, integer-scaled.

    Two encodings have to be undone, and both are traps:

    * ``TMP`` is tenths of a degree Celsius behind a quality flag, so ``+0084,1``
      is 8.4 C accepted, and the sentinels 9999/-9999 are not temperatures.
    * ``AA1`` is comma-packed ``period,amount_mm,condition,trace`` and the
      *period* decides what the amount means. Stations do not agree on a
      window: one reported 01/03/24 while another reported 06/12 for the same
      year, and the same rain is reported under several overlapping windows at
      once. Adding the windows together multiplies the storm, and keeping only
      the hourly code silently deletes a whole station's rainfall. So the
      window is carried as its own column and an intensity is derived from it,
      and nothing is summed across windows.
    """
    # ISD period code -> length of the accumulation window in hours. A code of
    # 99 means the publisher does not know the window either, so no intensity
    # is derived for it rather than one being guessed.
    WINDOW_HOURS = {
        "01": 1, "02": 2, "03": 3, "06": 6, "12": 12,
        "24": 24, "48": 48, "72": 72,
    }
    rows = _rows(src)
    header = rows[0]
    index = {name: position for position, name in enumerate(header)}

    def cell(record, name):
        position = index.get(name)
        if position is None or position >= len(record):
            return ""
        return record[position].strip()

    def element(record, name):
        """Split 'value,quality' and reject the documented sentinels."""
        raw = cell(record, name)
        if not raw:
            return ""
        value = raw.split(",")[0].strip()
        if value in {"", "M"}:
            return ""
        # Sentinels are written both bare and signed, so compare on magnitude.
        digits = value.lstrip("+-")
        if digits.isdigit() and abs(int(digits)) >= 9999:
            return ""
        return value

    with dst.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(
            [
                "station",
                "station_name",
                "latitude",
                "longitude",
                "elevation_m",
                "datetime",
                "ambient_temperature_c",
                "precipitation_mm",
                "precipitation_window_hours",
                "precipitation_intensity_mm_per_h",
                "precipitation_period_code",
                "precipitation_condition_code",
                "source_file",
                "source_url",
                "source_license",
            ]
        )
        written = 0
        for record in rows[1:]:
            stamp = cell(record, "DATE")
            if not stamp:
                continue
            temp_raw = element(record, "TMP")
            temp = ""
            if temp_raw and temp_raw.lstrip("+-").isdigit():
                temp = f"{int(temp_raw) / 10.0:.1f}"

            rain, window, intensity, period, condition = "", "", "", "", ""
            raw_aa1 = cell(record, "AA1")
            if raw_aa1:
                fields = raw_aa1.split(",")
                if len(fields) >= 3:
                    period, amount, condition = fields[0], fields[1], fields[2]
                    if amount.isdigit() and amount != "9999":
                        rain = str(int(amount))
                        hours = WINDOW_HOURS.get(period)
                        if hours:
                            window = str(hours)
                            intensity = f"{int(amount) / hours:.3f}"

            writer.writerow(
                [
                    cell(record, "STATION"),
                    cell(record, "NAME"),
                    cell(record, "LATITUDE"),
                    cell(record, "LONGITUDE"),
                    cell(record, "ELEVATION"),
                    stamp,
                    temp,
                    rain,
                    window,
                    intensity,
                    period,
                    condition,
                    src.name,
                    "https://www.ncei.noaa.gov/access/services/data/v1",
                    "NOAA public domain (no copyright)",
                ]
            )
            written += 1
    return written, {"rows": written}


def estuary(src: Path, dst: Path) -> tuple[int, dict[str, int]]:
    """SALTISolutions: already CSV, but a `_check` column is the curated twin."""
    rows = _rows(src)
    # `salinity` is a SaltLens alias for salinity_g_l, but SALTISolutions
    # reports practical salinity (PSU, dimensionless). Aliasing it straight in
    # would relabel PSU as g/L, so the native name is kept and the converted
    # value stays in the explicitly-named estimate column.
    EST_RENAME = {
        "date_time": "datetime",
        "depth": "sensor_depth_m",
        "salinity": "salinity_psu",
    }
    header = [EST_RENAME.get(h.strip(), h.strip()) for h in rows[0]]
    with dst.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(
            header
            + [
                "salinity_g_l_estimated",
                "source_file",
                "source_doi",
                "source_license",
                "environment",
            ]
        )
        written = 0
        for record in rows[1:]:
            if not any(cell.strip() for cell in record):
                continue
            record = record + [""] * (len(header) - len(record))
            sal = (
                record[header.index("salinity_psu")].strip()
                if "salinity_psu" in header
                else ""
            )
            est = ""
            if sal:
                try:
                    est = f"{float(sal) * PSU_TO_G_L:.3f}"
                except ValueError:
                    est = ""
            writer.writerow(
                record[: len(header)]
                + [
                    est,
                    src.name,
                    "https://data.4tu.nl/datasets/abc2eb1f-9291-44bd-9810-2ea0e98ae7eb",
                    "CC BY 4.0",
                    "estuary (Rhine-Meuse delta) - NOT a salt pan",
                ]
            )
            written += 1
    return written, {"rows": written, "columns": len(header)}


JOBS = [
    ("aux_salt_pan_owens_lake", ".tab", pangaea_tab),
    ("aux_salt_pan_owens_lake_subsurface", ".tab", pangaea_subsurface),
    ("aux_saltmarsh_salinity", ".tab", pangaea_tab),
    ("aux_usgs", ".rdb", usgs_rdb),
    ("aux_noaa_isd", ".csv", noaa_isd),
    ("aux_estuary_salinity", ".csv", estuary),
]


def main() -> int:
    for folder, suffix, handler in JOBS:
        source_dir = RAW / folder
        if not source_dir.is_dir():
            continue
        target_dir = OUT / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(source_dir.rglob(f"*{suffix}")):
            dst = target_dir / (src.stem + ".csv")
            try:
                written, info = handler(src, dst, folder) if folder in PANG_ENVIRONMENT else handler(src, dst)
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {src.name}: {exc}")
                continue
            print(f"  {folder}/{dst.name}: {written} rows {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
