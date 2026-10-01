"""Module 20 - field-validation API and service tests.

These run against the same in-memory ``FakeDatabase`` the other route suites
use, overridden onto the pan and field-validation route dependencies, so the
whole request/response contract is exercised without a live MongoDB.

The behaviours worth locking down are the ones that could quietly turn a field
report into a claim it does not support:

* the replay is rainfall-only: 250 g/L brine at 20 cm under 90 mm of rain must
  replay to ~172.41 g/L and 29 cm, whatever the reported post values are, and
  the absolute differences must be the difference of those numbers with the
  reported ones -- nothing is fitted to make the reported figures look smaller;
* an event with an intervention, and an intervention with an unknown transferred
  volume, are flagged rather than silently assimilated; the prediction still
  runs, but the flags (and the caveats) say the difference is not explained;
* every event is stored as ``reported`` and a stored event is never auto-verified:
  ``independently_verified`` derives from ``verification_state``, which no write
  on this API sets to ``verified``;
* export carries the same figures as the comparison view, in CSV and in a
  server-generated PDF.
"""

from __future__ import annotations

import copy
import csv
import io
import re
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.field_validation import get_field_validation_service
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.db.collections import (
    OPERATOR_DECISIONS,
    PAN_OBSERVATIONS,
    RECOMMENDATIONS,
    SIMULATION_RUNS,
)
from app.main import app
from app.services.field_validation import FieldValidationService
from app.services.observations import ObservationService
from app.services.salt_pans import SaltPanService
from tests.fakes import FakeDatabase

VALID_PAN = {
    "pan_id": "North-Pan",
    "pan_name": "North Pan",
    "location": "North field",
    "area_m2": 10_000.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}

EVENT_AT = "2026-03-14T18:00:00Z"
RAIN_AT = "2026-03-14T14:00:00Z"

#: The reported North Pan field-validation reference numbers the task fixes:
#: initial salinity 250 g/L, initial depth 20 cm, actual rainfall 90 mm, and a
#: brine-transfer-out intervention whose volume was not recorded.
NORTH_PAN_REPORT = {
    "pan_id": "north-pan",
    "name": "North Pan 14 Mar rain event",
    "event_timestamp": EVENT_AT,
    "reported_by": "field-operator",
    "actual_rainfall_mm": 90.0,
    "rainfall_timestamp": RAIN_AT,
    "rainfall_source": "manual rain gauge",
    "forecast_rainfall_mm": 40.0,
    "pre_event": {
        "salinity_g_l": 250.0,
        "water_depth_cm": 20.0,
        "timestamp": "2026-03-13T08:00:00Z",
        "source": "manual",
    },
    "post_event": {
        "salinity_g_l": 20.0,
        "water_depth_cm": 50.0,
        "timestamp": "2026-03-15T08:00:00Z",
        "source": "manual",
    },
    "interventions": [
        {
            "kind": "brine_transfer_out",
            "direction": "out",
            "performed_at": "2026-03-14T19:00:00Z",
            "description": "Transferred brine out after the event.",
        },
    ],
    "notes": "Reported by the field operator after the rain.",
}

#: The rainfall-only replay of the North Pan numbers: C1 = C0*D0/(D0+R) with
#: R = actual_rainfall_mm / 10, and the depth adding the rainfall's centimetres.
EXPECTED_PREDICTED_SALINITY = 250.0 * 20.0 / 29.0  # ~172.41
EXPECTED_PREDICTED_DEPTH_CM = 29.0  # 20 cm + 90 mm
EXPECTED_SALINITY_ABS_DIFF = abs(EXPECTED_PREDICTED_SALINITY - 20.0)  # ~152.41
EXPECTED_DEPTH_ABS_DIFF = 21.0  # |29 - 50|


