"""Module 15 - actual-outcome API tests.

These run against in-memory stand-ins for every collection the module touches,
so the whole request/response contract is exercised without a live MongoDB.

The behaviours worth locking down are the ones that could quietly turn a
research record into a flattering one:

* a measurement the operator never took stays null, and the event it belongs to
  is reported as not comparable rather than as a perfect prediction;
* a percentage error is null where the measurement it divides by is zero, and
  never a rendered ``0%``;
* an aggregate mean is taken over the comparable events only, with the size of
  the excluded set reported beside it;
* a prediction cannot be posted by the client, and an error cannot be stored
  without both a prediction and a real measurement behind it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

import pymongo
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.outcomes import (
    get_outcome_service,
    get_outcome_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.db.indexes import (
    ACTUAL_OUTCOMES,
    INDEX_PLAN,
    PAN_OBSERVATIONS,
    RECOMMENDATIONS,
    SIMULATION_RUNS,
    WEATHER_OBSERVATIONS,
)
from app.db.repository import MongoRepository
from app.main import app
from app.models.actual_outcome import ActualOutcome
from app.models.base import BaseDocument
from app.models.enums import HarvestOutcome
from app.models.pan_observation import PanObservation
from app.models.recommendation import Recommendation
from app.models.weather import WeatherObservation
from app.services.outcomes import OutcomeService
from app.services.salt_pans import SaltPanService
from tests.fakes import FakeDatabase

DocumentT = TypeVar("DocumentT", bound=BaseDocument)

VALID_PAN = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 10_000.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}

#: The event instant every test builds around: mid-March, in the past, so the
#: future-skew guard does not reject it.
EVENT_AT = "2026-03-14T18:00:00Z"

#: A fully measured event. 12,500 m2 is the other pan's area on purpose: the
#: predicted figure is checked against the balance, so a change to the area
#: would move it.
VALID_EVENT = {
    "pan_id": "kzn-2026-a01",
    "event_id": "storm-2026-03-14",
    "timestamp": EVENT_AT,
    "forecast_rainfall_mm": 30.0,
    "actual_rainfall_mm": 40.0,
    "pre_event_salinity_g_l": 280.0,
    "pre_event_depth_cm": 20.0,
    "post_event_salinity_g_l": 255.0,
    "post_event_depth_cm": 24.0,
    "operator_action": "Opened the outlet gate for 40 minutes to spread the brine.",
    "harvest_outcome": HarvestOutcome.PARTIAL,
}

#: C1 = C0 * V0 / V1, with V0 = 10_000 * 0.2 = 2_000 m3 and
#: Vr = 10_000 * 0.04 = 400 m3, so V1 = 2_400 m3.
PREDICTED_FROM_40MM = 280.0 * 2_000.0 / 2_400.0
#: The same balance from the 30 mm forecast: Vr = 300 m3, V1 = 2_300 m3.
PREDICTED_FROM_30MM = 280.0 * 2_000.0 / 2_300.0


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def outcome_client(database: FakeDatabase) -> Any:
    """A TestClient whose outcome and pan routes are backed by memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_outcome_service] = lambda: OutcomeService(database)
    app.dependency_overrides[get_outcome_status_service] = (
        lambda: OutcomeService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_pan(outcome_client: TestClient) -> str:
    response = outcome_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def _insert(
    database: FakeDatabase,
    collection: str,
    model: type[DocumentT],
    document: DocumentT,
) -> DocumentT:
    """Store one document of a neighbouring module directly, then return it.

    The fake collections hold plain dictionaries and are not bound to a running
    loop, so a short-lived loop is enough to drive the repository.
    """
    return asyncio.run(
        MongoRepository(database, collection, model).insert(document)  # type: ignore[arg-type]
    )


def record(client: TestClient, **overrides: Any) -> Any:
    """File one event, with fields overridden or removed by passing None."""
    return client.post("/api/outcomes/", json={**VALID_EVENT, **overrides})


def record_event(client: TestClient, label: str, **overrides: Any) -> Any:
    """File a distinct event, named so a fixture can tell them apart.

    An event may only be recorded once per pan, so a test that needs several
    outcomes is describing several events and has to say which is which.
    """
    return record(client, event_id=f"{VALID_EVENT['event_id']}-{label}", **overrides)


# -- recording ------------------------------------------------------------


def test_recording_an_event_returns_201(outcome_client, registered_pan):
    assert record(outcome_client).status_code == 201


def test_the_prediction_is_computed_server_side(outcome_client, registered_pan):
    """The salinity prediction comes from the mass balance, not from the client."""
    body = record(outcome_client).json()

    terms = body["simulation_error"]
    assert terms["predicted"] == pytest.approx(PREDICTED_FROM_40MM)
    assert terms["predicted_from"] == "actual_rainfall_mm"
    assert terms["unit"] == "g/L"
    assert terms["is_comparable"] is True


def test_a_second_prediction_grades_the_forecast_as_well(outcome_client, registered_pan):
    """The same pre-event state, run on the forecast rainfall instead.

    Both salinity blocks share a starting state, so the gap between the two
    predictions is exactly the effect of the forecast's rainfall error and
    nothing else.
    """
    body = record(outcome_client).json()

    forecast_terms = body["forecast_simulation_error"]
    assert forecast_terms["predicted"] == pytest.approx(PREDICTED_FROM_30MM)
    assert forecast_terms["predicted_from"] == "forecast_rainfall_mm"
    # Graded against the same single measurement as the other block.
    assert forecast_terms["actual"] == body["simulation_error"]["actual"]
    assert forecast_terms["unit"] == body["simulation_error"]["unit"]
    # The forecast was lighter than the rain, so it predicted less dilution and
    # a higher salinity.
    assert forecast_terms["predicted"] > body["simulation_error"]["predicted"]
    assert forecast_terms["absolute_error"] == pytest.approx(
        abs(PREDICTED_FROM_30MM - 255.0)
    )


def test_the_forecast_is_graded_against_the_rainfall_that_fell(outcome_client, registered_pan):
    terms = record(outcome_client).json()["rainfall_forecast_error"]

    assert terms["predicted"] == pytest.approx(30.0)
    assert terms["actual"] == pytest.approx(40.0)
    assert terms["unit"] == "mm"
    assert terms["signed_error"] == pytest.approx(-10.0)
    assert terms["absolute_error"] == pytest.approx(10.0)
    assert terms["percentage_error_percent"] == pytest.approx(-25.0)


def test_errors_are_signed_so_the_direction_of_the_mistake_is_visible(
    outcome_client, registered_pan
):
    """A prediction below the measurement is a different mistake from one above."""
    high = record_event(outcome_client, "high", post_event_salinity_g_l=200.0).json()[
        "simulation_error"
    ]
    low = record_event(outcome_client, "low", post_event_salinity_g_l=290.0).json()[
        "simulation_error"
    ]

    # Both measured far below the predicted 233.3, and far above it respectively.
    assert high["actual"] < high["predicted"]
    assert high["signed_error"] > 0
    assert low["actual"] > low["predicted"]
    assert low["signed_error"] < 0
    # Magnitude is the same view of the same event, not a second opinion.
    assert high["absolute_error"] == pytest.approx(high["signed_error"])
    assert low["absolute_error"] == pytest.approx(-low["signed_error"])


def test_every_measured_field_is_stored_exactly_as_given(outcome_client, registered_pan):
    body = record(outcome_client).json()

    assert body["pan_id"] == registered_pan
    assert body["event_id"] == "storm-2026-03-14"
    assert body["timestamp"] == "2026-03-14T18:00:00Z"
    assert body["actual_rainfall_mm"] == pytest.approx(40.0)
    assert body["forecast_rainfall_mm"] == pytest.approx(30.0)
    assert body["pre_event_salinity_g_l"] == pytest.approx(280.0)
    assert body["pre_event_depth_cm"] == pytest.approx(20.0)
    assert body["post_event_salinity_g_l"] == pytest.approx(255.0)
    assert body["post_event_depth_cm"] == pytest.approx(24.0)
    assert body["harvest_outcome"] == "partial"
    assert "40 minutes" in body["operator_action"]


def test_outcome_identifier_is_readable_back(outcome_client, registered_pan):
    created = record(outcome_client).json()

    response = outcome_client.get(f"/api/outcomes/{created['outcome_id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_unknown_outcome_is_404_not_an_empty_record(outcome_client, registered_pan):
    response = outcome_client.get("/api/outcomes/outcome-does-not-exist")
    assert response.status_code == 404


def test_a_pan_that_is_not_registered_is_refused(outcome_client, registered_pan):
    response = outcome_client.post(
        "/api/outcomes/", json={**VALID_EVENT, "pan_id": "kzn-2026-zz9"}
    )
    assert response.status_code == 404
    assert "No registered pan" in response.json()["detail"]


def test_the_same_event_cannot_be_recorded_twice(outcome_client, registered_pan):
    """One rainfall event is one event, however often it is filed.

    Two documents for the same event would both be individually valid and both
    counted, so a re-entered measurement would quietly be counted twice in every
    mean. The second write is refused instead.
    """
    assert record(outcome_client).status_code == 201

    repeat = record(outcome_client, post_event_salinity_g_l=240.0)

    assert repeat.status_code == 409
    detail = repeat.json()["detail"]
    assert "already recorded" in detail
    assert VALID_EVENT["event_id"] in detail
    assert "measured once" in detail


def test_a_refused_repeat_does_not_change_the_stored_measurement(
    outcome_client, registered_pan, database
):
    first = record(outcome_client).json()
    assert record(outcome_client, post_event_salinity_g_l=99.0).status_code == 409

    stored = asyncio.run(
        MongoRepository(
            database, ACTUAL_OUTCOMES, ActualOutcome
        ).find({"pan_id": VALID_EVENT["pan_id"]})
    )
    assert len(stored) == 1
    assert stored[0].outcome_id == first["outcome_id"]
    assert stored[0].post_event_salinity_g_l == VALID_EVENT[
        "post_event_salinity_g_l"
    ]


def test_the_same_event_id_on_another_pan_is_not_a_duplicate(
    outcome_client, registered_pan
):
    """Event ids are scoped to a pan, so a collision across pans is fine."""
    assert record(outcome_client).status_code == 201

    other = outcome_client.post(
        "/api/pans/",
        json={
            **VALID_PAN,
            "pan_id": "kzn-2026-b02",
            "pan_name": "Second pan",
            "location": "Block B",
        },
    )
    assert other.status_code == 201, other.text

    second = record(outcome_client, pan_id="kzn-2026-b02")
    assert second.status_code == 201, second.text


def test_a_repeat_is_refused_even_when_it_differs_only_in_timestamp(
    outcome_client, registered_pan
):
    assert record(outcome_client).status_code == 201

    later = record(
        outcome_client, timestamp="2026-03-14T18:00:00+02:00"
    )

    assert later.status_code == 409


def test_the_pan_index_makes_one_event_unique_per_pan():
    """The database is the backstop for two concurrent writes."""
    spec = next(
        entry
        for entry in INDEX_PLAN
        if entry.collection == ACTUAL_OUTCOMES and entry.unique
    )
    assert spec.keys == (
        ("pan_id", pymongo.ASCENDING),
        ("event_id", pymongo.ASCENDING),
    )


# -- missing measurements stay missing ------------------------------------


def test_an_unmeasured_salinity_is_not_invented(outcome_client, registered_pan):
    """The central rule: nobody managed to measure it, so it stays null."""
    body = record(outcome_client, post_event_salinity_g_l=None).json()

    assert body["post_event_salinity_g_l"] is None
    for block in ("simulation_error", "forecast_simulation_error"):
        terms = body[block]
        assert terms["actual"] is None
        assert terms["signed_error"] is None
        assert terms["absolute_error"] is None
        assert terms["percentage_error_percent"] is None
        assert terms["is_comparable"] is False


def test_an_unmeasured_salinity_is_reported_with_the_figure_still_awaited(
    outcome_client, registered_pan
):
    """A null with no explanation is indistinguishable from a bug."""
    terms = record(outcome_client, post_event_salinity_g_l=None).json()[
        "simulation_error"
    ]

    assert "post_event_salinity_g_l" in terms["missing_fields"]
    reasons = [entry["reason"] for entry in terms["missing_inputs"]]
    assert any("post-event salinity" in reason for reason in reasons)


def test_an_unmeasured_event_reports_itself_as_not_comparable(outcome_client, registered_pan):
    body = record(outcome_client, post_event_salinity_g_l=None).json()
    assert body["is_comparable"] is False


def test_the_prediction_survives_the_measurement_going_missing(
    outcome_client, registered_pan
):
    """The model still made a prediction; only the comparison is unavailable.

    Keeping the prediction is what lets an operator see later that the record
    exists and is merely waiting on a refractometer reading.
    """
    terms = record(outcome_client, post_event_salinity_g_l=None).json()[
        "simulation_error"
    ]
    assert terms["predicted"] == pytest.approx(PREDICTED_FROM_40MM)


def test_an_unmeasured_rainfall_is_reported_against_the_forecast_block(
    outcome_client, registered_pan
):
    """No rain gauge reading means the forecast cannot be graded, not that it was right."""
    terms = record(outcome_client, actual_rainfall_mm=None).json()[
        "rainfall_forecast_error"
    ]

    assert terms["predicted"] == pytest.approx(30.0)
    assert terms["actual"] is None
    assert terms["signed_error"] is None
    assert terms["is_comparable"] is False
    assert "actual_rainfall_mm" in terms["missing_fields"]


def test_zero_rainfall_is_a_measurement_not_a_gap(outcome_client, registered_pan):
    """A gauge reading of 0 is evidence, and it is graded as evidence."""
    body = record(
        outcome_client, actual_rainfall_mm=0.0, forecast_rainfall_mm=10.0
    ).json()

    terms = body["rainfall_forecast_error"]
    assert terms["actual"] == 0.0
    assert terms["is_comparable"] is True
    assert terms["signed_error"] == pytest.approx(10.0)
    # A prediction from no rain is the pre-event concentration unchanged.
    assert body["simulation_error"]["predicted"] == pytest.approx(280.0)


def test_no_pre_event_salinity_means_no_prediction_and_no_guessed_error(
    outcome_client, registered_pan
):
    """Without a starting concentration the balance is not run at all."""
    body = record(outcome_client, pre_event_salinity_g_l=None).json()

    terms = body["simulation_error"]
    assert terms["predicted"] is None
    assert terms["signed_error"] is None
    assert terms["is_comparable"] is False
    assert "pre_event_salinity_g_l" in terms["missing_fields"]


def test_no_depth_means_no_prediction(outcome_client, registered_pan):
    terms = record(outcome_client, pre_event_depth_cm=None).json()["simulation_error"]

    assert terms["predicted"] is None
    assert "pre_event_depth_cm" in terms["missing_fields"]


def test_a_missing_input_stores_no_simulation_run(outcome_client, registered_pan, database):
    """A run recorded for a prediction that was never computed would be a fiction."""
    record_event(outcome_client, "unmeasurable", pre_event_salinity_g_l=None)

    assert database[SIMULATION_RUNS].documents() == []
    body = record_event(outcome_client, "measurable").json()
    assert body["links"]["simulation_run"]["record_id"]


def test_a_computable_event_stores_the_run_behind_its_prediction(
    outcome_client, registered_pan, database
):
    body = record(outcome_client).json()

    run_id = body["links"]["simulation_run"]["record_id"]
    assert run_id
    documents = database[SIMULATION_RUNS].documents()
    assert len(documents) == 1
    # The inputs are recorded so the arithmetic can be replayed later.
    assert documents[0]["run_id"] == run_id
    assert documents[0]["inputs"]["initial_salinity_g_l"] == pytest.approx(280.0)
    assert documents[0]["inputs"]["initial_depth_m"] == pytest.approx(0.2)
    assert documents[0]["inputs"]["rainfall_mm"] == pytest.approx(40.0)
    assert documents[0]["outputs"]["post_rain_salinity_g_l"] == pytest.approx(
        PREDICTED_FROM_40MM
    )


# -- percentage error is undefined at zero -------------------------------


def test_percentage_error_is_null_when_the_measurement_is_zero(
    outcome_client, registered_pan
):
    """A percentage of a measurement that is zero does not exist."""
    body = record(outcome_client, post_event_salinity_g_l=0.0).json()

    terms = body["simulation_error"]
    assert terms["actual"] == 0.0
    assert terms["absolute_error"] == pytest.approx(PREDICTED_FROM_40MM)
    assert terms["percentage_error_percent"] is None
    assert "undefined" in terms["percentage_error_reason"]
    assert terms["is_comparable"] is True


# -- aggregates -----------------------------------------------------------


def test_a_mean_is_taken_over_the_comparable_events_only(outcome_client, registered_pan):
    """The event nobody measured must not be counted as a zero error.

    Averaging it in would pull the mean down by exactly the thing the record does
    not know, and would make a model that was never graded look better than one
    that was.
    """
    record_event(outcome_client, "a", post_event_salinity_g_l=250.0)
    record_event(outcome_client, "b", post_event_salinity_g_l=260.0)
    record_event(outcome_client, "unmeasured", post_event_salinity_g_l=None)

    stats = outcome_client.get("/api/outcomes/comparison").json()["simulation_error"]

    assert stats["compared"] == 2
    assert stats["not_comparable"] == 1
    expected = (
        abs(PREDICTED_FROM_40MM - 250.0) + abs(PREDICTED_FROM_40MM - 260.0)
    ) / 2
    assert stats["mean_absolute_error"] == pytest.approx(expected)
    assert stats["coverage_percent"] == pytest.approx(200 / 3)


def test_the_largest_error_is_reported_beside_the_mean(outcome_client, registered_pan):
    """A mean can hide a single catastrophic event; the worst one cannot."""
    record_event(outcome_client, "far", post_event_salinity_g_l=270.0)
    record_event(outcome_client, "near", post_event_salinity_g_l=200.0)

    stats = outcome_client.get("/api/outcomes/comparison").json()["simulation_error"]
    # 270 is further from the predicted 233.3 than 200 is.
    assert stats["largest_absolute_error"] == pytest.approx(
        abs(PREDICTED_FROM_40MM - 270.0)
    )
    assert stats["largest_absolute_error"] > stats["mean_absolute_error"]


def test_bias_says_which_way_the_model_ran_high_or_low(outcome_client, registered_pan):
    """A signed mean is what separates a biased model from a merely noisy one.

    Both events here were measured below the prediction, so the two errors share a
    sign and the bias is negative: the model over-diluted on this pan, which no
    amount of averaging could be read off a mean absolute error.
    """
    record_event(outcome_client, "a", post_event_salinity_g_l=250.0)
    record_event(outcome_client, "b", post_event_salinity_g_l=260.0)

    terms = outcome_client.get("/api/outcomes/comparison").json()["simulation_error"]
    assert terms["bias"] == pytest.approx(((PREDICTED_FROM_40MM - 250.0) + (PREDICTED_FROM_40MM - 260.0)) / 2)
    assert terms["bias"] < 0
    assert terms["bias_reason"] is None


def test_no_comparable_events_means_a_null_mean_with_a_reason(
    outcome_client, registered_pan
):
    """Zero events graded is not zero error, and is not reported as 0.0."""
    record_event(outcome_client, "a", post_event_salinity_g_l=None)
    record_event(outcome_client, "b", post_event_salinity_g_l=None)

    stats = outcome_client.get("/api/outcomes/comparison").json()["simulation_error"]

    assert stats["compared"] == 0
    assert stats["not_comparable"] == 2
    assert stats["mean_absolute_error"] is None
    assert stats["coverage_percent"] == 0.0
    assert "No event in scope" in stats["mean_absolute_error_reason"]


def test_a_comparison_with_nothing_recorded_is_empty_not_zero(
    outcome_client, registered_pan
):
    """With no events at all the coverage is undefined rather than 0%."""
    summary = outcome_client.get("/api/outcomes/comparison").json()

    assert summary["total_outcomes"] == 0
    assert summary["simulation_error"]["compared"] == 0
    assert summary["simulation_error"]["coverage_percent"] is None
    assert summary["simulation_error"]["mean_absolute_error"] is None


def test_the_comparison_endpoint_is_not_read_as_an_outcome_identifier(
    outcome_client, registered_pan
):
    """Route order: the literal path must not be captured by /{outcome_id}."""
    record(outcome_client)
    response = outcome_client.get("/api/outcomes/comparison")

    assert response.status_code == 200
    assert "simulation_error" in response.json()
    assert response.json()["total_outcomes"] == 1


def test_the_comparison_can_be_narrowed_to_one_pan(outcome_client, registered_pan):
    other = outcome_client.post(
        "/api/pans/",
        json={**VALID_PAN, "pan_id": "kzn-2026-b02", "pan_name": "Southern Pan"},
    )
    assert other.status_code == 201, other.text
    other_pan = other.json()["pan_id"]
    record(outcome_client)
    record(outcome_client, pan_id=other_pan)

    both = outcome_client.get("/api/outcomes/comparison").json()
    assert both["total_outcomes"] == 2
    assert both["pan_id"] is None

    scoped = outcome_client.get(
        "/api/outcomes/comparison", params={"pan_id": other_pan}
    ).json()
    assert scoped["total_outcomes"] == 1
    assert scoped["pan_id"] == other_pan


def test_the_comparison_counts_the_measurement_backlog(outcome_client, registered_pan):
    record_event(outcome_client, "measured", post_event_salinity_g_l=255.0)
    record_event(outcome_client, "waiting-a", post_event_salinity_g_l=None)
    record_event(outcome_client, "waiting-b", post_event_salinity_g_l=None)

    summary = outcome_client.get("/api/outcomes/comparison").json()

    assert summary["total_outcomes"] == 3
    assert summary["events_missing_actual_salinity"] == 2


# -- listing --------------------------------------------------------------


def test_events_are_listed_newest_first(outcome_client, registered_pan):
    record(outcome_client, event_id="storm-a", timestamp="2026-03-10T18:00:00Z")
    record(outcome_client, event_id="storm-b", timestamp="2026-03-14T18:00:00Z")
    record(outcome_client, event_id="storm-c", timestamp="2026-03-12T18:00:00Z")

    items = outcome_client.get("/api/outcomes/").json()["items"]
    assert [item["event_id"] for item in items] == ["storm-b", "storm-c", "storm-a"]


def test_listing_reports_the_window_it_covers(outcome_client, registered_pan):
    record_event(outcome_client, "earlier", timestamp="2026-03-10T18:00:00Z")
    record_event(outcome_client, "later", timestamp="2026-03-14T18:00:00Z")

    body = outcome_client.get("/api/outcomes/").json()
    assert body["total"] == 2
    assert body["earliest_timestamp"] == "2026-03-10T18:00:00Z"
    assert body["latest_timestamp"] == "2026-03-14T18:00:00Z"


def test_listing_can_be_narrowed_to_one_pan_and_one_event_name(
    outcome_client, registered_pan
):
    record(outcome_client, event_id="storm-a")
    record(outcome_client, event_id="storm-b")

    by_name = outcome_client.get(
        "/api/outcomes/", params={"event_id": "storm-a"}
    ).json()
    assert by_name["total"] == 1
    assert by_name["items"][0]["event_id"] == "storm-a"

    by_pan = outcome_client.get(
        "/api/outcomes/", params={"pan_id": registered_pan}
    ).json()
    assert by_pan["total"] == 2


def test_listing_by_an_unregistered_pan_is_refused(outcome_client, registered_pan):
    """A filter that silently matched nothing would read as "no outcomes yet"."""
    response = outcome_client.get(
        "/api/outcomes/", params={"pan_id": "kzn-2026-zz9"}
    )
    assert response.status_code == 404


def test_a_reversed_time_window_is_refused(outcome_client, registered_pan):
    response = outcome_client.get(
        "/api/outcomes/",
        params={"start": "2026-03-14T00:00:00Z", "end": "2026-03-01T00:00:00Z"},
    )
    assert response.status_code == 422


# -- links ----------------------------------------------------------------


@pytest.fixture()
def linked_records(database: FakeDatabase, registered_pan: str) -> dict[str, str]:
    """A pre-event observation, a covering forecast and advice in force.

    Seeded straight into the collections rather than through the HTTP routes of
    Modules 3, 4 and 10, so this file tests Module 15's own selection logic and
    not theirs.
    """
    observation = _insert(
        database,
        PAN_OBSERVATIONS,
        PanObservation,
        PanObservation(
            pan_id=registered_pan,
            timestamp=datetime(2026, 3, 14, 6, 0, tzinfo=timezone.utc),
            salinity_g_l=281.0,
            water_depth_cm=19.0,
        ),
    )
    forecast = _insert(
        database,
        WEATHER_OBSERVATIONS,
        WeatherObservation,
        WeatherObservation(
            pan_id=registered_pan,
            timestamp=datetime(2026, 3, 14, 8, 0, tzinfo=timezone.utc),
            forecast_for=datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc),
            rainfall_mm=30.0,
            is_forecast=True,
        ),
    )
    recommendation = _insert(
        database,
        RECOMMENDATIONS,
        Recommendation,
        Recommendation(
            pan_id=registered_pan,
            title="Hold the gate closed",
            body="No action until the front has passed.",
            rationale="The forecast is for a light event only.",
            issued_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
            expires_at=datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc),
        ),
    )
    return {
        "observation_id": observation.observation_id,
        "weather_forecast_id": forecast.weather_id,
        "recommendation_id": recommendation.recommendation_id,
    }


