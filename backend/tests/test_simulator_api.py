"""Module 6 - what-if rainfall simulator API tests.

These lock down the request/response contract rather than the arithmetic, which
``test_mass_balance.py`` covers directly. The behaviours worth defending here are
the ones a caller could otherwise get wrong: that every result is labelled as
simulated and carries its assumptions, that a degenerate input is refused instead
of divided by, and that the scenario sweep is complete, ordered and comparable.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

VALID_INPUTS = {
    "area_m2": 12_500.0,
    "initial_depth_m": 0.155,
    "initial_salinity_g_l": 285.0,
}


@pytest.fixture()
def client() -> Any:
    with TestClient(app) as test_client:
        yield test_client


# -- status: what this module claims to be --------------------------------


def test_status_reports_the_module_as_implemented(client: TestClient):
    response = client.get("/api/simulator/status")
    assert response.status_code == 200
    body = response.json()
    assert body["module"] == "mass-balance-simulator"
    assert body["module_number"] == 6
    assert body["status"] == "implemented"
    assert "POST /api/simulator/mass-balance" in body["endpoints"]
    assert "POST /api/simulator/rainfall-scenarios" in body["endpoints"]


def test_status_states_that_this_is_physics_and_not_ml(client: TestClient):
    body = client.get("/api/simulator/status").json()
    assert body["model"]["is_ml"] is False
    assert body["model"]["kind"] == "deterministic physics"
    assert body["disclaimer"] == (
        "Physics-based simulation under simplifying assumptions; "
        "not an ML prediction."
    )


def test_status_discloses_every_assumption_and_the_preset_depths(client: TestClient):
    body = client.get("/api/simulator/status").json()
    assert body["standard_scenarios_mm"] == [0, 5, 10, 20, 50, 100]
    joined = " ".join(body["assumptions"]).lower()
    for excluded in (
        "complete mixing",
        "evaporation",
        "drainage",
        "overflow",
        "seepage",
        "transfer",
        "crystallization",
    ):
        assert excluded in joined, f"{excluded} is not disclosed"


def test_status_needs_no_database(client: TestClient):
    """The balance has no history to read, so it must not fail without Mongo."""
    from app.api.deps import get_db, get_optional_db

    app.dependency_overrides[get_db] = lambda: (_ for _ in ()).throw(
        AssertionError("the simulator must not ask for a database")
    )
    app.dependency_overrides[get_optional_db] = lambda: None
    try:
        assert client.get("/api/simulator/status").status_code == 200
    finally:
        app.dependency_overrides.clear()


# -- a single rainfall event ----------------------------------------------


def test_mass_balance_returns_every_reported_quantity(client: TestClient):
    response = client.post(
        "/api/simulator/mass-balance", json={**VALID_INPUTS, "rainfall_mm": 20.0}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["initial_volume_m3"] == pytest.approx(1937.5)
    assert body["rainfall_volume_m3"] == pytest.approx(250.0)
    assert body["post_rain_volume_m3"] == pytest.approx(2187.5)
    assert body["post_rain_salinity_g_l"] == pytest.approx(252.42857142857142)
    assert body["dilution_percent"] == pytest.approx(11.428571428571432)
    assert body["salinity_drop_g_l"] == pytest.approx(32.571428571428584)
    assert body["post_rain_depth_m"] == pytest.approx(0.175)
    assert body["initial_salinity_g_l"] == pytest.approx(285.0)
    assert body["rainfall_mm"] == pytest.approx(20.0)


def test_every_result_is_labelled_simulated_and_carries_its_assumptions(
    client: TestClient,
):
    body = client.post(
        "/api/simulator/mass-balance", json={**VALID_INPUTS, "rainfall_mm": 20.0}
    ).json()

    assert body["is_simulated"] is True
    assert body["disclaimer"].startswith("Physics-based simulation")
    assert len(body["assumptions"]) >= 7
    assert body["equations"] == [
        "V0 = A x D0  —  initial brine volume (m3)",
        "Vr = A x R  —  rainfall volume (m3)",
        "V1 = V0 + Vr  —  post-rain volume (m3)",
        "C1 = C0 x V0 / V1  —  predicted post-rain salinity (g/L)",
        "Dilution % = ((C0 - C1) / C0) x 100",
    ]


def test_the_salt_in_the_pan_is_reported_as_conserved(client: TestClient):
    body = client.post(
        "/api/simulator/mass-balance", json={**VALID_INPUTS, "rainfall_mm": 20.0}
    ).json()
    assert body["initial_salt_mass_kg"] == pytest.approx(552_187.5)
    assert body["post_rain_salt_mass_kg"] == pytest.approx(body["initial_salt_mass_kg"])
    assert body["salt_mass_conserved"] is True


def test_a_request_echoes_back_the_inputs_it_was_given(client: TestClient):
    body = client.post(
        "/api/simulator/mass-balance", json={**VALID_INPUTS, "rainfall_mm": 50.0}
    ).json()
    assert body["area_m2"] == pytest.approx(12_500.0)
    assert body["initial_depth_m"] == pytest.approx(0.155)
    assert body["initial_salinity_g_l"] == pytest.approx(285.0)
    assert body["rainfall_mm"] == pytest.approx(50.0)


def test_no_rain_is_a_valid_scenario_and_dilutes_nothing(client: TestClient):
    body = client.post(
        "/api/simulator/mass-balance", json={**VALID_INPUTS, "rainfall_mm": 0.0}
    ).json()
    assert body["rainfall_volume_m3"] == pytest.approx(0.0)
    assert body["post_rain_salinity_g_l"] == pytest.approx(285.0)
    assert body["dilution_percent"] == pytest.approx(0.0)


def test_fresh_brine_reports_no_dilution_percentage_rather_than_zero(
    client: TestClient,
):
    body = client.post(
        "/api/simulator/mass-balance",
        json={**VALID_INPUTS, "initial_salinity_g_l": 0.0, "rainfall_mm": 20.0},
    ).json()
    assert body["post_rain_salinity_g_l"] == pytest.approx(0.0)
    assert body["dilution_percent"] is None
    assert "undefined" in body["dilution_percent_reason"]


# -- inputs that would make the balance meaningless -----------------------


@pytest.mark.parametrize(
    "override, expected",
    [
        ({"area_m2": 0.0}, "greater than zero"),
        ({"area_m2": -1.0}, "greater than zero"),
        ({"initial_depth_m": 0.0}, "no salinity to dilute"),
        ({"initial_salinity_g_l": -5.0}, "negative"),
        ({"rainfall_mm": -1.0}, "Zero is a valid scenario"),
    ],
)
def test_a_degenerate_input_is_refused_with_an_explanation(
    client: TestClient, override: dict[str, float], expected: str
):
    payload = {**VALID_INPUTS, "rainfall_mm": 20.0, **override}
    response = client.post("/api/simulator/mass-balance", json=payload)
    assert response.status_code == 422, response.text
    assert expected in response.json()["detail"]


@pytest.mark.parametrize(
    "override",
    [
        {"area_m2": 0.0},
        {"initial_depth_m": 0.0},
        {"initial_salinity_g_l": -5.0},
        {"rainfall_mm": -1.0},
        {"rainfall_mm": 10_000.0},
        {"initial_depth_m": 5_000.0},
    ],
)
def test_the_sweep_applies_the_same_guards(client: TestClient, override: dict[str, float]):
    response = client.post(
        "/api/simulator/rainfall-scenarios", json={**VALID_INPUTS, **override}
    )
    assert response.status_code == 422, response.text


def test_a_missing_input_is_refused_rather_than_defaulted(client: TestClient):
    """Every input is required: a default would be a fabricated starting brine."""
    response = client.post(
        "/api/simulator/mass-balance", json={"area_m2": 12_500.0, "rainfall_mm": 20.0}
    )
    assert response.status_code == 422
    missing = {error["loc"][-1] for error in response.json()["detail"]}
    assert {"initial_depth_m", "initial_salinity_g_l"} <= missing


def test_an_unknown_field_is_refused_rather_than_ignored(client: TestClient):
    response = client.post(
        "/api/simulator/mass-balance",
        json={**VALID_INPUTS, "rainfall_mm": 20.0, "predicted_salinity_g_l": 12.0},
    )
    assert response.status_code == 422
    assert "predicted_salinity_g_l" in response.text


# -- the scenario sweep the what-if panel is built on ---------------------


def test_a_sweep_returns_the_six_presets_in_ascending_order(client: TestClient):
    response = client.post("/api/simulator/rainfall-scenarios", json=VALID_INPUTS)
    assert response.status_code == 200, response.text
    body = response.json()

    assert [item["rainfall_mm"] for item in body["scenarios"]] == [
        0.0, 5.0, 10.0, 20.0, 50.0, 100.0,
    ]
    assert body["standard_scenarios_mm"] == [0.0, 5.0, 10.0, 20.0, 50.0, 100.0]
    assert all(item["is_standard_preset"] is True for item in body["scenarios"])


def test_a_sweep_shares_one_starting_volume_and_salinity(client: TestClient):
    body = client.post("/api/simulator/rainfall-scenarios", json=VALID_INPUTS).json()
    assert body["initial_volume_m3"] == pytest.approx(1937.5)
    assert body["initial_salinity_g_l"] == pytest.approx(285.0)
    assert body["is_simulated"] is True
    assert body["disclaimer"].startswith("Physics-based simulation")
    assert len(body["assumptions"]) >= 7
    assert len(body["equations"]) == 5


def test_a_sweep_falls_monotonically_so_the_chart_reads_left_to_right(
    client: TestClient,
):
    body = client.post("/api/simulator/rainfall-scenarios", json=VALID_INPUTS).json()
    salinities = [item["post_rain_salinity_g_l"] for item in body["scenarios"]]
    assert all(later < earlier for earlier, later in itertools.pairwise(salinities))
    assert salinities[0] == pytest.approx(285.0)
    assert all(value > 0.0 for value in salinities)


def test_a_custom_depth_is_swept_alongside_the_presets(client: TestClient):
    body = client.post(
        "/api/simulator/rainfall-scenarios",
        json={**VALID_INPUTS, "rainfall_scenarios_mm": [12.5, 75.0]},
    ).json()
    assert [item["rainfall_mm"] for item in body["scenarios"]] == [
        0.0, 5.0, 10.0, 12.5, 20.0, 50.0, 75.0, 100.0,
    ]
    custom = [item for item in body["scenarios"] if item["rainfall_mm"] == 12.5]
    assert custom[0]["is_standard_preset"] is False
    assert custom[0]["label"] == "12.5 mm — custom"


def test_a_custom_depth_matching_a_preset_is_not_repeated(client: TestClient):
    body = client.post(
        "/api/simulator/rainfall-scenarios",
        json={**VALID_INPUTS, "rainfall_scenarios_mm": [20.0, 5.0]},
    ).json()
    assert [item["rainfall_mm"] for item in body["scenarios"]] == [
        0.0, 5.0, 10.0, 20.0, 50.0, 100.0,
    ]


def test_a_negative_custom_depth_is_refused(client: TestClient):
    response = client.post(
        "/api/simulator/rainfall-scenarios",
        json={**VALID_INPUTS, "rainfall_scenarios_mm": [20.0, -5.0]},
    )
    assert response.status_code == 422
    assert "Rainfall depth cannot be negative" in response.json()["detail"]


def test_every_scenario_carries_its_own_dilution_and_volumes(client: TestClient):
    body = client.post("/api/simulator/rainfall-scenarios", json=VALID_INPUTS).json()
    for item in body["scenarios"]:
        depth = item["rainfall_mm"] / 1000.0
        assert item["rainfall_volume_m3"] == pytest.approx(12_500.0 * depth)
        assert item["post_rain_volume_m3"] == pytest.approx(1937.5 + 12_500.0 * depth)
        assert item["post_rain_salinity_g_l"] == pytest.approx(
            285.0 * 1937.5 / item["post_rain_volume_m3"]
        )
        assert item["dilution_percent"] == pytest.approx(
            (285.0 - item["post_rain_salinity_g_l"]) / 285.0 * 100.0
        )
        assert item["post_rain_depth_m"] == pytest.approx(0.155 + depth)


def test_the_zero_rain_scenario_is_labelled_as_such(client: TestClient):
    body = client.post("/api/simulator/rainfall-scenarios", json=VALID_INPUTS).json()
    assert body["scenarios"][0]["label"] == "0 mm — no rain"
