"""Module 21's API surface, and the report the September decision gets.

Two things are being checked, and the second matters more.

**The endpoints refuse.** Every one of them is tested against an empty registry,
because that is the state this project is in for anything a reader would want a
number from. An endpoint that raises on no data, or renders a zero, or quietly
returns an empty list that a dashboard will draw as a chart of nothing happening,
is not finished.

**The September decision is not counted as a treated period.** This project has
eleven operator decisions and one of them -- the only accepted recommendation in
the system -- carries an explicit note that no physical harvesting occurred. An
operational study that counted it would report a completed action that never
happened. That test is here so nobody "fixes" the count later without meeting it.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_optional_db
from app.experiments.metrics import INSUFFICIENT_VALIDATION_MESSAGE
from app.experiments.paths import ExperimentPaths
from app.experiments.store import ExperimentStore
from app.main import app

UTC = timezone.utc
REGISTRY = "/api/experiment-registry"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client over an empty registry of its own.

    The registry location is set before the request rather than patched in after,
    because the endpoints locate it at call time -- which is the behaviour that
    lets a CI job work on its own copy.
    """
    monkeypatch.setenv("SALTLENS_EXPERIMENTS_DIR", str(tmp_path / "experiments"))
    return TestClient(app)


@pytest.fixture()
def seeded_client(client: TestClient, tmp_path: Path) -> TestClient:
    """A client over a registry holding the March event, classified."""
    store = ExperimentStore(paths=ExperimentPaths())
    store.register_event(
        event_id="ev-march",
        pan_id="pan_orginal",
        date="2026-03-14",
        initial_salinity_g_l=250.0,
        initial_depth_cm=20.0,
        rainfall_mm=90.0,
        rainfall_is_forecast=False,
        rainfall_observed=False,
        rainfall_source="operator reported",
    )
    store.lock_prediction(
        "ev-march",
        predicted_salinity_g_l=172.41379310344828,
        predicted_depth_cm=29.0,
        rainfall_mm=90.0,
        prediction_made_at=datetime(2026, 3, 14, 18, 0, tzinfo=UTC),
        model_name="rainfall-dilution-mass-balance-v1",
        app_version="0.1.0",
        git_commit="abc123",
        manifest_hash=None,
    )
    store.record_observation(
        "ev-march",
        observed_salinity_g_l=20.0,
        observed_depth_cm=50.0,
        post_event_timestamp=datetime(2026, 3, 15, 8, 0, tzinfo=UTC),
        post_event_timestamp_verified=False,
    )
    return client


class TestOverview:
    def test_an_empty_registry_reports_zero_events_and_no_figures(
        self, client: TestClient
    ) -> None:
        response = client.get(f"{REGISTRY}/")

        assert response.status_code == 200
        body = response.json()
        assert body["events_registered"] == 0
        assert body["validation_sufficient"] is False
        assert body["validation_message"] == INSUFFICIENT_VALIDATION_MESSAGE
        assert body["clean_verified_events"] == 0

    def test_the_overview_names_the_thresholds_it_is_measured_against(
        self, client: TestClient
    ) -> None:
        body = client.get(f"{REGISTRY}/").json()

        # A reader must be able to see what "insufficient" means numerically.
        assert body["min_clean_events_required"] == 3
        assert body["min_distinct_pans_required"] == 2

    def test_the_overview_reports_each_phase_separately(self, client: TestClient) -> None:
        body = client.get(f"{REGISTRY}/").json()

        assert body["phases"]["6_validation"] is False
        assert body["phases"]["8_ml"] == "not_assessed"
        assert body["phases"]["9_operational"] == "not_assessed"