def test_the_records_an_event_is_graded_against_are_resolved_automatically(
    outcome_client, registered_pan, linked_records
):
    """With no references given, the service selects them from the timestamp."""
    links = record(outcome_client).json()["links"]

    assert links["pan_state"]["is_resolved"] is True
    assert links["pan_state"]["salinity_g_l"] == pytest.approx(281.0)
    assert links["weather_forecast"]["is_resolved"] is True
    assert links["weather_forecast"]["rainfall_mm"] == pytest.approx(30.0)
    assert links["recommendation"]["is_resolved"] is True


def test_a_link_records_the_figures_without_replacing_the_operators_own(
    outcome_client, registered_pan, linked_records
):
    """The linked observation describes the pan; it does not become the entry.

    The pre-event figures stay what the operator typed, so the link and the entry
    can be compared and any disagreement noticed.
    """
    body = record(outcome_client).json()

    assert body["links"]["pan_state"]["salinity_g_l"] == pytest.approx(281.0)
    assert body["pre_event_salinity_g_l"] == pytest.approx(280.0)


def test_an_absent_record_is_reported_rather_than_left_out(
    outcome_client, registered_pan
):
    """All four links are present even when nothing was found for them."""
    links = record(outcome_client).json()["links"]

    assert links["pan_state"]["is_resolved"] is False
    assert "No pan observation" in links["pan_state"]["summary"]
    assert links["weather_forecast"]["is_resolved"] is False
    assert links["recommendation"]["is_resolved"] is False


