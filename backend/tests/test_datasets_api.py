"""Module 07 - the research dataset module, exercised over HTTP.

The tests are organised around the claims the module makes rather than around its
endpoints, because the claims are what a reviewer will challenge: that a row can
only be assembled from stored measurements, that the target is configured rather
than invented, that the report is available before training, that the content
hash reproduces, and that a measurement taken after the outcome it predicts can
never become a feature of it.

What these tests cannot do is prove the module is not leaky in some way nobody
thought of. They prove the checks it does make are real, that they run on every
build, and that the exclusions it documents match the audit it performs.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.datasets import (
    CONTENT_HASH_HEADER,
    DRIFT_HEADER,
    RECORDED_HASH_HEADER,
    get_dataset_service,
    get_dataset_status_service,
)
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.weather import get_weather_service, get_weather_status_service
from app.main import app
from app.services.datasets import DatasetService
from app.services.observations import ObservationService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

VALID_PAN = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 10_000.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}

#: Four daily readings a day apart. Under a 24-hour horizon the first three
#: anchors each find a later reading to be their label and the fourth finds
#: nothing, so one unlabelled row is built rather than silently dropped.
OBSERVATIONS: list[dict[str, Any]] = [
    {
        "timestamp": "2026-03-01T06:00:00Z",
        "salinity_g_l": 280.0,
        "water_depth_cm": 20.0,
        "brine_temperature_c": 31.0,
        "humidity_percent": 60.0,
        "ambient_temperature_c": 27.0,
    },
    {
        "timestamp": "2026-03-02T06:00:00Z",
        "salinity_g_l": 265.0,
        "water_depth_cm": 24.0,
        "brine_temperature_c": 30.0,
        "humidity_percent": 70.0,
        "ambient_temperature_c": 25.0,
    },
    {
        "timestamp": "2026-03-03T06:00:00Z",
        "salinity_g_l": 250.0,
        "water_depth_cm": 28.0,
        "brine_temperature_c": 29.0,
        "humidity_percent": 80.0,
        "ambient_temperature_c": 24.0,
    },
    {
        "timestamp": "2026-03-04T06:00:00Z",
        "salinity_g_l": 240.0,
        "water_depth_cm": 30.0,
        "brine_temperature_c": 28.0,
        "humidity_percent": 75.0,
        "ambient_temperature_c": 26.0,
    },
]

#: The regression target these readings support. A pan with four daily readings
#: is enough to test the mechanics, not to train anything: the report says so in
#: its own warnings, and one of the tests below checks that it does.
SALINITY_TARGET = {"name": "post_rain_salinity_g_l", "horizon_hours": 24.0}


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def dataset_client(database: FakeDatabase) -> Any:
    """A TestClient whose pan, observation and dataset routes are backed by memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = (
        lambda: ObservationService(database)
    )
    app.dependency_overrides[get_observation_status_service] = (
        lambda: ObservationService(database)
    )
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_weather_status_service] = (
        lambda: WeatherService(database)
    )
    app.dependency_overrides[get_dataset_service] = lambda: DatasetService(database)
    app.dependency_overrides[get_dataset_status_service] = (
        lambda: DatasetService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _backdate_pan_registration(database: FakeDatabase, moment: str) -> None:
    """Move the stored pan's ``created_at`` to before the readings it describes.

    A pan registered today cannot be a static property of a March anchor, and the
    builder says so rather than borrowing the area backwards in time. Most tests
    want the opposite: a pan that existed, so its area and the physics columns
    derived from it are populated.
    """
    collection = database["salt_pans"]
    stored = collection.documents()[0]
    collection._documents[0] = {**stored, "created_at": datetime.fromisoformat(moment)}


@pytest.fixture()
def seeded(dataset_client: TestClient, database: FakeDatabase) -> TestClient:
    """One registered pan, present before its readings, and four daily readings."""
    response = dataset_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    _backdate_pan_registration(database, "2026-02-01T00:00:00+00:00")
    for reading in OBSERVATIONS:
        created = dataset_client.post(
            "/api/observations/", json={**reading, "pan_id": VALID_PAN["pan_id"]}
        )
        assert created.status_code == 201, created.text
    return dataset_client


def build(client: TestClient, **overrides: Any) -> Any:
    """Build a dataset, with request fields overridden or removed by passing None."""
    body: dict[str, Any] = {"target": dict(SALINITY_TARGET), **overrides}
    if "target" in overrides:
        body["target"] = {**SALINITY_TARGET, **overrides["target"]}
    return client.post("/api/datasets/", json=body)


def build_ok(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = build(client, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


def report_of(client: TestClient, **overrides: Any) -> dict[str, Any]:
    return build_ok(client, **overrides)["dataset"]["report"]


def distribution(report: dict[str, Any], column: str) -> dict[str, Any]:
    """The distribution block for one column, or a failure naming what is there."""
    for block in report["distributions"]:
        if block["column"] == column:
            return block
    raise AssertionError(
        f"no distribution for {column}; the report covers "
        f"{[block['column'] for block in report['distributions']]}"
    )


def missing_of(report: dict[str, Any], column: str) -> dict[str, Any]:
    """The missingness block for one column, or a failure naming what is there."""
    for block in report["missing_values"]:
        if block["column"] == column:
            return block
    raise AssertionError(f"no missingness block for {column}")


def rows_of(client: TestClient, dataset_id: str) -> list[dict[str, str]]:
    """The dataset's CSV, parsed, for assertions about what a file actually holds."""
    response = client.get(f"/api/datasets/{dataset_id}/export")
    assert response.status_code == 200, response.text
    return list(csv.DictReader(io.StringIO(response.text)))


# -- the catalogue -------------------------------------------------------------


def test_the_catalogue_is_served_without_a_database(dataset_client):
    """A researcher can read the feature set before there is data to build it from."""
    body = dataset_client.get("/api/datasets/catalog").json()

    assert body["features"], "the catalogue must list the features it can build"
    assert body["targets"], "the catalogue must list the targets a build may name"
    assert body["leakage_policy"]
    assert body["leakage_checks"]
    assert body["row_keys"]


def test_every_catalogue_feature_documents_its_origin(dataset_client):
    """No column exists without a source, a selection rule and a scientific basis."""
    for feature in dataset_client.get("/api/datasets/catalog").json()["features"]:
        assert feature["source_collection"], feature["name"]
        assert feature["source_field"], feature["name"]
        assert feature["selection_rule"], feature["name"]
        assert feature["derivation"], feature["name"]
        assert feature["scientific_basis"], feature["name"]
        assert feature["leakage_class"] in {"as_of", "static", "future"}, feature["name"]


def test_the_catalogue_offers_every_feature_the_module_brief_names(dataset_client):
    features = {
        feature["name"]
        for feature in dataset_client.get("/api/datasets/catalog").json()["features"]
    }

    assert {
        "current_salinity_g_l",
        "water_depth_cm",
        "brine_temperature_c",
        "observation_ambient_temperature_c",
        "observation_humidity_percent",
        "forecast_rainfall_mm",
        "forecast_rainfall_probability_percent",
        "recent_rainfall_mm",
        "recent_rainfall_record_count",
        "salinity_change_g_l",
        "depth_change_cm",
        "physics_predicted_post_rain_salinity_g_l",
        "physics_predicted_dilution_percent",
        "hour_of_day_utc",
        "day_of_year",
        "season",
    } <= features


def test_the_catalogue_keeps_weather_and_observation_instruments_apart(
    dataset_client,
):
    """Two instruments are two columns, never averaged into one."""
    features = {
        feature["name"]: feature
        for feature in dataset_client.get("/api/datasets/catalog").json()["features"]
    }

    assert features["observation_humidity_percent"]["source_field"] == (
        "humidity_percent"
    )
    assert features["observation_humidity_percent"]["source_collection"] == (
        "pan_observations"
    )
    assert features["weather_humidity_percent"]["source_collection"] == (
        "weather_observations"
    )


def test_the_catalogue_names_the_fields_it_refuses(dataset_client):
    """Exclusions are documented, so their absence reads as a decision."""
    excluded = dataset_client.get("/api/datasets/catalog").json()["excluded_fields"]
    fields = {entry["field"] for entry in excluded}

    assert "actual_outcomes.actual_rainfall_mm" in fields
    assert "actual_outcomes.post_event_salinity_g_l" in fields
    assert "salt_pans.pan_id" in fields
    assert {entry["reason_kind"] for entry in excluded} == {
        "future_measurement",
        "row_identifier",
    }
    assert all(entry["reason"] for entry in excluded)


def test_the_audit_and_the_catalogue_report_the_same_exclusions(seeded):
    """Two renderings of what was left out could let a reader conclude they differ."""
    catalogue = {
        entry["field"]
        for entry in seeded.get("/api/datasets/catalog").json()["excluded_fields"]
    }
    audit = {
        entry["field"]
        for entry in build_ok(seeded)["dataset"]["report"]["leakage_audit"][
            "excluded_fields"
        ]
    }

    assert catalogue == audit


def test_the_catalogue_offers_no_default_target(dataset_client):
    """Targets are named by the caller, so the catalogue must not imply one."""
    body = dataset_client.get("/api/datasets/catalog").json()

    assert "default" not in body["defaults"]
    assert "default_target" not in body
    assert body["defaults"]["min_horizon_hours"] >= 1.0


# -- configurable targets ------------------------------------------------------


def test_a_build_without_a_target_is_refused(seeded):
    """A dataset with an invented label is the failure this module exists to prevent."""
    response = seeded.post("/api/datasets/", json={})

    assert response.status_code == 422
    assert "target" in response.text


def test_an_unknown_target_is_refused_and_names_the_available_ones(seeded):
    response = build(seeded, target={"name": "tomorrows_bananas"})

    assert response.status_code == 422
    assert "post_rain_salinity_g_l" in response.text


def test_the_server_owns_the_label_column_and_the_kind(seeded):
    """The client cannot describe its own label, so two builds cannot disagree."""
    body = build_ok(seeded)

    assert body["dataset"]["target"]["label_column"] == "label_post_rain_salinity_g_l"
    assert body["dataset"]["target"]["kind"] == "regression"
    assert body["preview_columns"][-3] == "label_post_rain_salinity_g_l"


def test_a_client_cannot_post_its_own_label_column(seeded):
    response = seeded.post(
        "/api/datasets/",
        json={"target": {**SALINITY_TARGET, "label_column": "salinity"}},
    )

    assert response.status_code == 422


def test_a_horizon_of_zero_is_refused(seeded):
    """A label measured at the anchor describes the state, not its consequence."""
    response = build(seeded, target={"horizon_hours": 0.0})

    assert response.status_code == 422
    assert "horizon" in response.text.lower()


def test_a_negative_horizon_is_refused(seeded):
    assert build(seeded, target={"horizon_hours": -5.0}).status_code == 422


def test_a_banded_target_refuses_to_build_without_bin_edges(seeded):
    response = build(
        seeded, target={"name": "post_rain_salinity_band", "horizon_hours": 24.0}
    )

    assert response.status_code == 422
    assert "bin_edges" in response.text


def test_bin_edges_are_refused_for_a_target_that_has_no_use_for_them(seeded):
    """Edges on a regression target would be hashed while changing nothing."""
    response = build(seeded, target={"bin_edges": [200.0, 300.0]})

    assert response.status_code == 422
    assert "bin_edges" in response.text


def test_a_banded_target_builds_when_the_caller_supplies_the_edges(seeded):
    body = build_ok(
        seeded,
        target={
            "name": "post_rain_salinity_band",
            "horizon_hours": 24.0,
            "bin_edges": [230.0, 260.0, 290.0],
        },
    )

    assert body["dataset"]["target"]["kind"] == "classification"
    assert body["dataset"]["target"]["bin_edges"] == [230.0, 260.0, 290.0]
    assert set(body["dataset"]["report"]["target_distribution"]["class_counts"]) <= {
        "<230",
        "230-260",
        "260-290",
        ">=290",
    }


def test_non_ascending_bin_edges_are_refused(seeded):
    response = build(
        seeded,
        target={
            "name": "post_rain_salinity_band",
            "horizon_hours": 24.0,
            "bin_edges": [300.0, 200.0],
        },
    )

    assert response.status_code == 422


def test_the_class_distribution_reports_the_balance_it_was_given(seeded):
    """A classification target reports how its rows spread across the bands."""
    body = build_ok(
        seeded,
        target={
            "name": "post_rain_salinity_band",
            "horizon_hours": 24.0,
            "bin_edges": [230.0, 260.0, 290.0],
        },
    )
    block = body["dataset"]["report"]["target_distribution"]

    assert block["kind"] == "classification"
    assert block["label_column"] == "label_post_rain_salinity_band"
    assert set(block["class_counts"]) <= {"<230", "230-260", "260-290", ">=290"}
    assert sum(block["class_counts"].values()) == 3
    assert block["imbalance_ratio"] is not None


def test_the_harvest_outcome_target_reads_the_operators_recorded_vocabulary(seeded):
    """A classification target whose outcomes were never recorded labels nothing."""
    body = build_ok(seeded, target={"name": "harvest_outcome", "horizon_hours": 24.0})

    assert body["dataset"]["target"]["kind"] == "classification"
    assert body["dataset"]["report"]["labelled_count"] == 0
    assert body["dataset"]["report"]["unlabelled_count"] == 4


# -- nothing can be invented ---------------------------------------------------


def test_no_endpoint_accepts_a_measurement(seeded):
    """The schema refuses extra fields, so a row cannot be introduced by hand."""
    for field in ("rows", "observations", "record_count", "salinity_g_l"):
        response = seeded.post(
            "/api/datasets/", json={"target": SALINITY_TARGET, field: [1, 2, 3]}
        )
        assert response.status_code == 422, field


def test_an_unknown_feature_name_is_refused_rather_than_silently_dropped(seeded):
    response = build(seeded, features=["current_salinity_g_l", "not_a_feature"])

    assert response.status_code == 422
    assert "not_a_feature" in response.text


def test_an_empty_feature_list_is_refused(seeded):
    """A dataset with no features cannot say anything about anything."""
    assert build(seeded, features=[]).status_code == 422


def test_a_narrow_feature_selection_is_honoured_in_the_file(seeded):
    body = build_ok(
        seeded,
        features=["current_salinity_g_l", "forecast_rainfall_mm"],
        preview_rows=1,
    )

    assert body["dataset"]["feature_names"] == [
        "current_salinity_g_l",
        "forecast_rainfall_mm",
    ]
    assert "water_depth_cm" not in body["preview_columns"]


# -- the report arrives before training ----------------------------------------


def test_a_build_returns_a_report_not_just_rows(seeded):
    report = report_of(seeded)

    assert report["record_count"] == 4
    assert report["pan_count"] == 1
    assert report["pan_ids"] == ["kzn-2026-a01"]


def test_the_report_states_its_date_range(seeded):
    date_range = report_of(seeded)["date_range"]

    assert date_range["first_anchor"].startswith("2026-03-01")
    assert date_range["last_anchor"].startswith("2026-03-04")
    assert date_range["span_days"] == 3.0


def test_the_report_states_per_column_missing_values_with_reasons(seeded):
    report = report_of(seeded)

    salinity = missing_of(report, "current_salinity_g_l")
    assert salinity["missing_count"] == 0
    assert salinity["present_count"] == 4

    change = missing_of(report, "salinity_change_g_l")
    assert change["missing_count"] == 1, "the first anchor has no earlier reading"
    assert "No earlier observation" in change["reason"]


def test_the_report_flags_a_column_nothing_backs(seeded):
    """No weather was recorded, so the rainfall columns are empty and say why."""
    report = report_of(seeded)
    rainfall = missing_of(report, "recent_rainfall_mm")

    assert rainfall["missing_count"] == 4
    assert rainfall["coverage_warning"] is True
    assert "lookback window" in rainfall["reason"]


def test_the_report_carries_a_numeric_distribution_per_feature(seeded):
    salinity = distribution(report_of(seeded), "current_salinity_g_l")

    assert salinity["kind"] == "numeric"
    assert salinity["unit"] == "g/L"
    assert salinity["numeric"]["count"] == 4
    assert salinity["numeric"]["minimum"] == 240.0
    assert salinity["numeric"]["maximum"] == 280.0
    assert salinity["numeric"]["mean"] == pytest.approx(258.75)
    assert salinity["description"], "a distribution must say what the column means"


def test_the_report_reports_no_statistic_for_a_column_with_no_values(seeded):
    """A mean over nothing is null with a reason, never zero."""
    rainfall = distribution(report_of(seeded), "weather_humidity_percent")

    assert rainfall["numeric"]["count"] == 0
    assert rainfall["numeric"]["mean"] is None
    assert rainfall["numeric"]["empty_reason"]


def test_the_report_carries_a_categorical_distribution(seeded):
    season = distribution(report_of(seeded), "season")

    assert season["kind"] == "categorical"
    assert season["categorical"]["values"] == {"spring": 4}
    assert season["categorical"]["distinct"] == 1


def test_the_report_names_the_data_source_of_every_row(seeded):
    sources = report_of(seeded)["data_sources"]

    assert sources["collection_counts"]["pan_observations"] == 4
    assert sources["collection_counts"]["salt_pans"] == 1
    assert sources["observation_sources"] == {"manual": 4}
    assert sources["source_windows"]["pan_observations"]["count"] == 4


# -- demonstration records are stored but are not evidence -------------------


def test_a_demonstration_observation_is_stored_and_labelled_as_one(seeded):
    """Typing a value in is allowed; calling it a measurement is not.

    The record has to survive, because the operator's demonstration is a thing
    they did and the dashboard reads it back. What it may not do is arrive
    without the field that says where it came from.
    """
    created = seeded.post(
        "/api/observations/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-02T12:00:00Z",
            "salinity_g_l": 250.0,
            "water_depth_cm": 20.0,
            "brine_temperature_c": 32.0,
            "data_source": "demonstration",
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["data_source"] == "demonstration"


def test_a_demonstration_observation_never_becomes_a_dataset_row(seeded):
    """A demonstration value is not a datapoint, however real the record looks.

    The reading lands between two measured readings, so without the exclusion it
    would become an anchor of its own, contribute a salinity-change feature to the
    rows either side of it, and appear in the provenance counts as a fourth source.
    """
    seeded.post(
        "/api/observations/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-02T12:00:00Z",
            "salinity_g_l": 250.0,
            "water_depth_cm": 20.0,
            "brine_temperature_c": 32.0,
            "data_source": "demonstration",
        },
    )

    report = report_of(seeded)

    assert report["data_sources"]["collection_counts"]["pan_observations"] == 4
    assert report["data_sources"]["observation_sources"] == {"manual": 4}


def test_a_demonstration_forecast_never_rains_on_a_dataset_row(seeded):
    """A typed-in forecast must not become the rain a research row was measured against.

    This is the failure that matters most for the weather side, because a
    forecast is an input to a row's features rather than a row of its own. A
    demonstration rainfall figure would set ``recent_rainfall_mm`` and the
    rain-event label on rows whose pan was never actually rained on.
    """
    created = seeded.post(
        "/api/weather/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-03T12:00:00Z",
            "forecast_for": "2026-03-03T18:00:00Z",
            "rainfall_mm": 25.0,
            "rainfall_probability_percent": 80.0,
            "source": "demonstration",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "demonstration"

    report = report_of(seeded)

    assert report["data_sources"]["collection_counts"]["weather_observations"] == 0
    assert "demonstration" not in report["data_sources"]["weather_sources"]


def test_the_report_counts_the_demonstration_records_it_withheld(seeded):
    """A dataset that quietly drops records is indistinguishable from one without them.

    A researcher comparing counts against the operational database would otherwise
    have no way to tell that the difference was the isolation working as intended
    rather than records lost to a cap or a query.
    """
    for timestamp in ("2026-03-02T12:00:00Z", "2026-03-02T13:00:00Z"):
        seeded.post(
            "/api/observations/",
            json={
                "pan_id": VALID_PAN["pan_id"],
                "timestamp": timestamp,
                "salinity_g_l": 250.0,
                "water_depth_cm": 20.0,
                "data_source": "demonstration",
            },
        )
    seeded.post(
        "/api/weather/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-03T12:00:00Z",
            "forecast_for": "2026-03-03T18:00:00Z",
            "rainfall_mm": 25.0,
            "source": "demonstration",
        },
    )

    excluded = report_of(seeded)["data_sources"]["demonstration_records_excluded"]

    assert excluded == {"pan_observations": 2, "weather_observations": 1}


def test_a_demonstration_record_does_not_displace_a_measured_one(seeded):
    """The exclusion happens in the query, so the cap is spent on real records.

    Filtering after loading would leave a demonstration record having consumed
    part of the record budget, which is the difference between "these are the
    first N research records" and "these are N records of which some were dropped".
    """
    for index in range(6):
        seeded.post(
            "/api/observations/",
            json={
                "pan_id": VALID_PAN["pan_id"],
                "timestamp": f"2026-03-05T0{index}:00:00Z",
                "salinity_g_l": 250.0,
                "water_depth_cm": 20.0,
                "data_source": "demonstration",
            },
        )

    built = build_ok(seeded, max_source_records=4)

    assert built["dataset"]["report"]["record_count"] == 4
    assert built["dataset"]["report"]["data_sources"]["collection_counts"][
        "pan_observations"
    ] == 4


def test_the_report_counts_duplicates(seeded):
    duplicates = report_of(seeded)["duplicates"]

    assert duplicates["duplicate_group_count"] == 0
    assert duplicates["duplicate_row_count"] == 0
    assert duplicates["definition"], "the reader must know what counts as a duplicate"


def test_the_report_warns_that_four_rows_cannot_support_a_split(seeded):
    """The report says the dataset is too small before anyone trains on it."""
    warnings = " ".join(report_of(seeded)["warnings"])

    assert "below the 50" in warnings
    assert "descriptive" in warnings


def test_the_report_records_the_leakage_audit(seeded):
    audit = report_of(seeded)["leakage_audit"]

    assert audit["rows_checked"] == 4
    assert audit["rows_passed"] == 4
    assert audit["violations"] == []
    assert audit["policy"]
    assert audit["checks"]


def test_unmeasured_outcomes_are_kept_and_counted_rather_than_dropped(seeded):
    """The cost of the measurement backlog stays visible instead of being filtered."""
    report = report_of(seeded)

    assert report["record_count"] == 4
    assert report["labelled_count"] == 3
    assert report["unlabelled_count"] == 1
    assert report["labelled_count"] + report["unlabelled_count"] == report["record_count"]
    assert "no target and cannot be trained on" in report["target_distribution"]["note"]


def test_excluding_unlabelled_rows_returns_only_measured_outcomes(seeded):
    report = report_of(seeded, include_unlabelled=False)

    assert report["record_count"] == 3
    assert report["unlabelled_count"] == 0


def test_the_report_can_be_narrowed_to_pans_and_to_a_window(seeded):
    report = report_of(
        seeded, start="2026-03-02T00:00:00Z", end="2026-03-03T23:59:00Z"
    )

    assert report["record_count"] == 2
    assert report["date_range"]["first_anchor"].startswith("2026-03-02")
    assert report["date_range"]["last_anchor"].startswith("2026-03-03")


# -- leakage -------------------------------------------------------------------


def test_a_rows_label_is_measured_after_its_anchor_not_at_it(seeded):
    body = build_ok(seeded, preview_rows=50)
    rows = {row["anchor_timestamp"]: row for row in body["preview_rows"]}

    first = rows["2026-03-01T06:00:00Z"]
    assert first["current_salinity_g_l"] == 280.0, "the state being predicted"
    assert first["label_post_rain_salinity_g_l"] == 265.0, "its consequence"
    assert first["target_measured_at"].startswith("2026-03-02")
    assert first["target_source_id"], "the label traces to the reading that measured it"


def test_no_row_is_labelled_before_its_own_horizon_has_elapsed(seeded):
    labelled = [
        row
        for row in build_ok(seeded, preview_rows=50)["preview_rows"]
        if row["is_labelled"]
    ]

    assert len(labelled) == 3
    for row in labelled:
        anchor = datetime.fromisoformat(row["anchor_timestamp"])
        measured = datetime.fromisoformat(row["target_measured_at"])
        assert measured >= anchor + timedelta(hours=24.0)


def test_the_horizon_actually_moves_the_label(seeded):
    """A longer horizon must reach a later reading, or the horizon means nothing."""
    short = {
        row["anchor_timestamp"]: row["label_post_rain_salinity_g_l"]
        for row in build_ok(seeded, preview_rows=50)["preview_rows"]
    }
    long = {
        row["anchor_timestamp"]: row["label_post_rain_salinity_g_l"]
        for row in build_ok(
            seeded, target={"horizon_hours": 48.0}, preview_rows=50
        )["preview_rows"]
    }

    assert short["2026-03-01T06:00:00Z"] == 265.0
    assert long["2026-03-01T06:00:00Z"] == 250.0


def test_the_pan_identifier_is_a_row_key_and_not_a_feature(seeded):
    """An identifier in the training set lets a model memorise per-pan outcomes."""
    body = build_ok(seeded)

    assert "pan_id" not in body["dataset"]["feature_names"]
    assert "pan_id" in body["dataset"]["row_keys"]
    assert "pan_id" in body["preview_columns"], "the file still says which pan"


def test_the_anchor_is_a_key_and_not_a_feature(seeded):
    body = build_ok(seeded)

    assert "anchor_timestamp" not in body["dataset"]["feature_names"]
    assert "anchor_timestamp" in body["dataset"]["row_keys"]


def test_a_pan_registered_after_the_anchor_has_no_area_in_that_row(seeded, database):
    """A static feature needs the registration to have happened by then."""
    _backdate_pan_registration(database, "2026-06-01T00:00:00+00:00")

    report = report_of(seeded)
    area = missing_of(report, "pan_area_m2")

    assert area["missing_count"] == 4
    assert "not been registered at the anchor" in area["reason"]


def test_the_physics_prediction_uses_the_forecast_and_never_the_rain_that_fell(seeded):
    """A forecast issued before the anchor drives the prediction; later rain cannot."""
    observed = seeded.post(
        "/api/weather/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-02-28T00:00:00Z",
            "rainfall_mm": 0.0,
            "ambient_temperature_c": 26.0,
            "humidity_percent": 65.0,
        },
    )
    assert observed.status_code == 201, observed.text
    forecast = seeded.post(
        "/api/weather/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-02-28T06:00:00Z",
            "forecast_for": "2026-03-01T12:00:00Z",
            "rainfall_mm": 20.0,
            "rainfall_probability_percent": 70.0,
        },
    )
    assert forecast.status_code == 201, forecast.text
    # Rain that fell on the anchor day. It is a future measurement for every
    # earlier anchor, so it must not reach the physics columns.
    rain = seeded.post(
        "/api/weather/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-01T12:00:00Z",
            "rainfall_mm": 90.0,
        },
    )
    assert rain.status_code == 201, rain.text

    body = build_ok(seeded, preview_rows=50)
    rows = {row["anchor_timestamp"]: row for row in body["preview_rows"]}
    first = rows["2026-03-01T06:00:00Z"]

    assert first["forecast_rainfall_mm"] == 20.0
    assert first["forecast_rainfall_probability_percent"] == 70.0
    assert first["physics_prediction_rainfall_source"] == "forecast"
    # C1 = C0 * V0 / V1 with V0 = 10_000 * 0.20 = 2_000 m3, Vr = 10_000 * 0.02
    # = 200 m3, so C1 = 280 * 2_000 / 2_200.
    assert first["physics_predicted_post_rain_salinity_g_l"] == pytest.approx(
        280.0 * 2_000.0 / 2_200.0
    )
    assert first["physics_predicted_dilution_percent"] == pytest.approx(
        (280.0 - 280.0 * 2_000.0 / 2_200.0) / 280.0 * 100.0
    )
    assert body["dataset"]["report"]["data_sources"]["rows_using_forecast_rainfall"] > 0


