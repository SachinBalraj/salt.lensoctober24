from datetime import datetime

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.models import (
    ActualOutcome,
    DigitalTwinState,
    Explanation,
    MLPrediction,
    ModelVersion,
    OperatorDecision,
    PanObservation,
    Recommendation,
    SaltPan,
    SimulationRun,
    User,
    WeatherObservation,
)
from app.models.enums import (
    HarvestOutcome,
    MlTarget,
    ModelStatus,
    OperatorDecisionType,
    ResearchDataSource,
    ResearchTarget,
    SimulationStatus,
    WeatherSource,
)
from app.models.salt_pan import RiskThresholds


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def risks(**overrides: float) -> RiskThresholds:
    values = {
        "flood_depth_cm": 40.0,
        "dilution_salinity_g_l": 180.0,
        "critical_rainfall_24h_mm": 75.0,
        "target_flow_cm": 15.0,
    }
    values.update(overrides)
    return RiskThresholds(**values)


def pan(**overrides) -> SaltPan:
    values = {
        "pan_id": "PAN-01",
        "pan_name": "Arrival Pan",
        "location_name": "Thoothukudi",
        "area_m2": 5000.0,
        "normal_depth_cm": 25.0,
        "target_harvest_salinity_g_l": 300.0,
        "risk_thresholds": risks(),
    }
    values.update(overrides)
    return SaltPan(**values)


def observation(**overrides) -> PanObservation:
    values = {
        "pan_id": "PAN-01",
        "timestamp": utc("2026-01-01T06:00:00Z"),
        "salinity_g_l": 285.0,
        "water_depth_cm": 24.0,
        "brine_temperature_c": 31.2,
        "humidity_percent": 62.0,
    }
    values.update(overrides)
    return PanObservation(**values)


def weather(**overrides) -> WeatherObservation:
    values = {
        "pan_id": "PAN-01",
        "timestamp": utc("2026-01-01T06:00:00Z"),
        "rainfall_mm": 4.0,
        "ambient_temperature_c": 30.0,
        "humidity_percent": 70.0,
    }
    values.update(overrides)
    return WeatherObservation(**values)


def test_all_requested_models_expose_their_persistence_fields():
    expected = {
        User: {"username", "password_hash", "created_at"},
        SaltPan: {"pan_id", "pan_name", "location_name", "risk_thresholds"},
        PanObservation: {"pan_id", "timestamp", "data_source", "created_at"},
        WeatherObservation: {"pan_id", "timestamp", "forecast_for", "created_at"},
        DigitalTwinState: {"pan_id", "as_of", "provenance"},
        SimulationRun: {"pan_id", "status", "is_simulated"},
        MLPrediction: {"pan_id", "model_version", "target"},
        Explanation: {"prediction_id", "attributions"},
        Recommendation: {"pan_id", "priority", "issued_at"},
        OperatorDecision: {"recommendation_id", "decision"},
        ActualOutcome: {"pan_id", "event_id", "timestamp"},
        ModelVersion: {"version", "target", "status"},
    }
    for model, fields in expected.items():
        assert fields <= set(model.model_fields), model.__name__


def test_models_generate_metadata_but_not_measurements():
    user = User(username="operator", password_hash="hash")
    state = DigitalTwinState(pan_id="PAN-01", as_of=utc("2026-01-01T00:00:00Z"))
    prediction = MLPrediction(
        pan_id="PAN-01",
        model_version="1.0.0",
        target=MlTarget.SALINITY_G_L,
    )
    assert user.created_at.tzinfo is not None
    assert state.estimated_salinity_g_l is None
    assert prediction.prediction is None
    assert prediction.probability is None


def test_mongo_round_trip_preserves_object_id_and_enums():
    document = pan()
    object_id = ObjectId()
    document.id = object_id
    stored = document.to_mongo()
    assert stored["_id"] == object_id
    assert stored["pan_id"] == "pan-01"
    restored = SaltPan.from_mongo(stored)
    assert restored.id == object_id
    assert restored.created_at == document.created_at


