"""Tests for the source normaliser.

The two failures these guard against were both silent. A NOAA station that
reports its rainfall on 6 h and 12 h windows produced an empty column under a
"hourly rows only" rule that another station's 1 h windows satisfied perfectly,
and a PANGAEA ``Date/Time`` header with a unit suffix stopped matching the
catalogue alias and became an unmapped column. Neither raised anything; the
data simply stopped arriving.

So the assertions are about what the file *says*, not about a convenient
number: the accumulation window has to travel with the amount, and a publisher
short code has to be renamed only when the publisher's own metadata says what
it is.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "normalize_sources", REPO / "scripts" / "normalize_sources.py"
)
assert SPEC and SPEC.loader
normalize = importlib.util.module_from_spec(SPEC)
sys.modules["normalize_sources"] = normalize
SPEC.loader.exec_module(normalize)


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


# --------------------------------------------------------------------------
# PANGAEA
# --------------------------------------------------------------------------


def _pangaea_file(tmp_path: Path, header: str, body: str) -> Path:
    src = tmp_path / "series.tab"
    src.write_text(
        "/* DATA DESCRIPTION:\n"
        "Coverage:\tLATITUDE: 36.499250 * LONGITUDE: -117.933320\n"
        "Event(s):\tTEST_SITE (TEST_SITE) * LOCATION: California, USA\n"
        "License:\tCreative Commons Attribution 4.0 International (CC-BY-4.0)\n"
        f"*/\n{header}\n{body}",
        encoding="utf-8",
    )
    return src


def test_pangaea_units_suffix_does_not_block_the_catalogue_alias(tmp_path: Path) -> None:
    """`Date/Time [x]` must still become the `datetime` the catalogue expects."""
    src = _pangaea_file(
        tmp_path, "Date/Time\tTTT [°C]\tRH [%]", "2016-11-25T09:14\t3.04\t65.8\n"
    )
    dst = tmp_path / "out.csv"
    written, _ = normalize.pangaea_tab(src, dst, folder="aux_salt_pan_owens_lake")
    columns, rows = _read(dst)

    assert written == 1
    assert "datetime" in columns, "timestamp column lost to the unit suffix"
    assert "air_temperature_c" in columns
    assert "rh" in columns
    assert rows[0]["datetime"] == "2016-11-25T09:14"


def test_pangaea_provenance_is_read_not_invented(tmp_path: Path) -> None:
    """DOI, licence and site come from the file's own metadata block."""
    src = _pangaea_file(
        tmp_path, "Date/Time\tRH [%]", "2016-11-25T09:14\t65.8\n"
    )
    dst = tmp_path / "out.csv"
    written, _ = normalize.pangaea_tab(src, dst, folder="aux_salt_pan_owens_lake")
    _, rows = _read(dst)

    assert written == 1
    assert rows[0]["source_license"].startswith("Creative Commons Attribution 4.0")
    assert rows[0]["site_or_sensor"] == "TEST_SITE"
    assert rows[0]["latitude"] == "36.499250"
    # No DOI in the metadata block means no DOI, not a constructed one.
    assert rows[0]["source_doi"] == ""


def test_a_salt_marsh_is_labelled_so_it_cannot_become_a_pan(tmp_path: Path) -> None:
    """Every row carries the environment it came from."""
    src = _pangaea_file(tmp_path, "Date/Time\tSal", "2019-03-26T18:00\t30.56\n")
    dst = tmp_path / "out.csv"
    normalize.pangaea_tab(src, dst, folder="aux_saltmarsh_salinity")
    _, rows = _read(dst)

    assert "NOT a salt pan" in rows[0]["environment"]


def test_psu_is_not_relabelled_as_grams_per_litre(tmp_path: Path) -> None:
    """`salinity` is a SaltLens alias for salinity_g_l, so PSU keeps its own name."""
    assert normalize._pang_column("Sal") == "sal"


# --------------------------------------------------------------------------
# NOAA ISD
# --------------------------------------------------------------------------