def test_a_row_with_no_forecast_in_force_predicts_nothing_rather_than_using_real_rain(
    seeded,
):
    """With no forecast, the physics columns are empty even though rain later fell."""
    seeded.post(
        "/api/weather/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-01T12:00:00Z",
            "rainfall_mm": 90.0,
        },
    )

    first = build_ok(seeded, preview_rows=1)["preview_rows"][0]

    assert first["forecast_rainfall_mm"] is None
    assert first["physics_predicted_post_rain_salinity_g_l"] is None
    assert "forecast" in first["missing_features"]


def test_the_audit_refuses_a_leaked_dataset_rather_than_flagging_one(seeded, monkeypatch):
    """A leaked dataset is not a dataset with a warning attached to it."""
    from app.models.dataset import LeakageViolation
    from app.services import dataset_builder

    def leaky(self, rows, spec):
        return dataset_builder.LeakageAudit(
            policy=dataset_builder.LEAKAGE_POLICY,
            rows_checked=len(rows),
            rows_passed=0,
            violations=[
                LeakageViolation(
                    row_id=rows[0].row_id,
                    field="current_salinity_g_l",
                    reason="read from a record after the anchor",
                )
            ],
            excluded_fields=dataset_builder.excluded_field_records(),
            checks=list(dataset_builder.LEAKAGE_CHECKS),
        )

    monkeypatch.setattr(dataset_builder.DatasetBuilder, "_audit", leaky)

    response = build(seeded)

    assert response.status_code == 500
    assert "causal ordering" in response.text
    assert "current_salinity_g_l" in response.text
    assert seeded.get("/api/datasets/").json()["total"] == 0, (
        "a refused build must leave no dataset behind"
    )