def test_explicit_references_are_honoured(outcome_client, registered_pan, linked_records):
    body = record(
        outcome_client,
        references={
            "observation_id": linked_records["observation_id"],
            "weather_forecast_id": linked_records["weather_forecast_id"],
            "recommendation_id": linked_records["recommendation_id"],
        },
    ).json()

    assert body["links"]["pan_state"]["record_id"] == linked_records["observation_id"]
    assert (
        body["links"]["weather_forecast"]["record_id"]
        == linked_records["weather_forecast_id"]
    )
    assert (
        body["links"]["recommendation"]["record_id"]
        == linked_records["recommendation_id"]
    )


def test_a_reference_to_another_pans_record_is_refused(
    outcome_client, registered_pan, database
):
    """Comparing an event against another pan's evidence would misattribute the error."""
    other = outcome_client.post(
        "/api/pans/",
        json={**VALID_PAN, "pan_id": "kzn-2026-b02", "pan_name": "Southern Pan"},
    )
    assert other.status_code == 201, other.text
    foreign = _insert(
        database,
        PAN_OBSERVATIONS,
        PanObservation,
        PanObservation(
            pan_id=other.json()["pan_id"],
            timestamp=datetime(2026, 3, 14, 6, 0, tzinfo=timezone.utc),
            salinity_g_l=100.0,
        ),
    )

    response = record(
        outcome_client, references={"observation_id": foreign.observation_id}
    )
    assert response.status_code == 422
    assert "belongs to pan" in response.json()["detail"]