def _event(**overrides: Any) -> dict[str, Any]:
    return {**NORTH_PAN_REPORT, **overrides}


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def client(database: FakeDatabase) -> TestClient:
    """A TestClient backed by memory for the pan and field-validation routes.

    The observation routes are overridden too, because reconciling a pre-event
    snapshot means linking it to a stored observation, and the tests need to file
    those observations through the same API an operator would use.
    """
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = lambda: ObservationService(
        database
    )
    app.dependency_overrides[get_observation_status_service] = (
        lambda: ObservationService(database)
    )
    app.dependency_overrides[get_field_validation_service] = (
        lambda: FieldValidationService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_pan(client: TestClient) -> str:
    response = client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def report(client: TestClient, **overrides: Any) -> Any:
    return client.post("/api/field-validation/events", json=_event(**overrides))


def comparisons(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/field-validation/comparisons", params=params)
    assert response.status_code == 200, response.text
    return response.json()


class TestStatus:
    def test_empty_status_is_not_ready_but_can_be_written_to(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/field-validation/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["event_count"] == 0
        assert payload["ready"] is False
        assert "No reported rainfall events" in payload["message"]

    def test_status_counts_filed_events(self, client: TestClient) -> None:
        client.post("/api/pans/", json=VALID_PAN)
        assert report(client).status_code == 201
        payload = client.get("/api/field-validation/status").json()
        assert payload["event_count"] == 1
        assert payload["ready"] is True


class TestFiling:
    def test_unknown_pan_is_404(self, client: TestClient) -> None:
        response = report(client)
        assert response.status_code == 404
        assert "no registered pan" in response.json()["detail"].lower()

    def test_pre_snapshot_must_carry_salinity_and_depth(
        self, client: TestClient, registered_pan: str
    ) -> None:
        missing = _event(pre_event={"water_depth_cm": 20.0})
        response = report(client, pre_event=missing["pre_event"])
        assert response.status_code == 422
        assert "pre-event snapshot" in response.json()["detail"].lower()

    def test_reports_are_stored_as_reported_never_verified(
        self, client: TestClient, registered_pan: str
    ) -> None:
        response = report(client)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["verification_state"] == "reported"
        assert body["independently_verified"] is False


class TestNorthPanReplay:
    """The exact reference numbers the task must reproduce, rainfall-only."""

    def test_predicted_salinity_and_depth_match_the_mass_balance(
        self, client: TestClient, registered_pan: str
    ) -> None:
        response = report(client)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["actual_rainfall_mm"] == 90.0
        assert body["predicted_salinity_g_l"] == pytest.approx(EXPECTED_PREDICTED_SALINITY, rel=1e-6)
        assert body["predicted_depth_cm"] == pytest.approx(EXPECTED_PREDICTED_DEPTH_CM)

    def test_reported_differences_are_the_arithmetic_of_the_two_numbers(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        assert body["reported_salinity_g_l"] == 20.0
        assert body["reported_depth_cm"] == 50.0
        assert body["salinity_abs_diff_g_l"] == pytest.approx(EXPECTED_SALINITY_ABS_DIFF, rel=1e-6)
        assert body["depth_abs_diff_cm"] == pytest.approx(EXPECTED_DEPTH_ABS_DIFF)

    def test_intervention_flags_survive_into_the_comparison(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        assert body["intervention_affected"] is True
        assert body["unknown_intervention_volumes"] is True
        joined = " ".join(body["caveats"]).lower()
        assert "intervention" in joined
        assert "transferred volume" in joined

    def test_prediction_uses_actual_rainfall_not_forecast(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        assert body["forecast_rainfall_mm"] == 40.0
        # The predicted salinity corresponds to 90 mm of rain, not the 40 mm
        # forecast; a replay using the forecast would predict differently.
        assert body["predicted_salinity_g_l"] == pytest.approx(EXPECTED_PREDICTED_SALINITY, rel=1e-6)


class TestReadingBack:
    def test_list_and_filter_by_pan(self, client: TestClient, registered_pan: str) -> None:
        assert report(client, name="First").status_code == 201
        assert report(client, name="Second").status_code == 201
        listing = client.get(
            "/api/field-validation/events", params={"pan_id": "NORTH-PAN"}
        )
        assert listing.status_code == 200
        assert len(listing.json()) == 2

    def test_get_comparison_by_event_id(self, client: TestClient, registered_pan: str) -> None:
        event_id = report(client).json()["event_id"]
        response = client.get(f"/api/field-validation/events/{event_id}")
        assert response.status_code == 200
        assert response.json()["event_id"] == event_id

    def test_unknown_event_is_404(self, client: TestClient, registered_pan: str) -> None:
        assert client.get("/api/field-validation/events/ev-nope").status_code == 404

    def test_append_intervention_updates_the_event(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client, interventions=[]).json()["event_id"]
        response = client.post(
            f"/api/field-validation/events/{event_id}/interventions",
            json={
                "kind": "brine_transfer_in",
                "direction": "in",
                "performed_at": "2026-03-15T09:00:00Z",
                "volume_m3": 12.5,
                "description": "Added brine back.",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["intervention_affected"] is True
        assert body["unknown_intervention_volumes"] is False
        listing = client.get(
            "/api/field-validation/events", params={"pan_id": "north-pan"}
        ).json()
        assert len(listing[0]["interventions"]) == 1


class TestSummary:
    def test_summary_counts_and_averages(self, client: TestClient, registered_pan: str) -> None:
        report(client)
        summary = client.get(
            "/api/field-validation/summary", params={"pan_id": "north-pan"}
        )
        assert summary.status_code == 200
        body = summary.json()
        assert body["total_events"] == 1
        assert body["compared_events"] == 1
        assert body["events_with_salinity"] == 1
        assert body["events_with_depth"] == 1
        assert body["intervention_affected_count"] == 1
        assert body["unknown_volume_count"] == 1
        assert body["verified_count"] == 0
        assert body["mean_abs_salinity_diff_g_l"] == pytest.approx(EXPECTED_SALINITY_ABS_DIFF, rel=1e-6)
        assert body["mean_abs_depth_diff_cm"] == pytest.approx(EXPECTED_DEPTH_ABS_DIFF)

    def test_summary_with_no_compared_events_is_none_not_zero(
        self, client: TestClient, registered_pan: str
    ) -> None:
        summary = client.get("/api/field-validation/summary").json()
        assert summary["total_events"] == 0
        assert summary["mean_abs_salinity_diff_g_l"] is None
        assert summary["mean_abs_depth_diff_cm"] is None


class TestExport:
    def test_csv_carries_header_and_the_replay_figures(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        response = client.get("/api/field-validation/export", params={"format": "csv"})
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        content = response.text
        assert "event_id" in content
        assert "predicted_salinity_g_l" in content
        assert content.startswith(
            "event_id,pan_id,name,event_timestamp,event_milestone,site_timezone_label"
        )
        assert "north-pan" in content
        assert "brine_out" not in content  # kinds are embedded in the event, not a column

    def test_pdf_is_server_generated(self, client: TestClient, registered_pan: str) -> None:
        report(client)
        response = client.get("/api/field-validation/export", params={"format": "pdf"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")

    def test_unknown_format_is_422(self, client: TestClient, registered_pan: str) -> None:
        response = client.get("/api/field-validation/export", params={"format": "xlsx"})
        assert response.status_code == 422


class TestHonestyGuarantees:
    def test_no_write_can_mark_an_event_verified(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        stored = client.get(
            "/api/field-validation/events", params={"pan_id": "north-pan"}
        ).json()[0]
        assert stored["verification_state"] == "reported"
        # The only two writes this module exposes are create and append an
        # intervention; neither accepts a verification flag, so a stored event
        # can only ever be read back as reported here.
        response = client.post(
            f"/api/field-validation/events/{event_id}/interventions",
            json={
                "kind": "other",
                "direction": "none",
                "performed_at": "2026-03-15T10:00:00Z",
                "description": "Checked nothing.",
            },
        )
        assert response.status_code == 201
        assert response.json()["verification_state"] == "reported"
        assert response.json()["independently_verified"] is False

    def test_forecast_difference_is_a_caveat_not_a_hidden_assumption(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        joined = " ".join(body["caveats"]).lower()
        assert "actual rainfall only" in joined


def _stored_event(client: TestClient, pan_id: str = "north-pan") -> dict[str, Any]:
    listing = client.get("/api/field-validation/events", params={"pan_id": pan_id})
    assert listing.status_code == 200, listing.text
    assert len(listing.json()) == 1
    return listing.json()[0]


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, f"stored timestamp lost its timezone: {value!r}"
    return parsed


class TestEventChronology:
    """The event must keep the reported ordering of its own instants.

    The module stores what it is given; it never reorders, backdates or
    re-times a report. These tests lock the two things the North Pan audit
    depends on: a coherent event's pre → rain → post ordering survives
    reading back unchanged, and the event is created server-side at filing
    time, which is what an auditor uses to tell a fresh report from a backfilled
    one.
    """

    def test_pre_rain_post_order_survives_round_trip(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        event = _stored_event(client)
        pre = _parse(event["pre_event"]["timestamp"])
        rain = _parse(event["rainfall_timestamp"])
        post = _parse(event["post_event"]["timestamp"])
        assert pre <= rain <= post
        assert event["event_timestamp"] <= event["created_at"]

    def test_an_event_created_in_september_keeps_the_march_it_claimed(
        self, client: TestClient, registered_pan: str
    ) -> None:
        """The audit's exact case: a March-dated event is read back exactly as
        filed, with its March instants and its `created_at` untouched -- so a
        cross-date replay stays identifiable rather than being silently folded
        into the past."""
        report(client)
        event = _stored_event(client)
        assert _parse(event["event_timestamp"]).year == 2026
        assert _parse(event["event_timestamp"]).month == 3
        assert _parse(event["created_at"]) > _parse(event["event_timestamp"])
        # Both stamps are server filing time -- an auditor reads them together
        # to tell a fresh report from a backfilled one.
        assert _parse(event["created_at"]) > _parse(event["event_timestamp"])
        delta = abs((_parse(event["reported_at"]) - _parse(event["created_at"])).total_seconds())
        assert delta < 1.0

    def test_disordered_post_before_pre_is_stored_as_reported_not_rewritten(
        self, client: TestClient, registered_pan: str
    ) -> None:
        loud_event = _event(
            pre_event={**NORTH_PAN_REPORT["pre_event"], "timestamp": "2026-03-15T08:00:00Z"},
            post_event={**NORTH_PAN_REPORT["post_event"], "timestamp": "2026-03-13T08:00:00Z"},
        )
        response = report(client, pre_event=loud_event["pre_event"],
                          post_event=loud_event["post_event"])
        assert response.status_code == 201, response.text
        event = _stored_event(client)
        pre = _parse(event["pre_event"]["timestamp"])
        post = _parse(event["post_event"]["timestamp"])
        # Stored exactly as reported -- the module does not silently reorder time.
        assert post < pre
        assert event["verification_state"] == "reported"

    def test_client_cannot_backdate_the_creation_stamp(
        self, client: TestClient, registered_pan: str
    ) -> None:
        response = report(
            client,
            event_timestamp="2026-03-14T18:00:00Z",
            pre_event={**NORTH_PAN_REPORT["pre_event"], "timestamp": "2026-03-13T08:00:00Z"},
            post_event={**NORTH_PAN_REPORT["post_event"], "timestamp": "2026-03-15T08:00:00Z"},
        )
        assert response.status_code == 201, response.text
        event = _stored_event(client)
        # `reported_at` / `created_at` are server-time and always at or after the
        # event's instants; a March-dated event filed later is identifiable as
        # exactly that.
        assert _parse(event["created_at"]) >= _parse(event["event_timestamp"])
        assert _parse(event["reported_at"]) >= _parse(event["event_timestamp"])


class TestTimezoneHandling:
    """Timestamps are stored as UTC and read back tz-aware.

    ``UtcDateTime`` normalizes every instant to UTC; an explicit +05:30 offset
    (the pan's own zone, IST) must land on the correct UTC instant and every
    stored instant must come back carrying a timezone rather than a bare,
    zone-less time.
    """

    def test_ist_offset_is_stored_as_the_correct_utc_instant(
        self, client: TestClient, registered_pan: str
    ) -> None:
        # Salem local time 23:30 on the 14th is 18:00 UTC, and the North Pan
        # reference event is exactly that: the same instant, written in IST.
        response = report(client, event_timestamp="2026-03-14T23:30:00+05:30")
        assert response.status_code == 201, response.text
        event = _stored_event(client)
        instant = _parse(event["event_timestamp"])
        assert instant == datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc)

    def test_every_stored_instant_reads_back_tz_aware_utc(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        event = _stored_event(client)
        for stamp in (
            event["event_timestamp"],
            event["rainfall_timestamp"],
            event["pre_event"]["timestamp"],
            event["post_event"]["timestamp"],
            event["interventions"][0]["performed_at"],
            event["created_at"],
        ):
            parsed = _parse(stamp)
            assert parsed.tzinfo == timezone.utc

    def test_naive_inputs_are_normalized_to_utc_not_silently_kept_naive(
        self, client: TestClient, registered_pan: str
    ) -> None:
        # The documented rule in app/models/base.py: a zone-less instant is
        # assumed to be UTC (replace, not offset). A naive "18:00" must be read
        # back as 18:00 *UTC*, carrying a zone -- never as a bare local time that
        # another reader could interpret as IST.
        response = report(client, event_timestamp="2026-03-14T18:00:00")
        assert response.status_code == 201, response.text
        event = _stored_event(client)
        instant = _parse(event["event_timestamp"])
        assert instant == datetime(2026, 3, 14, 18, 0, tzinfo=timezone.utc)
        assert instant.astimezone(timezone(timedelta(hours=5, minutes=30))).hour == 23


class TestProvenance:
    """A stored event's links (or their absence) must survive the round trip.

    The North Pan audit turned on whether the pre-event snapshot traces to a
    stored observation. These tests lock the mechanics an auditor relies on: a
    snapshot's `observation_id` is preserved when present, and is never invented
    when absent -- an unlinked snapshot stays unlinked.
    """

    def test_snapshot_links_are_preserved_when_present(
        self, client: TestClient, registered_pan: str
    ) -> None:
        response = report(
            client,
            weather_observation_id="wx-march-14",
            pre_event={
                **NORTH_PAN_REPORT["pre_event"],
                "observation_id": "obs-march-13",
            },
            post_event={
                **NORTH_PAN_REPORT["post_event"],
                "observation_id": "obs-march-15",
            },
        )
        assert response.status_code == 201, response.text
        event = _stored_event(client)
        assert event["pre_event"]["observation_id"] == "obs-march-13"
        assert event["post_event"]["observation_id"] == "obs-march-15"
        assert event["weather_observation_id"] == "wx-march-14"

    def test_unlinked_snapshot_is_not_given_an_invented_link(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        event = _stored_event(client)
        assert event["pre_event"]["observation_id"] is None
        assert event["post_event"]["observation_id"] is None
        assert event["weather_observation_id"] is None

def _observation(
    client: TestClient,
    *,
    pan_id: str = "north-pan",
    timestamp: str,
    salinity: float | None = None,
    depth: float | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """File one stored observation through the API an operator would use."""
    payload: dict[str, Any] = {
        "pan_id": pan_id,
        "timestamp": timestamp,
        "data_source": "manual",
    }
    if salinity is not None:
        payload["salinity_g_l"] = salinity
    if depth is not None:
        payload["water_depth_cm"] = depth
    if note is not None:
        payload["note"] = note
    response = client.post("/api/observations/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _link(
    client: TestClient,
    event_id: str,
    observation_id: str,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "observation_id": observation_id,
        "measured_at_local": "2026-03-13T13:30",
        "measured_timezone": "IST",
        "observer": "site operator",
        "evidence_source": "North Pan field log, page 4",
        "reason": "Original field record found for the pre-event reading.",
        "corrected_by": "audit",
    }
    payload.update(overrides)
    return client.post(
        f"/api/field-validation/events/{event_id}/pre-event-link", json=payload
    )


def _correct(client: TestClient, event_id: str, **overrides: Any) -> Any:
    payload: dict[str, Any] = {"field": "event_milestone", "reason": "test"}
    payload.update(overrides)
    return client.post(
        f"/api/field-validation/events/{event_id}/corrections", json=payload
    )


class TestEventMilestone:
    """A stored instant is meaningless until somebody says what it marks.

    The North Pan event's 23:30 IST instant has no established meaning, so it is
    filed as ``unknown`` and stays there until an operator states it. Nothing in
    the module infers the milestone from the nearby rainfall timestamp.
    """

    def test_an_event_filed_without_a_milestone_is_unknown(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        assert body["event_milestone"] == "unknown"
        assert _stored_event(client)["event_milestone"] == "unknown"

    def test_the_milestone_is_not_inferred_from_the_rainfall_timestamp(
        self, client: TestClient, registered_pan: str
    ) -> None:
        # The event instant is four hours after the rain; the module still does
        # not decide which of the two the event "happened" at.
        body = report(client).json()
        assert body["rainfall_timestamp_utc"] == "2026-03-14 14:00:00 UTC"
        assert body["event_milestone"] == "unknown"

    def test_unknown_milestone_is_reported_as_a_caveat(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        joined = " ".join(body["caveats"]).lower()
        assert "has not been assigned a meaning" in joined

    def test_a_stated_milestone_is_stored_and_returned(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client, event_milestone="rainfall_end").json()
        assert body["event_milestone"] == "rainfall_end"
        assert _stored_event(client)["event_milestone"] == "rainfall_end"

    def test_an_unknown_milestone_is_a_caveat_an_established_one_is_not(
        self, client: TestClient, registered_pan: str
    ) -> None:
        established = report(client, event_milestone="rainfall_end").json()
        assert "has not been assigned a meaning" not in " ".join(established["caveats"])


class TestUtcAndIstArePublishedSeparately:
    """Both zones, both labelled, and the stored instant left alone.

    The audit's central arithmetic was that 18:00 UTC is 23:30 IST. Rendering one
    converted number invites a reader to convert it again; rendering both, each
    named, makes that impossible.
    """

    def test_event_instant_is_published_in_utc_and_ist(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        assert body["event_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert body["event_timestamp_ist"] == "2026-03-14 23:30:00 IST"
        assert "IST" in body["site_timezone_label"]

    def test_rainfall_and_snapshot_instants_are_published_in_both_zones(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        assert body["rainfall_timestamp_utc"] == "2026-03-14 14:00:00 UTC"
        assert body["rainfall_timestamp_ist"] == "2026-03-14 19:30:00 IST"
        assert body["pre_event_timestamp_utc"] == "2026-03-13 08:00:00 UTC"
        assert body["pre_event_timestamp_ist"] == "2026-03-13 13:30:00 IST"
        assert body["post_event_timestamp_utc"] == "2026-03-15 08:00:00 UTC"
        assert body["post_event_timestamp_ist"] == "2026-03-15 13:30:00 IST"

    def test_publishing_a_local_time_does_not_move_the_stored_instant(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        stored = _stored_event(client)
        assert _parse(stored["event_timestamp"]) == datetime(
            2026, 3, 14, 18, 0, tzinfo=timezone.utc
        )

    def test_an_event_declaring_utc_shows_utc_on_both_sides(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client, event_timezone="UTC").json()
        assert body["event_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert body["event_timestamp_ist"] == "2026-03-14 18:00:00 UTC"
        assert "UTC+00:00" in body["site_timezone_label"]


class TestCorrectionHistoryIsAppendOnly:
    """Corrections accumulate; nothing is rewritten and nothing is verified."""

    def test_a_correction_records_the_previous_and_the_new_value(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _correct(
            client,
            event_id,
            field="event_milestone",
            reason="Operator confirmed the log marks when the rain stopped.",
            event_milestone="rainfall_end",
            corrected_by="site operator",
            evidence_source="North Pan field log, page 4",
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["event_milestone"] == "rainfall_end"
        assert body["correction_count"] == 1
        entry = body["corrections"][0]
        assert entry["previous_value"] == "unknown"
        assert entry["new_value"] == "rainfall_end"
        assert entry["reason"].startswith("Operator confirmed")
        assert entry["corrected_by"] == "site operator"
        assert entry["corrected_at"]

    def test_a_second_correction_leaves_the_first_entry_intact(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        first = _correct(
            client, event_id, event_milestone="rainfall_start", reason="First."
        ).json()["corrections"][0]
        body = _correct(
            client, event_id, event_milestone="rainfall_end", reason="Second."
        ).json()
        assert body["correction_count"] == 2
        history = body["corrections"]
        assert history[0]["correction_id"] == first["correction_id"]
        assert history[0]["new_value"] == first["new_value"] == "rainfall_start"
        assert history[0]["reason"] == "First."
        assert history[1]["previous_value"] == "rainfall_start"
        # Appended in order, so the history reads as a sequence of steps.
        assert _parse(history[0]["corrected_at"]) <= _parse(history[1]["corrected_at"])

    def test_no_correction_can_set_the_event_verified(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        body = _correct(
            client,
            event_id,
            event_milestone="rainfall_end",
            reason="Confirmed against the field log.",
            evidence_source="North Pan field log, page 4",
        ).json()
        assert body["verification_state"] == "reported"
        assert body["independently_verified"] is False
        assert body["intervention_affected"] is True
        assert body["unknown_intervention_volumes"] is True

    def test_a_restated_timestamp_demands_its_evidence_source(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _correct(
            client,
            event_id,
            field="post_event_timestamp",
            timestamp="2026-03-15T09:00:00Z",
            reason="Read the wrong row in the log.",
        )
        assert response.status_code == 422, response.text
        assert "evidence_source" in response.text

    def test_a_restated_timestamp_stays_uncorroborated_and_keeps_the_figures(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        body = _correct(
            client,
            event_id,
            field="post_event_timestamp",
            timestamp="2026-03-15T09:00:00Z",
            recorded_timezone="IST",
            reason="The log records the reading two hours later than first typed.",
            evidence_source="North Pan field log, page 5",
        ).json()
        assert body["post_event_timestamp_utc"] == "2026-03-15 09:00:00 UTC"
        assert body["post_event_timestamp_ist"] == "2026-03-15 14:30:00 IST"
        # An operator asserting a time cannot corroborate that same time.
        assert body["post_event_timestamp_verified"] is False
        entry = body["corrections"][0]
        assert entry["previous_value"] == "2026-03-15 08:00:00 UTC"
        assert entry["new_value"] == "2026-03-15 09:00:00 UTC"
        # The reported readings themselves are untouched.
        assert body["reported_salinity_g_l"] == 20.0
        assert body["reported_depth_cm"] == 50.0

    def test_an_unknown_field_is_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _correct(
            client,
            event_id,
            field="reported_salinity_g_l",
            reason="Trying to overwrite a measured figure.",
        )
        assert response.status_code == 422

    def test_the_history_offers_no_way_to_delete_or_edit_it(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(client, event_id, event_milestone="rainfall_end", reason="First.")
        for method in ("delete", "put", "patch"):
            call = getattr(client, method)
            response = call(f"/api/field-validation/events/{event_id}/corrections")
            assert response.status_code in (404, 405), (
                f"{method.upper()} on the audit history returned "
                f"{response.status_code}"
            )
        after = client.get(f"/api/field-validation/events/{event_id}").json()
        assert after["correction_count"] == 1


class TestPreEventLinking:
    """Linking is the only route to a traceable starting brine, and it is strict."""

    def test_a_genuine_march_observation_can_be_linked(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _observation(
            client,
            timestamp="2026-03-13T08:00:00Z",
            salinity=250.0,
            depth=20.0,
            note="Morning reading before the rain.",
        )
        response = _link(client, event_id, observation["observation_id"])
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["pre_event_observation_id"] == observation["observation_id"]
        assert body["pre_event_observer"] == "site operator"
        assert body["pre_event_timestamp_verified"] is True
        assert body["pre_event_timestamp_utc"] == "2026-03-13 08:00:00 UTC"
        assert body["correction_count"] == 1
        entry = body["corrections"][0]
        assert entry["field"] == "pre_event_link"
        assert "observation_id=none" in entry["previous_value"]
        assert entry["evidence_record_id"] == observation["observation_id"]

    def test_linking_does_not_disturb_the_replay(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        before = client.get(f"/api/field-validation/events/{event_id}").json()
        observation = _observation(
            client, timestamp="2026-03-13T08:00:00Z", salinity=250.0, depth=20.0
        )
        after = _link(client, event_id, observation["observation_id"]).json()
        assert after["predicted_salinity_g_l"] == pytest.approx(
            before["predicted_salinity_g_l"]
        )
        assert after["predicted_depth_cm"] == pytest.approx(before["predicted_depth_cm"])
        assert after["pre_event_salinity_g_l"] == 250.0
        assert after["pre_event_depth_cm"] == 20.0

    def test_the_september_observation_cannot_be_linked_to_the_march_event(
        self, client: TestClient, registered_pan: str
    ) -> None:
        """The audit's exact case, refused for the reason that matters."""
        event_id = report(client).json()["event_id"]
        september = _observation(
            client,
            timestamp="2026-09-30T09:10:27Z",
            salinity=250.0,
            depth=20.0,
            note="Pre-rainfall reading.",
        )
        response = _link(
            client,
            event_id,
            september["observation_id"],
            measured_at_local="2026-09-30T14:40:27",
        )
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert "2026-09-30 09:10:27 UTC" in detail
        assert "2026-03-14 14:00:00 UTC" in detail
        assert "cannot postdate" in detail
        assert "Nothing was linked" in detail
        body = client.get(f"/api/field-validation/events/{event_id}").json()
        assert body["pre_event_observation_id"] is None
        assert body["correction_count"] == 0

    def test_a_refused_link_leaves_the_september_record_byte_identical(
        self, client: TestClient, database: FakeDatabase, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _observation(
            client, timestamp="2026-09-30T09:10:27Z", salinity=250.0, depth=20.0
        )
        before = _docs(database, PAN_OBSERVATIONS)
        _link(
            client,
            event_id,
            before[0]["observation_id"],
            measured_at_local="2026-09-30T14:40:27",
        )
        assert _docs(database, PAN_OBSERVATIONS) == before

    def test_figures_that_disagree_are_refused_rather_than_averaged(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        other = _observation(
            client, timestamp="2026-03-13T08:00:00Z", salinity=241.0, depth=20.0
        )
        response = _link(client, event_id, other["observation_id"])
        assert response.status_code == 409, response.text
        assert "salinity differs by 9 g/L" in response.json()["detail"]
        body = client.get(f"/api/field-validation/events/{event_id}").json()
        assert body["pre_event_salinity_g_l"] == 250.0
        assert body["pre_event_observation_id"] is None

    def test_an_observation_of_another_pan_is_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        other_pan = client.post(
            "/api/pans/",
            json={**VALID_PAN, "pan_id": "South-Pan", "pan_name": "South Pan"},
        )
        assert other_pan.status_code == 201, other_pan.text
        event_id = report(client).json()["event_id"]
        observation = _observation(
            client,
            pan_id="south-pan",
            timestamp="2026-03-13T08:00:00Z",
            salinity=250.0,
            depth=20.0,
        )
        response = _link(client, event_id, observation["observation_id"])
        assert response.status_code == 422
        assert "Nothing was linked" in response.json()["detail"]

    def test_an_unknown_observation_is_refused_without_inventing_one(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _link(client, event_id, "obs-that-does-not-exist")
        assert response.status_code == 404
        assert "does not invent one" in response.json()["detail"]

    def test_a_declared_time_that_disagrees_with_the_record_is_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _observation(
            client, timestamp="2026-03-13T08:00:00Z", salinity=250.0, depth=20.0
        )
        response = _link(
            client,
            event_id,
            observation["observation_id"],
            measured_at_local="2026-03-13T18:30",
        )
        assert response.status_code == 422
        assert "different instants" in response.json()["detail"]

    def test_an_unreadable_zone_is_refused_rather_than_guessed(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _observation(
            client, timestamp="2026-03-13T08:00:00Z", salinity=250.0, depth=20.0
        )
        response = _link(
            client, event_id, observation["observation_id"], measured_timezone="salem"
        )
        assert response.status_code == 422
        assert "not a time zone" in response.json()["detail"]

    def test_a_declared_time_carrying_its_own_conflicting_offset_is_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        """The double-conversion guard: two clocks in one field is a refusal."""
        event_id = report(client).json()["event_id"]
        observation = _observation(
            client, timestamp="2026-03-13T08:00:00Z", salinity=250.0, depth=20.0
        )
        response = _link(
            client,
            event_id,
            observation["observation_id"],
            measured_at_local="2026-03-13T13:30:00+04:00",
        )
        assert response.status_code == 422
        assert "carries the offset" in response.json()["detail"]

    def test_linking_the_same_record_twice_adds_no_second_entry(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _observation(
            client, timestamp="2026-03-13T08:00:00Z", salinity=250.0, depth=20.0
        )
        _link(client, event_id, observation["observation_id"])
        again = _link(client, event_id, observation["observation_id"]).json()
        assert again["correction_count"] == 1

    def test_an_unlinked_snapshot_reports_itself_as_untraceable(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        assert body["pre_event_timestamp_verified"] is False
        joined = " ".join(body["caveats"]).lower()
        assert "not linked to a stored observation" in joined
        assert "has not been corroborated" in joined

    def test_the_summary_counts_the_unreconciled_events(
        self, client: TestClient, database: FakeDatabase, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _observation(
            client, timestamp="2026-03-13T08:00:00Z", salinity=250.0, depth=20.0
        )
        _link(
            client,
            event_id,
            _docs(database, PAN_OBSERVATIONS)[0]["observation_id"],
        )
        summary = client.get("/api/field-validation/summary").json()
        assert summary["events_with_unlinked_pre_event"] == 0
        assert summary["uncorroborated_post_event_timestamps"] == 1
        assert summary["events_with_unknown_milestone"] == 1


def _pdf_text(payload: bytes) -> str:
    """Read back the text a PDF renders, without pulling in a PDF library.

    The report is generated here, so the only thing worth checking is that the
    strings the module says it writes really reach the page: inflate each content
    stream and unescape the parenthesis the format escapes inside text runs.
    """
    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", payload, re.DOTALL):
        raw = match.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        chunks.append(raw.decode("latin-1"))
    text = "\n".join(chunks)
    return text.replace("\\(", "(").replace("\\)", ")")


def _docs(database: FakeDatabase, collection: str) -> list[dict[str, Any]]:
    """Every stored document in one collection, deep-copied for comparison."""
    return copy.deepcopy(database[collection].documents())


class TestSeptemberEvidenceIsPreserved:
    """Reconciling the March event must not disturb the September records.

    The live audit found a 30 September observation, two 25 mm simulations, two
    recommendations and an accepted decision on the same pan. Those are the only
    records of what the pan actually did that month; a reconciliation pass on a
    March event has no business touching them, and these tests hold the whole
    surrounding record still while the March event is corrected and a
    cross-date link is refused.
    """

    SEPTEMBER_SIMULATION: ClassVar[dict[str, Any]] = {
        "run_id": "run-september-1",
        "pan_id": "north-pan",
        "initial_salinity_g_l": 250.0,
        "initial_depth_cm": 20.0,
        "rainfall_mm": 25.0,
        "final_salinity_g_l": 222.22222222222223,
        "final_depth_cm": 22.5,
        "created_at": datetime(2026, 9, 30, 10, 37, 2, tzinfo=timezone.utc),
    }
    SEPTEMBER_RECOMMENDATION: ClassVar[dict[str, Any]] = {
        "recommendation_id": "rec-september-1",
        "run_id": "run-september-1",
        "pan_id": "north-pan",
        "status": "open",
        "created_at": datetime(2026, 9, 30, 10, 38, 0, tzinfo=timezone.utc),
    }
    SEPTEMBER_DECISION: ClassVar[dict[str, Any]] = {
        "decision_id": "decision-september-1",
        "recommendation_id": "rec-september-1",
        "disposition": "accepted",
        "created_at": datetime(2026, 9, 30, 11, 0, 42, tzinfo=timezone.utc),
    }

    def _seed_september(self, database: FakeDatabase) -> None:
        import anyio

        async def seed() -> None:
            for collection, document in (
                (SIMULATION_RUNS, self.SEPTEMBER_SIMULATION),
                (RECOMMENDATIONS, self.SEPTEMBER_RECOMMENDATION),
                (OPERATOR_DECISIONS, self.SEPTEMBER_DECISION),
            ):
                await database[collection].insert_one(dict(document))

        anyio.run(seed)

    def test_the_surrounding_september_records_survive_a_reconciliation(
        self, client: TestClient, database: FakeDatabase, registered_pan: str
    ) -> None:
        self._seed_september(database)
        event_id = report(client).json()["event_id"]
        before = {
            collection: _docs(database, collection)
            for collection in (SIMULATION_RUNS, RECOMMENDATIONS, OPERATOR_DECISIONS)
        }

        # A milestone correction, then an attempted cross-date link.
        _correct(
            client,
            event_id,
            field="event_milestone",
            event_milestone="rainfall_end",
            reason="Confirmed against the field log.",
            evidence_source="North Pan field log, page 4",
        )
        september_observation = _observation(
            client, timestamp="2026-09-30T09:10:27Z", salinity=250.0, depth=20.0
        )
        _link(
            client,
            event_id,
            september_observation["observation_id"],
            measured_at_local="2026-09-30T14:40:27",
        )

        for collection, documents in before.items():
            assert _docs(database, collection) == documents, (
                f"{collection} was modified while reconciling a March event"
            )

    def test_the_september_simulation_figures_are_never_replayed_against_the_event(
        self, client: TestClient, database: FakeDatabase, registered_pan: str
    ) -> None:
        """The March replay must use the March event's own numbers."""
        self._seed_september(database)
        body = report(client).json()
        assert body["actual_rainfall_mm"] == 90.0
        assert body["forecast_rainfall_mm"] == 40.0
        assert body["predicted_salinity_g_l"] == pytest.approx(
            EXPECTED_PREDICTED_SALINITY
        )
        # The 25 mm September run would give ~222.2 g/L; the event's 90 mm must
        # not produce it.
        assert body["predicted_salinity_g_l"] != pytest.approx(222.2222, abs=1e-3)

    def test_reconciling_the_event_leaves_its_reported_values_untouched(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(
            client,
            event_id,
            field="pre_event_observer",
            observer="site operator",
            reason="Observer named in the field log.",
        )
        stored = _stored_event(client)
        assert stored["pre_event"]["salinity_g_l"] == 250.0
        assert stored["pre_event"]["water_depth_cm"] == 20.0
        assert stored["post_event"]["salinity_g_l"] == 20.0
        assert stored["post_event"]["water_depth_cm"] == 50.0
        assert stored["actual_rainfall_mm"] == 90.0
        assert stored["interventions"][0]["volume_m3"] is None
        assert stored["verification_state"] == "reported"


class TestExportCarriesTheReconciliation:
    """CSV and PDF must show what the API shows, in both zones."""

    def _csv_row(self, client: TestClient) -> dict[str, str]:
        response = client.get("/api/field-validation/export", params={"format": "csv"})
        assert response.status_code == 200, response.text
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 1
        return rows[0]

    def test_the_csv_carries_both_zones_for_every_instant(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        row = self._csv_row(client)
        for column in (
            "event_timestamp_utc",
            "event_timestamp_ist",
            "rainfall_timestamp_utc",
            "rainfall_timestamp_ist",
            "pre_event_timestamp_utc",
            "pre_event_timestamp_ist",
            "post_event_timestamp_utc",
            "post_event_timestamp_ist",
        ):
            assert column in row, f"{column} missing from the CSV export"
        assert row["event_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert row["event_timestamp_ist"] == "2026-03-14 23:30:00 IST"

    def test_the_csv_carries_the_milestone_and_the_verification_flags(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        row = self._csv_row(client)
        assert row["event_milestone"] == "unknown"
        assert row["pre_event_observation_id"] == ""
        assert row["pre_event_timestamp_verified"] == "False"
        assert row["post_event_timestamp_verified"] == "False"
        assert row["independently_verified"] == "False"
        assert row["verification_state"] == "reported"
        assert row["unknown_intervention_volumes"] == "True"

    def test_the_csv_numbers_agree_with_the_comparison(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = report(client).json()
        row = self._csv_row(client)
        assert float(row["predicted_salinity_g_l"]) == pytest.approx(
            body["predicted_salinity_g_l"]
        )
        assert float(row["predicted_depth_cm"]) == pytest.approx(
            body["predicted_depth_cm"]
        )
        assert float(row["salinity_abs_diff_g_l"]) == pytest.approx(
            body["salinity_abs_diff_g_l"]
        )
        assert float(row["actual_rainfall_mm"]) == 90.0

    def test_the_csv_reflects_an_appended_correction(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(
            client,
            event_id,
            event_milestone="rainfall_end",
            reason="Confirmed against the field log.",
            evidence_source="North Pan field log, page 4",
        )
        row = self._csv_row(client)
        assert row["event_milestone"] == "rainfall_end"
        assert row["correction_count"] == "1"

    def test_the_pdf_carries_the_event_the_audit_trail_and_both_zones(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(
            client,
            event_id,
            event_milestone="rainfall_end",
            reason="Confirmed against the field log.",
            evidence_source="North Pan field log, page 4",
        )
        response = client.get("/api/field-validation/export", params={"format": "pdf"})
        assert response.status_code == 200
        text = _pdf_text(response.content)
        assert event_id in text
        assert "2026-03-14 18:00:00 UTC" in text
        assert "2026-03-14 23:30:00 IST" in text
        assert "audit history (1 entry)" in text
        assert "North Pan field log, page 4" in text

    def test_a_single_audit_entry_is_not_written_as_a_plural(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(client, event_id, event_milestone="rainfall_end", reason="Field log.")
        _correct(client, event_id, event_milestone="unknown", reason="Superseded.")

        text = _pdf_text(
            client.get(
                "/api/field-validation/export", params={"format": "pdf"}
            ).content
        )
        assert "audit history (2 entries)" in text
        assert "audit history (1 entries)" not in text


#: The confirmed North Pan notebook entries the operator supplied. Kept as
#: constants so a test states plainly which figures it is asserting about, and
#: so a change to either side is a visible edit rather than a quiet drift.
NOTEBOOK_PRE_EVENT_AT = "2026-03-13T08:00:00Z"  # 13 March 2026, 13:30 IST
NOTEBOOK_RAINFALL_START_AT = "2026-03-14T18:00:00Z"  # 14 March 2026, 23:30 IST
NOTEBOOK_POST_EVENT_AT = "2026-03-15T08:00:00Z"  # 15 March 2026, 13:30 IST
NOTEBOOK_SOURCE = "North Pan field notebook (manual field measurement)"
#: The stored rainfall instant is four hours before the confirmed rain start.
STORED_RAINFALL_AT = "2026-03-14T14:00:00Z"  # 19:30 IST
RAINFALL_CONFLICT_HOURS = 4


def _march_observation(client: TestClient, **overrides: Any) -> dict[str, Any]:
    """File the genuine March pre-event reading, as the notebook dates it.

    Filed through the ordinary observations API with its own original instant:
    this is a historical record being entered truthfully, not an existing record
    being moved backwards.
    """
    payload: dict[str, Any] = {
        "pan_id": "north-pan",
        "timestamp": NOTEBOOK_PRE_EVENT_AT,
        "salinity_g_l": 250.0,
        "water_depth_cm": 20.0,
        "data_source": "manual",
        "observer": "site operator",
        "evidence_source": NOTEBOOK_SOURCE,
    }
    payload.update(overrides)
    response = client.post("/api/observations/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _conflict(client: TestClient, event_id: str, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "reported_timestamp": NOTEBOOK_RAINFALL_START_AT,
        "reported_source": NOTEBOOK_SOURCE,
        "reason": "The notebook dates the rain's start four hours after the "
        "timestamp this event was filed with.",
        "recorded_by": "audit",
    }
    payload.update(overrides)
    return client.post(
        f"/api/field-validation/events/{event_id}/rainfall-timestamp-conflict",
        json=payload,
    )


class TestTheNotebookMarchReadingIsARealRecord:
    """The pre-event figure becomes traceable only via a genuine record.

    The notebook entry is filed as its own observation at its own original
    instant. The September reading is a different record of the same pan months
    later and can never stand in for it.
    """

    def test_the_march_reading_is_stored_at_its_own_original_instant(
        self, client: TestClient, registered_pan: str
    ) -> None:
        observation = _march_observation(client)
        assert observation["timestamp"] == NOTEBOOK_PRE_EVENT_AT
        assert observation["salinity_g_l"] == 250.0
        assert observation["water_depth_cm"] == 20.0
        assert observation["data_source"] == "manual"
        assert observation["evidence_source"] == NOTEBOOK_SOURCE
        assert observation["observer"] == "site operator"

    def test_filing_a_historical_reading_does_not_move_the_pan_backwards(
        self, client: TestClient, registered_pan: str
    ) -> None:
        _observation(
            client, timestamp="2026-03-20T06:00:00Z", salinity=240.0, depth=18.0
        )
        _march_observation(client)
        pan = client.get(f"/api/pans/{registered_pan}").json()
        assert pan["latest_observation"]["timestamp"].startswith("2026-03-20")
        assert pan["latest_observation"]["salinity_g_l"] == 240.0

    def test_the_march_reading_is_a_distinct_record_from_the_september_one(
        self, client: TestClient, registered_pan: str
    ) -> None:
        september = _observation(
            client, timestamp="2026-09-30T09:10:27Z", salinity=250.0, depth=20.0
        )
        march = _march_observation(client)
        assert march["observation_id"] != september["observation_id"]
        assert march["timestamp"] != september["timestamp"]
        listing = client.get("/api/observations/", params={"pan_id": "north-pan"})
        assert listing.json()["total"] == 2

    def test_a_future_timestamped_reading_is_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        response = client.post(
            "/api/observations/",
            json={
                "pan_id": "north-pan",
                "timestamp": "2027-01-01T00:00:00Z",
                "salinity_g_l": 250.0,
            },
        )
        assert response.status_code == 422


class TestLinkingTheMarchReading:
    def test_the_link_corroborates_the_instant_and_records_the_notebook(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)

        response = _link(
            client,
            event_id,
            observation["observation_id"],
            measured_at_local="2026-03-13T13:30",
            measured_timezone="IST",
            observer="site operator",
            evidence_source=NOTEBOOK_SOURCE,
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["pre_event_observation_id"] == observation["observation_id"]
        assert body["pre_event_timestamp_verified"] is True
        assert body["pre_event_timestamp_utc"] == "2026-03-13 08:00:00 UTC"
        assert body["pre_event_timestamp_ist"] == "2026-03-13 13:30:00 IST"
        assert body["pre_event_observer"] == "site operator"

        event = _stored_event(client)
        assert event["pre_event"]["observation_id"] == observation["observation_id"]
        assert event["pre_event"]["evidence_source"] == NOTEBOOK_SOURCE

    def test_linking_does_not_verify_the_event(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)
        _link(client, event_id, observation["observation_id"])
        body = client.get(
            f"/api/field-validation/events/{event_id}"
        ).json()
        assert body["verification_state"] == "reported"
        assert body["independently_verified"] is False

    def test_the_replay_is_unchanged_by_the_link(
        self, client: TestClient, registered_pan: str
    ) -> None:
        before = report(client).json()
        observation = _march_observation(client)
        after = _link(client, before["event_id"], observation["observation_id"]).json()
        assert after["predicted_salinity_g_l"] == pytest.approx(before["predicted_salinity_g_l"])
        assert after["salinity_abs_diff_g_l"] == pytest.approx(before["salinity_abs_diff_g_l"])

    def test_the_notebook_time_must_match_the_stored_instant(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)
        response = _link(
            client,
            event_id,
            observation["observation_id"],
            measured_at_local="2026-03-13T18:00",
        )
        assert response.status_code == 422
        assert "different instants" in response.json()["detail"]
        assert _stored_event(client)["pre_event"]["observation_id"] is None

    def test_the_september_reading_is_still_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        september = _observation(
            client, timestamp="2026-09-30T09:10:27Z", salinity=250.0, depth=20.0
        )
        response = _link(
            client,
            event_id,
            september["observation_id"],
            measured_at_local="2026-09-30T14:40",
        )
        assert response.status_code == 409
        assert "after this event's rainfall" in response.json()["detail"]
        assert _stored_event(client)["pre_event"]["observation_id"] is None


class TestTheMilestoneIsNowEstablished:
    def test_the_notebook_establishes_the_rainfall_start(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _correct(
            client,
            event_id,
            event_milestone="rainfall_start",
            reason="The notebook records the rain starting at 23:30 IST.",
            evidence_source=NOTEBOOK_SOURCE,
        )
        assert response.status_code == 201, response.text
        assert response.json()["event_milestone"] == "rainfall_start"
        assert _stored_event(client)["event_milestone"] == "rainfall_start"

    def test_the_previous_milestone_stays_readable(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(client, event_id, event_milestone="rainfall_start", reason="Notebook.")
        history = _stored_event(client)["corrections"]
        assert history[-1]["previous_value"] == "unknown"
        assert history[-1]["new_value"] == "rainfall_start"


class TestTheRainfallTimestampConflict:
    """Two accounts of when the rain fell, kept side by side.

    The notebook dates the rain's start at 23:30 IST while the event carries
    19:30 IST. The stored timestamp is what the replay was run against, so it is
    not rewritten; the disagreement is recorded instead and blocks the event.
    """

    def test_recording_the_conflict_does_not_move_the_stored_timestamp(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _conflict(client, event_id)
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["rainfall_timestamp_utc"] == "2026-03-14 14:00:00 UTC"
        assert body["rainfall_timestamp_ist"] == "2026-03-14 19:30:00 IST"
        assert _stored_event(client)["rainfall_timestamp"].startswith(
            "2026-03-14T14:00:00"
        )

    def test_both_accounts_are_preserved_in_both_zones(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        conflict = _conflict(client, event_id).json()["rainfall_timestamp_conflicts"][0]

        assert conflict["stored_timestamp_utc"] == "2026-03-14 14:00:00 UTC"
        assert conflict["stored_timestamp_ist"] == "2026-03-14 19:30:00 IST"
        assert conflict["reported_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert conflict["reported_timestamp_ist"] == "2026-03-14 23:30:00 IST"
        assert conflict["reported_source"] == NOTEBOOK_SOURCE
        assert conflict["status"] == "unresolved"

    def test_the_size_of_the_disagreement_is_reported(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        conflict = _conflict(client, event_id).json()["rainfall_timestamp_conflicts"][0]
        assert conflict["offset_seconds"] == RAINFALL_CONFLICT_HOURS * 3600
        assert conflict["offset_human"] == "4 h later"

    def test_the_conflict_lands_in_the_append_only_history(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _conflict(client, event_id)
        history = _stored_event(client)["corrections"]
        entry = history[-1]
        assert entry["field"] == "rainfall_timestamp_conflict"
        assert entry["previous_value"] == "2026-03-14 14:00:00 UTC"
        assert entry["new_value"] == "2026-03-14 18:00:00 UTC"

    def test_the_conflict_blocks_the_event_from_being_reconciled(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)
        _link(client, event_id, observation["observation_id"])
        _correct(client, event_id, event_milestone="rainfall_start", reason="Notebook.")
        assert client.get(
            f"/api/field-validation/events/{event_id}"
        ).json()["reconciliation_state"] == "complete"

        blocked = _conflict(client, event_id).json()
        assert blocked["reconciliation_state"] == "blocked_by_evidence"
        assert blocked["unresolved_conflict_count"] == 1
        assert any(
            "cannot be reported as reconciled" in caveat for caveat in blocked["caveats"]
        )

    def test_the_event_is_never_marked_verified_by_any_of_this(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)
        _link(client, event_id, observation["observation_id"])
        _correct(client, event_id, event_milestone="rainfall_start", reason="Notebook.")
        _conflict(client, event_id)
        body = client.get(f"/api/field-validation/events/{event_id}").json()
        assert body["independently_verified"] is False
        assert body["verification_state"] == "reported"

    def test_a_milestone_of_rainfall_start_exposes_the_stored_rainfall_impossible(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(client, event_id, event_milestone="rainfall_start", reason="Notebook.")
        caveats = " ".join(client.get(
            f"/api/field-validation/events/{event_id}"
        ).json()["caveats"])
        assert "before it began" in caveats or "One of the two is wrong" in caveats

    def test_recording_the_same_conflict_twice_is_a_no_op(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _conflict(client, event_id)
        again = _conflict(client, event_id)
        assert again.status_code == 201
        event = _stored_event(client)
        assert len(event["rainfall_timestamp_conflicts"]) == 1
        assert len(
            [
                entry
                for entry in event["corrections"]
                if entry["field"] == "rainfall_timestamp_conflict"
            ]
        ) == 1

    def test_an_agreeing_reported_time_is_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _conflict(client, event_id, reported_timestamp=STORED_RAINFALL_AT)
        assert response.status_code == 422
        assert "no disagreement" in response.json()["detail"]

    def test_the_rainfall_timestamp_cannot_be_restated_as_a_correction(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = client.post(
            f"/api/field-validation/events/{event_id}/corrections",
            json={
                "field": "rainfall_timestamp",
                "reason": "moving it to match the notebook",
                "timestamp": NOTEBOOK_RAINFALL_START_AT,
                "evidence_source": NOTEBOOK_SOURCE,
            },
        )
        assert response.status_code == 422
        assert _stored_event(client)["rainfall_timestamp"].startswith(
            "2026-03-14T14:00:00"
        )

    def test_the_summary_counts_the_blocked_events(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _conflict(client, event_id)
        summary = client.get("/api/field-validation/summary").json()
        assert summary["events_blocked_on_evidence"] == 1
        assert summary["fully_reconciled_events"] == 0
        assert any(
            "unresolved rainfall-timestamp conflict" in caveat
            for caveat in summary["caveats"]
        )

    def test_the_conflict_reaches_the_exports(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _conflict(client, event_id)

        csv_body = client.get(
            "/api/field-validation/export", params={"format": "csv"}
        ).text
        rows = list(csv.DictReader(io.StringIO(csv_body)))
        assert rows[0]["rainfall_timestamp_utc"] == "2026-03-14 14:00:00 UTC"
        assert rows[0]["unresolved_rainfall_conflicts"] == "1"
        assert rows[0]["reconciliation_state"] == "blocked_by_evidence"

        text = _pdf_text(
            client.get(
                "/api/field-validation/export", params={"format": "pdf"}
            ).content
        )
        assert "rainfall timestamp conflict (unresolved)" in text
        assert "2026-03-14 14:00:00 UTC" in text
        assert "2026-03-14 23:30:00 IST" in text
        assert "4 h later" in text
        assert NOTEBOOK_SOURCE in text


class TestThePostEventInstantStaysUncorroborated:
    def test_confirming_the_post_event_time_does_not_corroborate_it(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _correct(
            client,
            event_id,
            field="post_event_timestamp",
            timestamp=NOTEBOOK_POST_EVENT_AT,
            recorded_timezone="IST",
            evidence_source=NOTEBOOK_SOURCE,
            reason="The notebook dates the post-event sampling at 13:30 IST.",
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["post_event_timestamp_utc"] == "2026-03-15 08:00:00 UTC"
        assert body["post_event_timestamp_ist"] == "2026-03-15 13:30:00 IST"
        assert body["post_event_timestamp_verified"] is False

    def test_the_post_event_provenance_is_recorded_without_verification(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(
            client,
            event_id,
            field="post_event_evidence",
            evidence_source=NOTEBOOK_SOURCE,
            reason="The notebook is the source of the post-event reading.",
        )
        event = _stored_event(client)
        assert event["post_event"]["evidence_source"] == NOTEBOOK_SOURCE
        assert event["post_event"]["timestamp_verified"] is False
        assert event["verification_state"] == "reported"


class TestReconciliationStateRequiresEverything:
    def test_a_bare_event_is_incomplete(
        self, client: TestClient, registered_pan: str
    ) -> None:
        assert report(client).json()["reconciliation_state"] == "incomplete"

    def test_a_milestone_alone_is_not_enough(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _correct(client, event_id, event_milestone="rainfall_start", reason="Notebook.")
        body = client.get(f"/api/field-validation/events/{event_id}").json()
        assert body["reconciliation_state"] == "incomplete"

    def test_a_link_alone_is_not_enough(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)
        body = _link(client, event_id, observation["observation_id"]).json()
        assert body["reconciliation_state"] == "incomplete"

    def test_the_summary_counts_fully_reconciled_events(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)
        _link(client, event_id, observation["observation_id"])
        _correct(client, event_id, event_milestone="rainfall_start", reason="Notebook.")
        summary = client.get("/api/field-validation/summary").json()
        assert summary["fully_reconciled_events"] == 1
        assert summary["events_blocked_on_evidence"] == 0


class TestTheFullReconciliationLeavesSeptemberAlone:
    """The whole March reconciliation, end to end, on one client.

    September carries the pan's simulation runs, recommendations and accepted
    decision. None of it is written by any step here, so it must come out
    identical.
    """

    def test_reconciling_march_touches_no_september_record(
        self, client: TestClient, registered_pan: str, database: FakeDatabase
    ) -> None:
        september = _observation(
            client, timestamp="2026-09-30T09:10:27Z", salinity=250.0, depth=20.0
        )
        september_before = _docs(database, PAN_OBSERVATIONS)
        simulations_before = _docs(database, SIMULATION_RUNS)
        recommendations_before = _docs(database, RECOMMENDATIONS)
        decisions_before = _docs(database, OPERATOR_DECISIONS)

        event_id = report(client).json()["event_id"]
        march = _march_observation(client)
        _link(client, event_id, march["observation_id"])
        _correct(
            client, event_id, event_milestone="rainfall_start", reason="Notebook.",
            evidence_source=NOTEBOOK_SOURCE,
        )
        _correct(
            client, event_id, field="post_event_timestamp",
            timestamp=NOTEBOOK_POST_EVENT_AT, recorded_timezone="IST",
            evidence_source=NOTEBOOK_SOURCE, reason="Notebook.",
        )
        _conflict(client, event_id)

        # Every protected collection is byte-identical.
        assert _docs(database, SIMULATION_RUNS) == simulations_before
        assert _docs(database, RECOMMENDATIONS) == recommendations_before
        assert _docs(database, OPERATOR_DECISIONS) == decisions_before

        # Exactly one observation was added, and September's is untouched: the
        # stored September row is still present, unchanged, in the same position.
        observations_after = _docs(database, PAN_OBSERVATIONS)
        assert len(observations_after) == len(september_before) + 1
        assert observations_after[: len(september_before)] == september_before
        added = observations_after[len(september_before) :]
        assert added[0]["observation_id"] != september["observation_id"]
        assert str(added[0]["timestamp"]).startswith("2026-03-13")

    def test_the_final_event_reports_its_blocked_state_and_flags(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        march = _march_observation(client)
        _link(client, event_id, march["observation_id"])
        _correct(
            client, event_id, event_milestone="rainfall_start", reason="Notebook.",
            evidence_source=NOTEBOOK_SOURCE,
        )
        _conflict(client, event_id)

        body = client.get(f"/api/field-validation/events/{event_id}").json()
        assert body["event_milestone"] == "rainfall_start"
        assert body["pre_event_timestamp_verified"] is True
        assert body["rainfall_timestamp_utc"] == "2026-03-14 14:00:00 UTC"
        assert body["unresolved_conflict_count"] == 1
        assert body["reconciliation_state"] == "blocked_by_evidence"
        assert body["independently_verified"] is False
        assert body["unknown_intervention_volumes"] is True
        assert body["actual_rainfall_mm"] == 90.0


def _resolution(client: TestClient, event_id: str, **overrides: Any) -> Any:
    """The controlled payload that replaces the seeded 19:30 IST instant."""
    payload: dict[str, Any] = {
        "timestamp": NOTEBOOK_RAINFALL_START_AT,
        "evidence_source": NOTEBOOK_SOURCE,
        "superseded_origin": "scripts/seed_field_validation.py RAIN_AT literal",
        "superseded_source_claim": "manual rain gauge",
        "reason": (
            "No rain-gauge record supports 19:30 IST and no source establishes "
            "what that time marked. The North Pan field notebook records the rain "
            "at 23:30 IST, so the notebook instant is adopted."
        ),
        "corrected_by": "audit",
    }
    payload.update(overrides)
    return client.post(
        f"/api/field-validation/events/{event_id}/rainfall-timestamp-resolution",
        json=payload,
    )


def _resolved(client: TestClient, registered_pan: str) -> dict[str, Any]:
    """An event whose conflict has been raised and then resolved on evidence."""
    event_id = report(client).json()["event_id"]
    _conflict(client, event_id)
    response = _resolution(client, event_id)
    assert response.status_code == 201, response.text
    return response.json()


class TestFilingAnUnsourcedInstant:
    """An instant nobody can account for must not arrive looking measured.

    The North Pan event was born carrying a hardcoded 19:30 IST labelled
    "manual rain gauge" with no gauge record behind it. These tests lock the
    default that keeps that from being a template for anything else: a filed
    event's rainfall instant is unsourced until somebody says otherwise, and a
    document that records a time never makes it a measured one.
    """

    def test_a_filed_instant_is_not_recorded_until_sourced(
        self, client: TestClient, registered_pan: str
    ) -> None:
        report(client)
        body = comparisons(client)[0]

        assert body["rainfall_timestamp_evidence"] is None
        assert body["rainfall_timestamp_verification"] == "not_recorded"
        assert body["rainfall_timestamp_observed"] is False
        assert body["superseded_rainfall_timestamps"] == []

    def test_filing_an_event_cannot_claim_the_instant_was_measured(
        self, client: TestClient, registered_pan: str
    ) -> None:
        response = report(
            client, rainfall_timestamp_verification="record_backed"
        )
        assert response.status_code == 422, response.text
        assert "record_backed" in response.text

    def test_an_operator_report_keeps_the_instant_unobserved(
        self, client: TestClient, registered_pan: str
    ) -> None:
        response = report(client, rainfall_timestamp_evidence=NOTEBOOK_SOURCE)
        assert response.status_code == 201, response.text
        body = comparisons(client)[0]
        assert body["rainfall_timestamp_verification"] == "operator_reported"
        # A notebook records a time. It does not measure one.
        assert body["rainfall_timestamp_observed"] is False
        assert body["independently_verified"] is False


class TestTheGenericCorrectionRefusesTheRainfallInstant:
    """The instant is only ever moved through the controlled resolution.

    The generic endpoint exists to restate a document path. The rainfall instant
    is not that: it is what the event means, so changing it requires accounting
    for the value being displaced. The refusal is the guarantee that no client
    can sidestep that by calling the ordinary correction route instead.
    """

    def test_the_generic_endpoint_still_refuses_the_instant(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _correct(
            client,
            event_id,
            field="rainfall_timestamp",
            timestamp=NOTEBOOK_RAINFALL_START_AT,
        )
        assert response.status_code == 422, response.text
        assert "rainfall-timestamp-resolution" in response.text

    def test_an_instant_already_in_force_cannot_be_restated(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _resolution(client, event_id)
        again = _resolution(client, event_id)
        assert again.status_code == 201
        assert _stored_event(client)["rainfall_timestamp"].startswith(
            "2026-03-14T18:00:00"
        )


class TestResolvingTheRainfallInstant:
    """Moving the instant onto the notebook, without inventing any authority.

    The correction is permitted because the caller had to account for what it
    displaced. What it may not do is upgrade the evidence: the instant stays
    operator-reported, the event stays unverified, and the displaced value becomes
    history rather than a second measurement.
    """

    def test_the_notebook_instant_becomes_the_one_in_force(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = _resolved(client, registered_pan)
        assert body["rainfall_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert body["rainfall_timestamp_ist"] == "2026-03-14 23:30:00 IST"
        assert _stored_event(client)["rainfall_timestamp"].startswith(
            "2026-03-14T18:00:00"
        )

    def test_the_displaced_instant_is_superseded_not_deleted(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = _resolved(client, registered_pan)
        assert len(body["superseded_rainfall_timestamps"]) == 1
        displaced = body["superseded_rainfall_timestamps"][0]
        assert displaced["timestamp_utc"] == "2026-03-14 14:00:00 UTC"
        assert displaced["timestamp_ist"] == "2026-03-14 19:30:00 IST"

    def test_the_displaced_instant_is_marked_meaning_unknown(
        self, client: TestClient, registered_pan: str
    ) -> None:
        displaced = _resolved(client, registered_pan)[
            "superseded_rainfall_timestamps"
        ][0]
        # Nothing established what 19:30 was meant to mark, so the module does not
        # guess from the event's milestone.
        assert displaced["meaning"] == "unknown"

    def test_the_displaced_instant_records_the_absent_evidence(
        self, client: TestClient, registered_pan: str
    ) -> None:
        displaced = _resolved(client, registered_pan)[
            "superseded_rainfall_timestamps"
        ][0]
        assert displaced["supporting_record"] is None
        assert displaced["origin"] == "scripts/seed_field_validation.py RAIN_AT literal"
        # The seed's gauge label is kept as a claim about provenance, never as
        # evidence that a gauge was read.
        assert displaced["source_claim"] == "manual rain gauge"

    def test_the_correction_lands_in_the_append_only_history(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _conflict(client, event_id)
        before = len(_stored_event(client)["corrections"])
        _resolution(client, event_id)

        history = _stored_event(client)["corrections"]
        assert len(history) == before + 1
        entry = history[-1]
        assert entry["field"] == "rainfall_timestamp"
        assert entry["previous_value"] == "2026-03-14 14:00:00 UTC"
        assert entry["new_value"] == "2026-03-14 18:00:00 UTC"
        assert entry["evidence_source"] == NOTEBOOK_SOURCE
        assert entry["corrected_by"] == "audit"

    def test_the_instant_stays_operator_reported_and_unobserved(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = _resolved(client, registered_pan)
        assert body["rainfall_timestamp_evidence"] == NOTEBOOK_SOURCE
        assert body["rainfall_timestamp_verification"] == "operator_reported"
        assert body["rainfall_timestamp_observed"] is False
        assert body["independently_verified"] is False
        assert body["verification_state"] == "reported"

    def test_the_resolution_cannot_claim_a_gauge_reading(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _resolution(
            client, event_id, verification="record_backed"
        )
        assert response.status_code == 422, response.text
        assert "record_backed" in response.text

    def test_the_resolution_requires_an_origin_for_the_displaced_value(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        payload: dict[str, Any] = {
            "timestamp": NOTEBOOK_RAINFALL_START_AT,
            "evidence_source": NOTEBOOK_SOURCE,
            "reason": "no origin given",
        }
        response = client.post(
            f"/api/field-validation/events/{event_id}/rainfall-timestamp-resolution",
            json=payload,
        )
        assert response.status_code == 422, response.text
        assert "superseded_origin" in response.text

    def test_the_conflict_is_closed_only_against_the_adopted_instant(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        # The disagreement the notebook raises, against the stored instant...
        assert _conflict(client, event_id).status_code == 201
        # ...and a second account at 21:00 IST that nothing here resolves.
        assert (
            _conflict(
                client,
                event_id,
                reported_timestamp="2026-03-14T15:30:00Z",
                reported_source="a second field report",
            ).status_code
            == 201
        )
        _resolution(client, event_id)

        conflicts = _stored_event(client)["rainfall_timestamp_conflicts"]
        statuses = sorted(c["status"] for c in conflicts)
        assert statuses == ["resolved", "unresolved"]
        closed = next(c for c in conflicts if c["status"] == "resolved")
        assert closed["reported_timestamp"].startswith("2026-03-14T18:00:00")
        assert closed["resolution_note"]
        # The instant nobody adopted still disagrees with what the event now
        # carries, so the event stays blocked.
        assert comparisons(client)[0]["reconciliation_state"] == "blocked_by_evidence"

    def test_resolving_unblocks_the_event_and_nothing_else_changes(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        observation = _march_observation(client)
        _link(client, event_id, observation["observation_id"])
        _correct(client, event_id, event_milestone="rainfall_start", reason="Notebook.")
        # The recorded disagreement is what blocks the event, and the instant in
        # force still cannot be called reconciled.
        _conflict(client, event_id)
        assert comparisons(client)[0]["reconciliation_state"] == "blocked_by_evidence"

        response = _resolution(client, event_id)
        assert response.status_code == 201
        body = response.json()

        assert body["unresolved_conflict_count"] == 0
        assert body["reconciliation_state"] == "complete"
        # The evidence the event rests on is untouched by the correction.
        assert body["pre_event_observation_id"] == observation["observation_id"]
        assert body["pre_event_timestamp_verified"] is True
        assert body["event_milestone"] == "rainfall_start"
        assert body["actual_rainfall_mm"] == 90.0
        assert body["forecast_rainfall_mm"] == 40.0
        assert body["unknown_intervention_volumes"] is True
        assert body["independently_verified"] is False

    def test_the_post_event_untouched_instant_is_not_upgraded(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = _resolved(client, registered_pan)
        # Correcting the rain's instant says nothing about when the post-event
        # reading was taken.
        assert body["post_event_timestamp_verified"] is False

    def test_repeating_the_same_resolution_changes_nothing(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        _resolution(client, event_id)
        stored = _stored_event(client)
        _resolution(client, event_id)
        _resolution(client, event_id)

        again = _stored_event(client)
        assert again["rainfall_timestamp"] == stored["rainfall_timestamp"]
        assert len(again["corrections"]) == len(stored["corrections"])
        assert len(again["superseded_rainfall_timestamps"]) == 1


class TestTheDisplacedInstantIsNeverPresentedAsAMeasurement:
    """19:30 IST stays visible without ever occupying a measurement's place.

    The audit requirement and the honesty requirement pull against each other
    here: the displaced value must not vanish, and it must not look like a second
    reading of when the rain fell. These tests check that every surface which
    shows it leads with the fact that nothing supported it.
    """

    def test_the_comparison_never_offers_it_as_a_timestamp(
        self, client: TestClient, registered_pan: str
    ) -> None:
        body = _resolved(client, registered_pan)
        # Exactly one instant in force, and it is the notebook's.
        assert body["rainfall_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert body["superseded_rainfall_timestamps"][0]["timestamp_utc"] == (
            "2026-03-14 14:00:00 UTC"
        )

    def test_a_caveat_names_what_was_displaced_and_why_it_is_not_a_reading(
        self, client: TestClient, registered_pan: str
    ) -> None:
        caveats = " ".join(_resolved(client, registered_pan)["caveats"])
        assert "2026-03-14 18:00:00 UTC" in caveats
        assert "2026-03-14 19:30:00 IST" in caveats
        assert "no record in this system supported it" in caveats
        assert "not a measurement" in caveats

    def test_a_caveat_says_the_instant_is_reported_not_measured(
        self, client: TestClient, registered_pan: str
    ) -> None:
        caveats = " ".join(_resolved(client, registered_pan)["caveats"])
        assert "operator-reported" in caveats
        assert "does not measure one" in caveats

    def test_the_caveat_names_the_origin_of_the_displaced_value(
        self, client: TestClient, registered_pan: str
    ) -> None:
        caveats = " ".join(_resolved(client, registered_pan)["caveats"])
        assert "seed_field_validation.py RAIN_AT literal" in caveats

    def test_the_csv_labels_the_value_superseded_and_unsupported(
        self, client: TestClient, registered_pan: str
    ) -> None:
        _resolved(client, registered_pan)
        response = client.get("/api/field-validation/export", params={"format": "csv"})
        assert response.status_code == 200
        row = next(
            r
            for r in csv.DictReader(io.StringIO(response.text))
            if r["pan_id"] == "north-pan"
        )

        assert row["rainfall_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert row["rainfall_timestamp_ist"] == "2026-03-14 23:30:00 IST"
        assert row["rainfall_timestamp_observed"] == "False"
        assert row["rainfall_timestamp_verification"] == "operator_reported"
        superseded = row["superseded_rainfall_timestamps"]
        assert "SUPERSEDED" in superseded
        assert "2026-03-14 14:00:00 UTC" in superseded
        assert "2026-03-14 19:30:00 IST" in superseded
        assert "not a measurement" in superseded

    def test_the_pdf_reports_the_instant_and_the_superseded_value(
        self, client: TestClient, registered_pan: str
    ) -> None:
        _resolved(client, registered_pan)
        response = client.get("/api/field-validation/export", params={"format": "pdf"})
        assert response.status_code == 200
        text = _pdf_text(response.content)
        assert "rainfall instant in force" in text
        assert "2026-03-14 18:00:00 UTC" in text
        assert "2026-03-14 23:30:00 IST" in text
        assert "not a measurement" in text
        assert "2026-03-14 14:00:00 UTC" in text
        assert "2026-03-14 19:30:00 IST" in text

    def test_a_resolved_conflict_no_longer_claims_the_stored_value_is_unchanged(
        self, client: TestClient, registered_pan: str
    ) -> None:
        _resolved(client, registered_pan)
        response = client.get("/api/field-validation/export", params={"format": "pdf"})
        text = _pdf_text(response.content)
        assert "has not been changed" not in text
        assert "Resolved: the event now carries" in text


class TestResolvingFromALocalClockReading:
    """A browser may send a local reading; the server places it on the timeline.

    The browser is never asked to convert a zone, because a second conversion
    could only ever disagree with the record. What the server does instead is
    refuse the ambiguous cases outright.
    """

    def test_a_local_reading_is_resolved_against_its_declared_zone(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _resolution(
            client,
            event_id,
            timestamp=None,
            timestamp_local="2026-03-14T23:30",
            timestamp_timezone="IST",
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["rainfall_timestamp_utc"] == "2026-03-14 18:00:00 UTC"
        assert body["rainfall_timestamp_ist"] == "2026-03-14 23:30:00 IST"

    def test_a_local_reading_with_no_zone_is_refused_rather_than_assumed(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _resolution(
            client, event_id, timestamp=None, timestamp_local="2026-03-14T23:30"
        )
        assert response.status_code == 422, response.text
        assert "timestamp_timezone" in response.text

    def test_a_reading_contradicting_its_own_offset_is_refused(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _resolution(
            client,
            event_id,
            timestamp=None,
            timestamp_local="2026-03-14T23:30:00+00:00",
            timestamp_timezone="IST",
        )
        assert response.status_code == 422, response.text
        assert "not" in response.text

    def test_an_unreadable_zone_is_refused_rather_than_guessed(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _resolution(
            client,
            event_id,
            timestamp=None,
            timestamp_local="2026-03-14T23:30",
            timestamp_timezone="the pan's usual time",
        )
        assert response.status_code == 422, response.text
        assert "time zone" in response.text.lower()

    def test_the_instant_must_be_given_exactly_once(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        both = _resolution(
            client,
            event_id,
            timestamp_local="2026-03-14T23:30",
            timestamp_timezone="IST",
        )
        assert both.status_code == 422, both.text
        assert "not both" in both.text

    def test_the_instant_cannot_be_omitted(
        self, client: TestClient, registered_pan: str
    ) -> None:
        event_id = report(client).json()["event_id"]
        response = _resolution(client, event_id, timestamp=None)
        assert response.status_code == 422, response.text
        assert "timestamp_local" in response.text