def test_pan_normalizes_ids_and_accepts_location_alias():
    document = SaltPan(
        pan_id="PAN-02",
        pan_name="Arrival Pan",
        location="Kanyakumari",
        area_m2=5000.0,
        normal_depth_cm=25.0,
        target_harvest_salinity_g_l=300.0,
        risk_thresholds=risks(),
    )
    assert document.pan_id == "pan-02"
    assert document.location_name == "Kanyakumari"


def test_risk_threshold_aliases_are_stored_canonically():
    document = pan(
        risk_thresholds={
            "max_depth_cm": 20,
            "min_salinity_g_l": 180,
            "max_rainfall_24h_mm": 75,
            "flow_depth_cm": 15,
        }
    )
    stored = document.to_mongo()["risk_thresholds"]
    assert stored["flood_depth_cm"] == 20
    assert stored["dilution_salinity_g_l"] == 180
    assert "max_depth_cm" not in stored
    assert "min_salinity_g_l" not in stored


def test_pan_requires_coordinate_pairs_and_positive_geometry():
    with pytest.raises(ValidationError, match="provided together"):
        pan(latitude=8.5)
    with pytest.raises(ValidationError):
        pan(area_m2=0)
    with pytest.raises(ValidationError):
        pan(risk_thresholds=risks(target_flow_cm=-1))


def test_observation_rejects_empty_rows_and_invalid_ranges():
    with pytest.raises(ValidationError, match="at least one"):
        PanObservation(pan_id="PAN-01", timestamp=utc("2026-01-01T00:00:00Z"))
    with pytest.raises(ValidationError):
        observation(salinity_g_l=-1)
    with pytest.raises(ValidationError):
        observation(humidity_percent=101)


def test_observation_keeps_unavailable_ambient_temperature_none():
    document = observation(ambient_temperature_c=None)
    assert document.ambient_temperature_c is None
    assert document.data_source == ResearchDataSource.MANUAL


def test_weather_canonical_timestamp_and_legacy_alias_round_trip():
    document = weather()
    assert document.timestamp == document.observed_at
    assert document.created_at == document.recorded_at
    legacy = WeatherObservation(
        pan_id="PAN-01",
        observed_at=utc("2026-01-01T06:00:00Z"),
        rainfall_mm=0,
    )
    assert legacy.timestamp == utc("2026-01-01T06:00:00Z")


def test_weather_forecast_inference_and_validation():
    forecast = weather(
        forecast_for=utc("2026-01-02T06:00:00Z"),
        source=WeatherSource.WEATHER_API,
    )
    assert forecast.is_forecast is True
    assert forecast.is_observed is False
    with pytest.raises(ValidationError, match="forecast_for"):
        weather(forecast_for=utc("2025-12-31T06:00:00Z"))
    with pytest.raises(ValidationError):
        weather(rainfall_probability_percent=101)


def test_weather_requires_at_least_one_measurement():
    with pytest.raises(ValidationError, match="at least one"):
        WeatherObservation(
            pan_id="PAN-01",
            timestamp=utc("2026-01-01T06:00:00Z"),
        )


def test_weather_keeps_a_zero_forecast_distinct_from_a_missing_one():
    assert weather(rainfall_mm=0.0).rainfall_mm == 0.0
    blank = weather(rainfall_mm=None, ambient_temperature_c=30.0)
    assert blank.rainfall_mm is None


def test_weather_rejects_implausible_measurements():
    with pytest.raises(ValidationError):
        weather(rainfall_mm=-0.1)
    with pytest.raises(ValidationError):
        weather(rainfall_mm=5_000)
    with pytest.raises(ValidationError):
        weather(ambient_temperature_c=200)