# -- the preview ---------------------------------------------------------------


def test_a_preview_reports_how_much_of_it_is_not_being_shown(seeded):
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    payload = seeded.get(
        f"/api/datasets/{dataset_id}/preview", params={"limit": 2}
    ).json()

    assert payload["total_rows"] == 4
    assert payload["returned"] == 2
    assert payload["drift"] is False
    assert payload["note"] is None


def test_a_preview_of_zero_rows_still_names_the_columns(seeded):
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    payload = seeded.get(
        f"/api/datasets/{dataset_id}/preview", params={"limit": 0}
    ).json()

    assert payload["returned"] == 0
    assert payload["rows"] == []
    assert "current_salinity_g_l" in payload["columns"]


def test_a_preview_agrees_with_the_exported_file_row_for_row(seeded):
    """A preview built by different code would describe a different dataset."""
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]

    preview = seeded.get(
        f"/api/datasets/{dataset_id}/preview", params={"limit": 50}
    ).json()
    exported = rows_of(seeded, dataset_id)

    assert [row["row_id"] for row in exported] == [
        row["row_id"] for row in preview["rows"]
    ]
    assert exported[0]["current_salinity_g_l"] == str(
        preview["rows"][0]["current_salinity_g_l"]
    )


# -- CSV export ----------------------------------------------------------------