def _isd_file(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    """Write a minimal ISD-lite file.

    Real column order matters here: ``SLP`` sits between ``DEW`` and ``AA1``,
    and a fixture that is one field short silently puts the rainfall into the
    pressure column where nothing will ever read it.
    """
    columns = [
        "STATION", "DATE", "SOURCE", "LATITUDE", "LONGITUDE", "ELEVATION",
        "NAME", "REPORT_TYPE", "CALL_SIGN", "QUALITY_CONTROL", "WND", "CIG",
        "VIS", "TMP", "DEW", "SLP", "AA1",
    ]
    defaults = {
        "STATION": "08430099999", "DATE": "2024-03-01T00:00:00", "SOURCE": "4",
        "LATITUDE": "38.0", "LONGITUDE": "-1.1", "ELEVATION": "62", "NAME": "X",
        "REPORT_TYPE": "FM-12", "CALL_SIGN": "99999", "QUALITY_CONTROL": "V020",
        "WND": "240,1,N", "CIG": "99999,9,9", "VIS": "009000,1,9,9",
        "TMP": "+0084,1", "DEW": "+0028,1", "SLP": "10132,1", "AA1": "99999,9",
    }
    src = tmp_path / "station.csv"
    lines = [",".join(f'"{n}"' for n in columns)]
    for overrides in rows:
        merged = {**defaults, **overrides}
        lines.append(",".join(f'"{merged[n]}"' for n in columns))
    src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return src


def test_rainfall_window_travels_with_the_amount(tmp_path: Path) -> None:
    """A 12 h accumulation must not be stored as though it were one hour of rain."""
    src = _isd_file(
        tmp_path,
        [
            {"DATE": "2024-03-01T00:00:00", "AA1": "01,020,2,1"},
            {"DATE": "2024-03-01T12:00:00", "AA1": "12,020,2,1"},
        ],
    )
    dst = tmp_path / "out.csv"
    normalize.noaa_isd(src, dst)
    _, rows = _read(dst)

    first, second = rows
    assert first["precipitation_mm"] == "20"
    assert first["precipitation_window_hours"] == "1"
    assert first["precipitation_intensity_mm_per_h"] == "20.000"
    # Same storm, 12 h window: the amount stands, the intensity is a twelfth.
    assert second["precipitation_mm"] == "20"
    assert second["precipitation_window_hours"] == "12"
    assert second["precipitation_intensity_mm_per_h"] == "1.667"


def test_an_unknown_window_yields_no_intensity(tmp_path: Path) -> None:
    """Period code 99 means the publisher does not know either."""
    src = _isd_file(tmp_path, [{"AA1": "99,005,9,1"}])
    dst = tmp_path / "out.csv"
    normalize.noaa_isd(src, dst)
    _, rows = _read(dst)

    assert rows[0]["precipitation_mm"] == "5"
    assert rows[0]["precipitation_window_hours"] == ""
    assert rows[0]["precipitation_intensity_mm_per_h"] == ""


@pytest.mark.parametrize("raw", ["9999", "+9999", "-9999", "99999", "M", ""])
def test_temperature_sentinels_never_become_degrees(tmp_path: Path, raw: str) -> None:
    """`+0084,1` is 8.4 C; a signed 9999 sentinel is not 999.9 C."""
    src = _isd_file(tmp_path, [{"TMP": f"{raw},1"}])
    dst = tmp_path / "out.csv"
    normalize.noaa_isd(src, dst)
    _, rows = _read(dst)

    assert rows[0]["ambient_temperature_c"] == ""


def test_a_valid_temperature_is_scaled_by_ten(tmp_path: Path) -> None:
    src = _isd_file(tmp_path, [{"TMP": "+0084,1"}])
    dst = tmp_path / "out.csv"
    normalize.noaa_isd(src, dst)
    _, rows = _read(dst)

    assert rows[0]["ambient_temperature_c"] == "8.4"

# --------------------------------------------------------------------------
# USGS
# --------------------------------------------------------------------------


def test_usgs_ignores_a_repeated_header_between_parameter_groups(
    tmp_path: Path,
) -> None:
    """A multi-parameter RDB repeats header/format rows; they are not data."""
    src = tmp_path / "iv.rdb"
    src.write_text(
        "# comment\n"
        "agency_cd\tsite_no\tdatetime\t295859_00095_00001\t295859_00095_00001_cd\n"
        "5s\t15s\t20d\t14n\t10s\n"
        "USGS\t10126000\t2018-09-28\t2560\tA\n"
        "USGS\t10126000\t2018-09-29\t2790\tA\n"
        "\n"
        "agency_cd\tsite_no\tdatetime\t241133_00095_00002\t241133_00095_00002_cd\n"
        "5s\t15s\t20d\t14n\t10s\n"
        "USGS\t10172630\t2018-09-28\t2470\tA\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.csv"
    written, info = normalize.usgs_rdb(src, dst)
    _, rows = _read(dst)

    assert written == 3
    assert info["sites"] == 2
    assert {row["site_no"] for row in rows} == {"10126000", "10172630"}
    assert all(row["site_no"].isdigit() for row in rows)


def test_usgs_averages_agencies_but_records_how_many(tmp_path: Path) -> None:
    src = tmp_path / "iv.rdb"
    src.write_text(
        "# c\n"
        "agency_cd\tsite_no\tdatetime\tA_00095_00001\tA_00095_00001_cd\t"
        "B_00095_00002\tB_00095_00002_cd\n"
        "5s\t15s\t20d\t14n\t10s\t14n\t10s\n"
        "USGS\t10126000\t2018-09-28\t1000\tA\t3000\tA\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.csv"
    normalize.usgs_rdb(src, dst)
    _, rows = _read(dst)

    assert rows[0]["specific_conductance_us_cm"] == "2000.0"
    assert rows[0]["specific_conductance_n"] == "2"
    assert rows[0]["salinity_g_l_estimated"] == "2.100"


# --------------------------------------------------------------------------
# SALTISolutions
# --------------------------------------------------------------------------


def test_estuary_depth_is_not_offered_as_pan_water_depth(tmp_path: Path) -> None:
    """Estuarine `depth` is metres below datum, not centimetres of brine."""
    src = tmp_path / "por_top.csv"
    src.write_text(
        "date_time,depth,temperature,salinity\n"
        "2022-08-01 15:25:27+00:00,-0.529,21.43,17.317\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.csv"
    normalize.estuary(src, dst)
    columns, rows = _read(dst)

    assert "sensor_depth_m" in columns
    assert "water_depth_cm" not in columns
    assert rows[0]["sensor_depth_m"] == "-0.529"
    assert rows[0]["datetime"] == "2022-08-01 15:25:27+00:00"
    assert "NOT a salt pan" in rows[0]["environment"]


# --------------------------------------------------------------------------
# Owens Lake subsurface brine (pore-water density, salt mass concentration)
# --------------------------------------------------------------------------


def test_pangaea_replicate_columns_do_not_collide(tmp_path: Path) -> None:
    """`Dens pw (measure 1..4)` and `Salt (direct/indirect)` are distinct columns.

    PANGAEA repeats a short code and disambiguates with a trailing qualifier. A
    splitter that cuts on the first `[` discards the qualifier, every replicate
    lands on the same header, and a CSV dict silently keeps only the last value.
    """
    assert normalize._pang_column("Dens pw [g/cm**3] (measure 1)") == "dens_pw_measure_1"
    assert normalize._pang_column("Dens pw [g/cm**3] (average)") == "dens_pw_average"
    assert normalize._pang_column("Salt [g] (direct)") == "salt_direct"
    assert normalize._pang_column("Salt [g] (indirect)") == "salt_indirect"

    # A rename still applies, and still carries its qualifier.
    assert normalize._pang_column("TTT [degC] (1)") == "air_temperature_c_1"


def test_pangaea_replicate_values_survive_into_distinct_columns(tmp_path: Path) -> None:
    src = tmp_path / "OwensLake_porewater_V2.tab"
    src.write_text(
        "/* DATA DESCRIPTION:\n"
        "Coverage:\tMEDIAN LATITUDE: 36.45 * MEDIAN LONGITUDE: -117.94\n"
        "License:\tCreative Commons Attribution 4.0 International (CC-BY-4.0)\n"
        "*/\n"
        "Event\tLatitude\tLongitude\tSite\tSample ID\tDepth sed [m]\t"
        "Distance [cm] (from left ridge)\tDens pw [g/cm**3] (measure 1)\t"
        "Dens pw [g/cm**3] (measure 2)\tDens pw [g/cm**3] (average)\t"
        "Density std e [+]\n"
        "OwensLake_T10-3\t36.3858\t-117.9462\tT10-3\t1\t0.00\t0\t"
        "1.24344\t1.24245\t1.2451150\t0.002538247\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.csv"
    normalize.pangaea_subsurface(src, dst)
    columns, rows = _read(dst)

    assert len(columns) == len(set(columns)), "duplicate headers collapse data"
    assert "dens_pw_measure_1" in columns
    assert rows[0]["dens_pw_measure_1"] == "1.24344"
    assert rows[0]["dens_pw_measure_2"] == "1.24245"
    assert rows[0]["dens_pw_average"] == "1.2451150"
    assert rows[0]["distance_from_left_ridge"] == "0"
    # The file states its own coordinates, so provenance must not shadow them.
    assert columns.count("latitude") == 1
    assert rows[0]["latitude"] == "36.3858"
    assert rows[0]["longitude"] == "-117.9462"


def test_salt_mass_fraction_is_mass_ratio_not_a_salinity(tmp_path: Path) -> None:
    """The fraction is salt/(water+salt): measured masses, no density relation.

    A g/L salinity would need the pore-water density and a concentration
    relation, which is a physics step the ingestion layer refuses as
    PHYSICS_DERIVED. So the column is named as a mass fraction and no
    `salinity_g_l` is written at all.
    """
    src = tmp_path / "saltconc_V2.tab"
    src.write_text(
        "/* DATA DESCRIPTION:\n"
        "License:\tCreative Commons Attribution 4.0 International (CC-BY-4.0)\n"
        "*/\n"
        "Sample label\tZ [cm]\tX [cm]\tWater wm [g]\tSand [g]\t"
        "Salt [g] (direct)\tSalt [g] (indirect)\n"
        '"(1,1)"\t3\t0\t4.1512\t9.75\t2.88\t3.28\n'
        '"(1,2)"\t10\t0\t4.0360\t7.60\t1.49\t1.67\n'
        '"(1,3)"\t20\t0\t0\t0\t\t\n',
        encoding="utf-8",
    )
    dst = tmp_path / "out.csv"
    normalize.pangaea_subsurface(src, dst, folder="aux_salt_pan_owens_lake_subsurface")
    columns, rows = _read(dst)

    assert rows[0]["salt_mass_fraction_measured"] == "0.40960"
    assert rows[0]["salt_direct"] == "2.88"
    assert rows[0]["salt_indirect"] == "3.28"
    assert "salinity_g_l" not in columns
    # No water mass means no ratio, and an empty cell rather than a zero salt pan.
    assert rows[2]["salt_mass_fraction_measured"] == ""
    assert "salt pan" in rows[0]["environment"]


def test_subsurface_rows_carry_no_timestamp_and_are_spatial_only(tmp_path: Path) -> None:
    """A crust transect is not a series, and must not be dressed as one."""
    src = tmp_path / "saltconc_V2.tab"
    src.write_text(
        "/* DATA DESCRIPTION:\n"
        "License:\tCreative Commons Attribution 4.0 International (CC-BY-4.0)\n"
        "*/\n"
        "Sample label\tZ [cm]\tX [cm]\tWater wm [g]\tSand [g]\t"
        "Salt [g] (direct)\tSalt [g] (indirect)\n"
        '"(1,1)"\t3\t0\t4.1512\t9.75\t2.88\t3.28\n',
        encoding="utf-8",
    )
    dst = tmp_path / "out.csv"
    normalize.pangaea_subsurface(src, dst, folder="aux_salt_pan_owens_lake_subsurface")
    columns, _ = _read(dst)

    for stamp in ("datetime", "timestamp", "date", "event_time"):
        assert stamp not in columns
    assert "z" in columns and "x" in columns