def test_a_reference_to_a_record_that_does_not_exist_is_refused(
    outcome_client, registered_pan
):
    response = record(outcome_client, references={"observation_id": "obs-nope"})
    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


# -- the client may not supply the answer ---------------------------------


def test_a_client_cannot_post_its_own_prediction(outcome_client, registered_pan):
    """Accepting this would let a caller assert the model's accuracy.

    The whole module exists so that accuracy claims are checkable, and that stops
    being true the moment the claimant supplies the comparison.
    """
    response = outcome_client.post(
        "/api/outcomes/",
        json={**VALID_EVENT, "simulation_error": {"signed_error": 0.0}},
    )
    assert response.status_code == 422


def test_a_client_cannot_post_its_own_mean_absolute_error(outcome_client, registered_pan):
    response = record(outcome_client, mean_absolute_error=0.0)
    assert response.status_code == 422


def test_an_event_that_says_nothing_about_what_happened_is_refused(
    outcome_client, registered_pan
):
    response = outcome_client.post(
        "/api/outcomes/",
        json={
            "pan_id": registered_pan,
            "event_id": "storm-2026-03-14",
            "timestamp": EVENT_AT,
        },
    )
    assert response.status_code == 422
    assert "at least one" in response.text.lower()


def test_an_operator_action_alone_is_enough_to_file_an_event(outcome_client, registered_pan):
    """Some events are worth recording for what was done, not for a measurement."""
    body = record(
        outcome_client,
        actual_rainfall_mm=None,
        pre_event_salinity_g_l=None,
        pre_event_depth_cm=None,
        post_event_salinity_g_l=None,
        post_event_depth_cm=None,
        operator_action="Repaired the collapsed southern bund.",
    ).json()
    assert body["outcome_id"]
    assert body["is_comparable"] is False