def test_the_export_is_a_csv_named_for_its_dataset(seeded):
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    response = seeded.get(f"/api/datasets/{dataset_id}/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert "post_rain_salinity_g_l" in disposition
    assert ".csv" in disposition


def test_the_export_carries_both_hashes_and_the_drift_flag(seeded):
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    response = seeded.get(f"/api/datasets/{dataset_id}/export")

    assert response.headers[CONTENT_HASH_HEADER].startswith("sha256:")
    assert response.headers[RECORDED_HASH_HEADER].startswith("sha256:")
    assert response.headers[DRIFT_HEADER] == "false"
    assert response.headers[CONTENT_HASH_HEADER] == response.headers[
        RECORDED_HASH_HEADER
    ], "an unbackfilled dataset's export must still match its manifest"


def test_the_export_writes_the_keys_the_features_and_the_label(seeded):
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    columns = next(csv.reader(io.StringIO(seeded.get(f"/api/datasets/{dataset_id}/export").text)))

    assert columns[:5] == [
        "row_id",
        "pan_id",
        "anchor_timestamp",
        "target_measured_at",
        "target_source_id",
    ]
    assert "current_salinity_g_l" in columns
    assert columns[-3:] == [
        "label_post_rain_salinity_g_l",
        "is_labelled",
        "missing_features",
    ]


def test_an_empty_cell_means_unmeasured_and_zero_means_measured(seeded):
    """The distinction survives CSV, so nothing writes a zero into a gap."""
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    first = rows_of(seeded, dataset_id)[0]

    assert first["current_salinity_g_l"] == "280.0"
    assert first["forecast_rainfall_mm"] == "", (
        "no forecast was in force, so the cell is empty rather than 0.0"
    )
    assert first["salinity_change_g_l"] == "", (
        "no earlier reading exists, so no change can be computed"
    )


def test_the_export_states_why_a_row_is_unlabelled(seeded):
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    unlabelled = [row for row in rows_of(seeded, dataset_id) if row["is_labelled"] == "false"]

    assert len(unlabelled) == 1
    assert unlabelled[0]["label_post_rain_salinity_g_l"] == ""
    assert "salinity_g_l" in unlabelled[0]["missing_features"]


def test_the_exported_hash_matches_a_second_export_of_the_same_records(seeded):
    """Two exports with no backfill between them must be byte-identical."""
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]

    first = seeded.get(f"/api/datasets/{dataset_id}/export")
    second = seeded.get(f"/api/datasets/{dataset_id}/export")

    assert first.text == second.text
    assert first.headers[CONTENT_HASH_HEADER] == second.headers[CONTENT_HASH_HEADER]