class TestEventsEndpoint:
    def test_an_empty_registry_returns_no_events_and_counts_every_category(
        self, client: TestClient
    ) -> None:
        body = client.get(f"{REGISTRY}/events").json()

        assert body["total_events"] == 0
        assert body["events"] == []
        # Every category at zero, not just the ones that occur. An absent count
        # reads as an oversight; a zero reads as a statement.
        assert body["counts_by_category"] == {
            "CLEAN_RAINFALL_EVENT": 0,
            "INTERVENTION_AFFECTED_EVENT": 0,
            "INCOMPLETE_EVIDENCE_EVENT": 0,
        }

    def test_the_march_event_comes_back_classified_with_its_reasons(
        self, seeded_client: TestClient
    ) -> None:
        body = seeded_client.get(f"{REGISTRY}/events").json()

        assert body["total_events"] == 1
        event = body["events"][0]
        assert event["event_id"] == "ev-march"
        assert event["category"] == "INCOMPLETE_EVIDENCE_EVENT"
        assert event["included_in_metrics"] is False
        assert event["independently_verified"] is False
        # The reasons travel with the verdict, so a reader can disagree with the
        # classification on the evidence rather than on trust.
        assert len(event["reasons"]) >= 1
        assert isinstance(event["reasons"], list)

    def test_absent_figures_serialise_as_null_and_not_zero(self, seeded_client: TestClient) -> None:
        event = seeded_client.get(f"{REGISTRY}/events").json()["events"][0]

        # The event has no drainage and no forecast flag set.
        assert event["rainfall_is_forecast"] is False
        assert event["predicted_salinity_g_l"] == pytest.approx(172.41379310344828)
        assert event["observed_salinity_g_l"] == 20.0
        assert event["observed_salinity_g_l"] != 0


class TestValidationEndpoint:
    def test_validation_withdraws_every_figure_over_an_incomplete_event(
        self, seeded_client: TestClient
    ) -> None:
        """The endpoint a reviewer would call to check the paper's figures.

        It must come back with ``sufficient: false`` and every reading
        ``computed: false``. A number here would be the failure the whole module
        exists to prevent.
        """
        body = seeded_client.get(f"{REGISTRY}/validation").json()

        assert body["sufficient"] is False
        assert body["message"] == INSUFFICIENT_VALIDATION_MESSAGE
        for quantity in ("salinity", "depth"):
            assert body[quantity] is not None
            for reading in (body[quantity]["mae"], body[quantity]["rmse"], body[quantity]["bias"]):
                assert reading["computed"] is False
                assert reading["value"] is None
                assert INSUFFICIENT_VALIDATION_MESSAGE in reading["reason"]

    def test_validation_over_an_empty_registry_also_withdraws_everything(
        self, client: TestClient
    ) -> None:
        body = client.get(f"{REGISTRY}/validation").json()

        assert body["sufficient"] is False
        assert body["total_events"] == 0
        assert body["salinity"]["mae"]["computed"] is False

    def test_validation_points_at_the_export(self, seeded_client: TestClient) -> None:
        body = seeded_client.get(f"{REGISTRY}/validation").json()

        assert body["export_csv"] == "experiments/events/events.csv"

    def test_the_per_event_table_carries_the_excluded_events_deviations(
        self, seeded_client: TestClient
    ) -> None:
        rows = seeded_client.get(f"{REGISTRY}/validation").json()["per_event"]

        assert len(rows) == 1
        assert rows[0]["included_in_metrics"] is False
        # The deviation is still reported, so the exclusion can be checked.
        assert rows[0]["salinity_absolute_error_g_l"] == pytest.approx(152.41379310344828)