def test_a_harvest_of_none_is_recorded_as_an_outcome_not_a_gap(
    outcome_client, registered_pan
):
    """Recording that nothing was harvested is not the same as not knowing."""
    body = record_event(
        outcome_client, "harvested-nothing", harvest_outcome=HarvestOutcome.NONE
    ).json()
    assert body["harvest_outcome"] == "none"

    unrecorded = record_event(
        outcome_client, "not-asked", harvest_outcome=None
    ).json()
    assert unrecorded["harvest_outcome"] is None


def test_an_event_cannot_be_dated_in_the_future(outcome_client, registered_pan):
    """An outcome describes something that has already happened."""
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = record(outcome_client, timestamp=future)
    assert response.status_code == 422


def test_an_event_defaults_to_the_moment_it_is_recorded(outcome_client, registered_pan):
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    body = record(outcome_client, timestamp=None).json()

    assert body["timestamp"] is not None
    parsed = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
    assert parsed >= before


# -- status ---------------------------------------------------------------


def test_status_reports_live_totals(outcome_client, registered_pan):
    record_event(outcome_client, "measured", post_event_salinity_g_l=255.0)
    record_event(outcome_client, "waiting", post_event_salinity_g_l=None)

    body = outcome_client.get("/api/outcomes/status").json()

    assert body["module"] == "actual-outcomes"
    assert body["module_number"] == 15
    assert body["status"] == "implemented"
    assert body["outcomes"]["available"] is True
    assert body["outcomes"]["total_outcomes"] == 2
    assert body["outcomes"]["comparable_outcomes"] == 1