# -- version and hash ----------------------------------------------------------


def test_a_build_is_stamped_with_a_content_hash_and_a_version(seeded):
    body = build_ok(seeded)

    assert body["dataset"]["content_hash"] == (
        "sha256:" + body["dataset"]["content_hash"].removeprefix("sha256:")
    )
    assert len(body["dataset"]["content_hash"]) == len("sha256:") + 64
    assert body["dataset"]["short_hash"] == body["dataset"]["content_hash"][
        len("sha256:") :
    ][:12]
    assert body["dataset"]["version"] == 1
    assert body["created"] is True
    assert body["reused_reason"] is None


def test_building_the_same_specification_twice_returns_one_dataset(seeded):
    """Same specification over same records is one dataset, not a second copy."""
    first = build_ok(seeded)
    response = build(seeded)

    assert response.status_code == 200, "an existing dataset is not a new creation"
    assert response.json()["created"] is False
    assert response.json()["dataset"]["dataset_id"] == first["dataset"]["dataset_id"]
    assert response.json()["reused_reason"]
    assert seeded.get("/api/datasets/").json()["total"] == 1


def test_changing_the_horizon_makes_a_different_dataset(seeded):
    """The horizon is part of the identity, because it changes what the label means."""
    first = build_ok(seeded)
    second = build_ok(seeded, target={"horizon_hours": 48.0})

    assert second["dataset"]["content_hash"] != first["dataset"]["content_hash"]
    assert second["dataset"]["version"] == 2
    assert second["dataset"]["supersedes_content_hash"] == first["dataset"][
        "content_hash"
    ]