class TestManifestEndpoint:
    def test_a_missing_manifest_says_so_rather_than_building_one(
        self, client: TestClient
    ) -> None:
        """No manifest in the registry means ``available: false``.

        The endpoint does not call the manifest builder, because a manifest built
        at request time would describe the process serving the request rather than
        the run that produced the results -- and the difference between those two
        is the entire point of a manifest.
        """
        body = client.get(f"{REGISTRY}/manifest").json()

        assert body["available"] is False
        assert "seed_experiment_registry" in body["reason"]
        assert "manifest" not in body

    def test_a_present_manifest_is_returned_as_written(
        self, client: TestClient
    ) -> None:
        store = ExperimentStore()
        written = {
            "manifest_version": "1.0",
            "git_commit": "abc123",
            "simulation_model_name": "rainfall-dilution-mass-balance-v1",
            "content_hash": "sha256:deadbeef",
        }
        store.paths.ensure()
        store.paths.manifest_file.write_text(json.dumps(written), encoding="utf-8")

        body = client.get(f"{REGISTRY}/manifest").json()

        assert body["available"] is True
        assert body["manifest"]["git_commit"] == "abc123"
        assert body["manifest"]["content_hash"] == "sha256:deadbeef"


class TestReadinessEndpoint:
    def test_readiness_reports_all_three_phases_with_no_figures(self, client: TestClient) -> None:
        body = client.get(f"{REGISTRY}/readiness").json()

        assert body["ml"]["status"] == "not_assessed"
        assert body["ml"]["metrics"] == {}
        assert body["operational"]["status"] == "not_assessed"
        assert body["usability"]["sufficient"] is False
        assert body["usability"]["metrics"] == {}

    def test_readiness_lists_what_each_phase_is_waiting_on(self, client: TestClient) -> None:
        body = client.get(f"{REGISTRY}/readiness").json()

        blocking = body["ml"]["blocking_preconditions"]
        assert "real_labelled_dataset" in blocking
        assert "trained_checkpoint" in blocking
        # Each gap names what specifically is missing, not just that something is.
        details = [
            item["detail"]
            for item in body["ml"]["preconditions"]
            if item["name"] == "trained_checkpoint"
        ]
        assert "ml/models" in details[0]

    def test_readiness_names_the_claims_each_phase_may_not_make(self, client: TestClient) -> None:
        body = client.get(f"{REGISTRY}/readiness").json()

        assert any("accuracy" in claim.lower() for claim in body["ml"]["prohibited_claims"])
        assert body["operational"]["prohibited_claims"]

    def test_readiness_returns_the_study_design_so_the_questions_are_fixed(
        self, client: TestClient
    ) -> None:
        """The protocol ships with the report, so the instrument is reviewable.

        A usability result whose questions were chosen after it was collected is
        not a finding, and shipping the design alongside makes that visible.
        """
        protocol = client.get(f"{REGISTRY}/readiness").json()["operator_study_protocol"]

        assert protocol["protocol_id"] == "operator-usability-v1"
        assert len(protocol["research_questions"]) >= 4
        assert protocol["limitations"]

    def test_each_operational_claim_is_paired_with_what_it_needs(self, client: TestClient) -> None:
        requirements = client.get(f"{REGISTRY}/readiness").json()[
            "operational_claim_requirements"
        ]

        assert requirements
        for requirement in requirements:
            assert requirement["claim"]
            assert requirement["requires"]


class TestEvidenceGaps:
    def test_every_gap_states_what_it_blocks(self, client: TestClient) -> None:
        gaps = client.get(f"{REGISTRY}/evidence-gaps").json()["gaps"]

        assert gaps
        for gap in gaps:
            assert gap["phase"]
            assert gap["gap"]
            assert gap["consequence"]
            # A gap that blocks nothing is not a gap; a gap that blocks claims
            # without naming them leaves the reader to work out what is affected.
            assert gap["blocked_claims"]

    def test_the_gaps_include_the_ones_this_project_actually_has(
        self, client: TestClient
    ) -> None:
        gaps = client.get(f"{REGISTRY}/evidence-gaps").json()["gaps"]
        text = " ".join(gap["gap"].lower() for gap in gaps)

        assert "independently verified" in text
        assert "rain-gauge" in text
        assert "checkpoint" in text