def test_lifecycle_and_model_validation():
    with pytest.raises(ValidationError):
        SimulationRun(pan_id="PAN-01", status=SimulationStatus.SUCCEEDED)
    with pytest.raises(ValidationError):
        SimulationRun(pan_id="PAN-01", is_simulated=False)
    with pytest.raises(ValidationError):
        MLPrediction(
            pan_id="PAN-01",
            model_version="1.0.0",
            target=MlTarget.SALINITY_G_L,
            probability=1.1,
        )


def test_explanation_recommendation_decision_and_outcome_models():
    explanation = Explanation(
        prediction_id="pred-1",
        pan_id="PAN-01",
        model_version="1.0.0",
    )
    recommendation = Recommendation(
        pan_id="PAN-01",
        priority="high",
        title="Review inflow",
        body="Inspect the inlet.",
        rationale="The configured depth threshold was exceeded.",
    )
    decision = OperatorDecision(
        pan_id="PAN-01",
        recommendation_id="rec-1",
        decision=OperatorDecisionType.ACCEPTED,
        decided_at=utc("2026-01-02T00:00:00Z"),
    )
    outcome = ActualOutcome(
        pan_id="PAN-01",
        event_id="event-1",
        actual_rainfall_mm=3.2,
        harvest_outcome=HarvestOutcome.PARTIAL,
    )
    assert explanation.attributions == []
    assert recommendation.priority == "high"
    assert decision.timestamp == utc("2026-01-02T00:00:00Z")
    assert outcome.harvest_outcome == HarvestOutcome.PARTIAL


def test_actual_outcome_rejects_negative_measurements():
    with pytest.raises(ValidationError):
        ActualOutcome(pan_id="PAN-01", event_id="event-1", post_event_depth_cm=-1)
    with pytest.raises(ValidationError):
        ActualOutcome(pan_id="PAN-01", event_id="event-1", actual_rainfall_mm=float("inf"))


def test_model_version_supports_legacy_target_spelling_but_normalizes_storage():
    version = ModelVersion(target="salinity", version="v1.2.3")
    # A bare field name is read as the research target it can only have meant.
    assert version.target == ResearchTarget.POST_RAIN_SALINITY_G_L
    assert version.metrics == {}
    # Untouched by the alias handling, a version registers as experimental rather
    # than in any of the later lifecycle states.
    assert version.status is ModelStatus.EXPERIMENTAL
    with pytest.raises(ValidationError):
        ModelVersion(target="salinity", version="not-semver")


def test_model_version_names_a_research_target_rather_than_a_bare_field():
    """A model says which salinity, taken when -- not merely "salinity"."""
    for target in ResearchTarget:
        version = ModelVersion(target=target, version="v1.0.0")
        assert version.target == target

    # A bare field name is no longer a storable target: "water_depth_cm" says
    # nothing about when the depth was measured, which is the whole content of
    # the target.
    with pytest.raises(ValidationError):
        ModelVersion(target="water_depth_cm", version="v1.0.0")


def test_model_version_carries_the_provenance_a_metric_cannot_be_read_without():
    """A metric without its provenance is a number that cannot be re-derived."""
    version = ModelVersion(
        target="operational_risk",
        version="v1.0.0",
        algorithm="logistic_regression",
        random_seed=20260926,
        dataset_id="ds-1",
        dataset_content_hash="sha256:" + "a" * 64,
        dataset_version=2,
        feature_names=["current_salinity_g_l", "water_depth_cm"],
        split_strategy="chronological",
        split_detail={"train_rows": 80, "test_rows": 20},
        library_versions={"scikit-learn": "1.9.1"},
        metrics={"f1_macro": 0.8},
    )
    assert version.random_seed == 20260926
    assert version.feature_names == ["current_salinity_g_l", "water_depth_cm"]
    assert version.split_strategy == "chronological"
    assert version.library_versions == {"scikit-learn": "1.9.1"}
    # A seed is a plain integer; a model that took one cannot claim it was absent.
    with pytest.raises(ValidationError):
        ModelVersion(target="operational_risk", version="v1.0.0", random_seed=-1)