def test_changing_a_bin_edge_makes_a_different_dataset(seeded):
    band = {"name": "post_rain_salinity_band", "horizon_hours": 24.0}
    first = build_ok(seeded, target={**band, "bin_edges": [230.0, 260.0]})
    second = build_ok(seeded, target={**band, "bin_edges": [240.0, 270.0]})

    assert second["dataset"]["content_hash"] != first["dataset"]["content_hash"]


def test_selecting_different_features_makes_a_different_dataset(seeded):
    first = build_ok(seeded)
    second = build_ok(seeded, features=["current_salinity_g_l"])

    assert second["dataset"]["content_hash"] != first["dataset"]["content_hash"]


def test_renaming_a_dataset_does_not_make_a_new_one(seeded):
    """The name is for a human reader and is never part of the identity."""
    first = build_ok(seeded, name="first pass")
    response = build(seeded, name="renamed")

    assert response.json()["created"] is False
    assert response.json()["dataset"]["dataset_id"] == first["dataset"]["dataset_id"]


def test_a_rebuild_of_unchanged_records_reports_no_change(seeded):
    """This is the reproducibility claim made concrete."""
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    response = seeded.post(f"/api/datasets/{dataset_id}/rebuild")

    assert response.status_code == 200
    body = response.json()
    assert body["rebuild"]["unchanged"] is True
    assert body["rebuild"]["previous_dataset_id"] is None
    assert body["dataset"]["dataset_id"] == dataset_id
    assert seeded.get("/api/datasets/").json()["total"] == 1