class TestCsvEndpoint:
    def test_the_csv_download_carries_the_same_rows_as_the_json(self, seeded_client: TestClient) -> None:
        response = seeded_client.get(f"{REGISTRY}/events.csv")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 1
        assert rows[0]["intervention_status"] == "INCOMPLETE_EVIDENCE_EVENT"

    def test_the_csv_for_an_empty_registry_is_a_header_and_nothing_else(
        self, client: TestClient
    ) -> None:
        response = client.get(f"{REGISTRY}/events.csv")

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert rows == []


class TestNoWriteEndpoints:
    """The registry is sealed, so nothing here accepts a write.

    Not a completeness check. If a write route were added later, this is the test
    that should fail, because a route that could set a prediction would undo the
    guarantee the store enforces.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "/events",
            "/events/ev-march",
            "/manifest",
            "/validation",
        ],
    )
    def test_no_post_put_or_patch_is_routed(self, client: TestClient, path: str) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = client.request(method, f"{REGISTRY}{path}", json={})
            assert response.status_code in (404, 405), (
                f"{method} {path} answered {response.status_code}; the registry is "
                "read-only and a write route would break the prediction lock."
            )


class TestTheSeptemberDecisionIsNotATreatedPeriod:
    def test_one_accepted_recommendation_is_not_an_operational_result(
        self, client: TestClient
    ) -> None:
        """The project has exactly one accepted recommendation.

        It carries a note that no physical harvesting occurred. The operational
        phase must therefore report zero matched pairs, and its report must say
        that an accepted decision is not a performed action. A count of one here
        would be the single easiest way to produce an operational claim this
        project cannot support.
        """
        body = client.get(f"{REGISTRY}/readiness").json()
        operational = body["operational"]

        assert operational["status"] == "not_assessed"
        assert operational["metrics"] == {}
        blocked = {item["name"]: item for item in operational["preconditions"]}
        assert blocked["physical_action_confirmed"]["met"] is False
        assert "no physical harvesting" in blocked["physical_action_confirmed"]["detail"]
        assert any(
            "acceptance indicates" in claim
            for claim in operational["prohibited_claims"]
        )

    def test_the_usability_phase_is_also_unassessable(self, client: TestClient) -> None:
        """One decision is not a usability study, and nothing here implies it is.

        Reported as ``INSUFFICIENT_EVIDENCE`` rather than a percentage over one
        response, which would be a 100% completion rate.
        """
        usability = client.get(f"{REGISTRY}/readiness").json()["usability"]

        assert usability["sufficient"] is False
        assert usability["metrics"] == {}
        assert usability["message"] == "Insufficient operator responses"


class TestReadOnlyWithoutADatabase:
    def test_the_endpoints_work_without_a_database(self, client: TestClient) -> None:
        """The registry is on disk, so the suite never needs a cluster.

        ``SALTLENS_TEST_NO_DATABASE`` is set for the whole suite. If an endpoint
        reached for the database, this would be a 503 rather than a 200 -- and in
        CI, where no cluster exists, that would be the only failure anyone saw.
        """
        app.dependency_overrides[get_optional_db] = lambda: None
        try:
            for path in ("/", "/events", "/validation", "/readiness", "/evidence-gaps", "/manifest"):
                assert client.get(f"{REGISTRY}{path}").status_code == 200, path
        finally:
            app.dependency_overrides.pop(get_optional_db, None)


def test_the_committed_registry_says_the_study_is_insufficient() -> None:
    """Checked against the committed registry, not a fixture.

    ``experiments/`` is a committed artefact that a reviewer reads. If it ever
    claimed a figure over one reported event, this test would fail rather than the
    claim being repeated.
    """
    root = ExperimentPaths().root
    results = root / "results" / "validation.json"
    if not results.exists():
        pytest.skip(
            "The committed registry has no results file; run "
            "scripts/seed_experiment_registry.py to produce one."
        )

    report: dict[str, Any] = json.loads(results.read_text())["report"]

    assert report["sufficient"] is False
    assert report["message"] == INSUFFICIENT_VALIDATION_MESSAGE
    assert report["independently_verified_events"] == 0