def test_a_rebuild_after_a_backfill_stores_a_successor(seeded):
    """The earlier experiment stays citable against the data as it was."""
    original = build_ok(seeded)["dataset"]
    backfill = seeded.post(
        "/api/observations/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-05T06:00:00Z",
            "salinity_g_l": 235.0,
            "water_depth_cm": 31.0,
            "brine_temperature_c": 28.0,
        },
    )
    assert backfill.status_code == 201, backfill.text

    response = seeded.post(f"/api/datasets/{original['dataset_id']}/rebuild")

    assert response.status_code == 201
    body = response.json()
    assert body["rebuild"]["unchanged"] is False
    assert body["rebuild"]["previous_dataset_id"] == original["dataset_id"]
    assert body["rebuild"]["previous_content_hash"] == original["content_hash"]
    assert body["dataset"]["version"] == original["version"] + 1
    assert body["dataset"]["supersedes_content_hash"] == original["content_hash"]
    assert body["dataset"]["content_hash"] != original["content_hash"]
    assert seeded.get(
        f"/api/datasets/{original['dataset_id']}"
    ).json()["content_hash"] == original["content_hash"], (
        "the superseded manifest must stay readable for the earlier experiment"
    )


def test_the_backfilled_reading_labels_a_row_that_was_previously_unlabelled(seeded):
    """Drift is the backlog shrinking, and the successor shows it."""
    original = build_ok(seeded)["dataset"]
    assert original["report"]["unlabelled_count"] == 1
    assert original["report"]["labelled_count"] == 3
    seeded.post(
        "/api/observations/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-05T06:00:00Z",
            "salinity_g_l": 235.0,
        },
    )

    report = seeded.post(f"/api/datasets/{original['dataset_id']}/rebuild").json()[
        "dataset"
    ]["report"]

    assert report["record_count"] == 5, "the new reading is an anchor in its own right"
    assert report["labelled_count"] == 4, (
        "the reading measured the 03-04 anchor's 24 h horizon"
    )


def test_a_stale_dataset_reports_drift_on_preview_and_export(seeded):
    dataset_id = build_ok(seeded)["dataset"]["dataset_id"]
    backfill = seeded.post(
        "/api/observations/",
        json={
            "pan_id": VALID_PAN["pan_id"],
            "timestamp": "2026-03-05T06:00:00Z",
            "salinity_g_l": 235.0,
            "water_depth_cm": 31.0,
        },
    )
    assert backfill.status_code == 201, backfill.text

    preview = seeded.get(f"/api/datasets/{dataset_id}/preview").json()
    exported = seeded.get(f"/api/datasets/{dataset_id}/export")

    assert preview["drift"] is True
    assert preview["note"], "a drift flag with no sentence is half an answer"
    assert preview["total_rows"] == 5, "the rows are current even if the manifest is not"
    assert exported.headers[DRIFT_HEADER] == "true"
    assert exported.headers[CONTENT_HASH_HEADER] != exported.headers[
        RECORDED_HASH_HEADER
    ]


def test_the_rows_are_derived_rather_than_stored(seeded):
    """A stored snapshot would go stale the moment an operator backfilled a reading."""
    build_ok(seeded)

    stored = seeded.get("/api/datasets/").json()["items"][0]

    assert "rows" not in stored
    assert "preview_rows" not in stored
    assert stored["report"]["record_count"] == 4


def test_the_manifest_documents_every_column_it_was_built_from(seeded):
    """A reader must not have to hold the catalogue in their head."""
    features = {
        feature["name"]: feature for feature in build_ok(seeded)["dataset"]["features"]
    }

    assert features["current_salinity_g_l"]["source_collection"] == "pan_observations"
    assert features["forecast_rainfall_mm"]["source_collection"] == (
        "weather_observations"
    )
    assert features["pan_area_m2"]["leakage_class"] == "static"
    assert features["current_salinity_g_l"]["leakage_class"] == "as_of"
    assert all(feature["scientific_basis"] for feature in features.values())


def test_the_manifest_records_the_specification_it_was_built_from(seeded):
    """An experiment reproduced outside the API needs the same specification."""
    dataset = build_ok(seeded, lookback_hours=48.0)["dataset"]

    assert dataset["lookback_hours"] == 48.0
    assert dataset["include_unlabelled"] is True
    assert dataset["max_source_records"] > 0
    assert dataset["builder_schema_version"] >= 1
    assert dataset["target"]["horizon_hours"] == 24.0


# -- listing and status --------------------------------------------------------


def test_listing_reports_the_total_beside_the_page(seeded):
    build_ok(seeded)
    build_ok(seeded, target={"horizon_hours": 48.0})

    body = seeded.get("/api/datasets/", params={"limit": 1}).json()

    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["limit"] == 1
    assert body["offset"] == 0


def test_listing_can_be_narrowed_to_one_target(seeded):
    build_ok(seeded)
    build_ok(seeded, target={"name": "post_rain_depth_cm", "horizon_hours": 24.0})

    body = seeded.get("/api/datasets/", params={"target": "post_rain_depth_cm"}).json()

    assert body["total"] == 1
    assert body["items"][0]["target"]["name"] == "post_rain_depth_cm"


def test_filtering_by_an_unknown_target_is_refused(seeded):
    response = seeded.get("/api/datasets/", params={"target": "not_a_target"})

    assert response.status_code == 422
    assert "post_rain_salinity_g_l" in response.text


def test_the_literal_paths_are_not_captured_as_dataset_ids(seeded):
    """A router ordering mistake here would make the catalogue unreachable."""
    assert seeded.get("/api/datasets/status").status_code == 200
    assert seeded.get("/api/datasets/catalog").status_code == 200


def test_opening_an_unknown_dataset_is_a_404(seeded):
    response = seeded.get("/api/datasets/ds-does-not-exist")

    assert response.status_code == 404
    assert "ds-does-not-exist" in response.text


def test_the_status_endpoint_reports_the_module_and_its_sources(seeded):
    body = seeded.get("/api/datasets/status").json()

    assert body["module"] == "datasets"
    assert body["module_number"] == 7
    assert body["status"] == "implemented"
    assert body["datasets"]["available"] is True
    assert body["datasets"]["total_datasets"] == 0
    assert body["datasets"]["source_records"]["pan_observations"] == 4
    assert "post_rain_salinity_g_l" in body["datasets"]["buildable_targets"]


def test_the_status_endpoint_survives_a_missing_database(dataset_client, monkeypatch):
    """Reporting status is the one thing it exists to do, so it must not raise."""
    from app.api.routers import datasets as datasets_router

    class Unavailable:
        def unavailable_reason(self) -> str:
            return "Database not configured."

    monkeypatch.setattr(datasets_router, "db", Unavailable())
    # Restore the real status dependency so it sees the absent database itself,
    # rather than being handed a service the fixture built over the fake.
    app.dependency_overrides.pop(get_dataset_status_service, None)
    app.dependency_overrides[get_optional_db] = lambda: None

    body = dataset_client.get("/api/datasets/status").json()

    assert body["datasets"]["available"] is False
    assert body["datasets"]["detail"] == "Database not configured."


def test_a_malformed_build_is_a_422_before_the_database_is_asked(dataset_client):
    """The body is validated first, so a typo is a 422 rather than a 503."""
    app.dependency_overrides[get_db] = _raise_unconfigured

    assert dataset_client.post("/api/datasets/", json={}).status_code == 422
    assert (
        dataset_client.post(
            "/api/datasets/", json={"target": SALINITY_TARGET, "nonsense": 1}
        ).status_code
        == 422
    )
    assert (
        dataset_client.post(
            "/api/datasets/", json={"target": {"name": "nope", "horizon_hours": 24.0}}
        ).status_code
        == 422
    )


def _raise_unconfigured() -> Any:
    """Stand in for ``get_db`` when no database is configured."""
    from app.db.mongo import db

    raise db.unavailable_error()
