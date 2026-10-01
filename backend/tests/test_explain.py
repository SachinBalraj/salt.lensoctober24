"""Module 11 - SHAP explainability, over HTTP, of a real model and real rows.

Everything here is real. The same lifecycle Module 10's suite builds is built
here: a rule written through the API, a dataset assembled from stored records, a
model fitted by Module 08, evaluated by Module 09, deployed by Module 17, a twin
state from Module 05, and an artifact loaded from disk. Nothing stubs the
estimator, the explainer or the snapshot, because each of those is exactly what a
reader of an attribution is relying on.

The properties under test, and the test that fails if each is quietly dropped:

**The attributions are the shap library's own.**
    :func:`test_the_contributions_are_the_shap_library_s_own_values` recomputes
    the values here, from the registered artifact, with shap called directly, and
    compares them to what the endpoint returned. A hardcoded list, a correlation
    coefficient, a normalised weight, or a value scaled to look like a
    contribution all fail it -- and so does any change in the row being explained,
    because the recomputation reads the same stored snapshot.

**The attributions account for the number they explain.**
    :func:`test_the_contributions_add_back_up_to_the_probability` checks that the
    base value plus the contributions is the stored probability, and that the
    residual the response reports is the one that was measured rather than zero
    written in.

**Every declared feature is accounted for.**
    :func:`test_every_declared_feature_gets_an_attribution` asserts one
    attribution per declared feature, including one whose contribution is exactly
    zero, and :func:`test_the_two_factor_lists_are_the_actual_signs` that the
    "increases" and "decreases" lists are the positive and negative contributions
    and nothing else.

**The three quantities are kept apart.**
    :func:`test_the_three_quantities_are_reported_apart` checks the model
    probability, the SHAP contributions and the physical simulation result are
    three labelled fields with three different units, and that the mass-balance
    figure appears as an *input* the model read rather than as an output or a
    contribution.

**Imputed values are not dressed up as measurements.**
    :func:`test_an_imputed_feature_is_marked_and_reports_no_value` asserts a
    feature the twin could not supply carries no ``feature_value`` at all, rather
    than the median the preprocessor put there.

**Nothing is invented when it cannot be computed.**
    A class-only model (:func:`test_a_class_only_model_reports_explainability_unavailable`),
    an artifact that is not on this host
    (:func:`test_a_missing_artifact_reports_unavailable_and_stores_nothing`), a
    snapshot that no longer matches its own hash
    (:func:`test_a_snapshot_that_no_longer_matches_its_hash_is_refused`) and a
    failing explainer (:func:`test_a_prediction_survives_a_failed_explanation`)
    each produce a named reason and no attributions. The last one asserts the
    model's own class and probability are returned untouched.

**A global summary is a summary of real rows, and is deterministic.**
    :func:`test_the_global_summary_ranks_the_features_it_explained` and
    :func:`test_the_global_summary_is_deterministic` cover the held-out sample and
    the repeatability that makes a stored number citable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier

from app.api.deps import get_db, get_optional_db
from app.api.routers.datasets import get_dataset_service, get_dataset_status_service
from app.api.routers.digital_twin import (
    get_digital_twin_service,
    get_digital_twin_status_service,
)
from app.api.routers.evaluation import get_evaluation_service
from app.api.routers.explain import (
    get_explanation_service,
    get_explanation_status_service,
)
from app.api.routers.ml import get_ml_service
from app.api.routers.model_registry import get_deployment_service
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.risk import get_prediction_service, get_risk_definition_service
from app.api.routers.weather import get_weather_service, get_weather_status_service
from app.db.collections import EXPLANATIONS
from app.db.repository import MongoRepository
from app.main import app
from app.models import Explanation
from app.models.explanation import (
    KIND_GLOBAL,
    KIND_LOCAL,
    SOURCE_IMPUTED,
    SOURCE_PHYSICS,
    SOURCE_TWIN,
)
from app.services.datasets import DatasetService
from app.services.deployment import DeploymentService
from app.services.digital_twin import DigitalTwinService
from app.services.evaluation import EvaluationService
from app.services.explain import (
    ADDITIVITY_TOLERANCE,
    PERMUTATION_MAX_EVALS,
)
from app.services.explain import ShapExplanationService as ExplainService
from app.services.ml import ARTIFACT_DIRECTORY, MLService
from app.services.observations import ObservationService
from app.services.prediction import OperationalPredictionService
from app.services.risk_definitions import RiskDefinitionService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

#: The lifecycle is Module 10's, reused rather than rebuilt: the explanation is a
#: property of a real prediction, and a fixture that grew its own way of producing
#: one would test a different system.
from tests.test_risk_prediction import (
    TWIN_FEATURES,
    _ClassOnly,
    _database_of,
    build_risk_dataset,
    deploy_model,
    predict,
    read,
    refresh_twin,
    seed,
    stored_model,
    stored_predictions,
)

#: The physics column the twin's mass balance fills, used to check that a
#: simulation result is reported as an input rather than as a contribution.
PHYSICS_FEATURE = "physics_predicted_post_rain_salinity_g_l"

#: The estimator types whose attributions are recomputed with the exact tree
#: explainer; anything else here is recomputed by sampling. Kept as its own list so
#: a candidate Module 8 adds is not silently left unverified.
_TREE_TYPES = {
    "sklearn.tree._classes.DecisionTreeClassifier": True,
    "sklearn.ensemble._forest.RandomForestClassifier": True,
    "sklearn.ensemble._gb.GradientBoostingClassifier": True,
}

#: Features the twin cannot supply, added to a model so the imputation path is
#: exercised against a real prediction rather than described in prose.
WITH_AN_IMPUTED_FEATURE = [*TWIN_FEATURES, "recent_rainfall_mm"]


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def artifacts(tmp_path: Path) -> Path:
    return tmp_path / "models"


def _overrides(database: FakeDatabase, artifacts: Path) -> dict[str, Any]:
    """Every service the lifecycle needs, pointed at the fake database.

    Module 11 is wired in beside the rest, and the prediction service is given the
    same explainer the explain routes use -- with the same artifact directory -- so
    an explanation stored during a prediction and one stored by asking for it come
    from one implementation reading one file.
    """
    explainer = lambda: ExplainService(database, artifact_directory=artifacts)
    return {
        get_db: lambda: database,
        get_optional_db: lambda: database,
        get_salt_pan_service: lambda: SaltPanService(database),
        get_observation_service: lambda: ObservationService(database),
        get_observation_status_service: lambda: ObservationService(database),
        get_weather_service: lambda: WeatherService(database),
        get_weather_status_service: lambda: WeatherService(database),
        get_dataset_service: lambda: DatasetService(database),
        get_dataset_status_service: lambda: DatasetService(database),
        get_risk_definition_service: lambda: RiskDefinitionService(database),
        get_ml_service: lambda: MLService(database, artifact_directory=artifacts),
        get_evaluation_service: lambda: EvaluationService(
            database, artifact_directory=artifacts
        ),
        get_deployment_service: lambda: DeploymentService(database),
        get_digital_twin_service: lambda: DigitalTwinService(database),
        get_digital_twin_status_service: lambda: DigitalTwinService(database),
        get_explanation_service: explainer,
        get_explanation_status_service: explainer,
        get_prediction_service: lambda: OperationalPredictionService(
            database,
            explainer=explainer(),
            artifact_directory=artifacts,
        ),
    }


@pytest.fixture()
def client(database: FakeDatabase, artifacts: Path) -> Any:
    app.dependency_overrides.update(_overrides(database, artifacts))
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def live(client: TestClient, database: FakeDatabase, artifacts: Path) -> dict[str, Any]:
    """A deployed model, a twin, and nothing explained yet."""
    seed(database)
    dataset = build_risk_dataset(client, TWIN_FEATURES)
    model = deploy_model(client, dataset["dataset_id"])
    twin = refresh_twin(client)
    return {"model": model, "dataset": dataset, "twin": twin, "artifacts": artifacts}


def explain(client: TestClient, prediction_id: str) -> Any:
    return client.post(f"/api/explain/predictions/{prediction_id}")


def read_explanation(client: TestClient, prediction_id: str) -> Any:
    return client.get(f"/api/explain/predictions/{prediction_id}")


def importance(client: TestClient, model: dict[str, Any], **params: Any) -> Any:
    return client.get(
        f"/api/explain/models/{model['model_id']}/versions/{model['version']}/importance",
        params=params or None,
    )


def stored_explanations(database: FakeDatabase) -> list[Any]:
    return read(MongoRepository(database, EXPLANATIONS, Explanation), {})


def stored_local(database: FakeDatabase) -> list[Any]:
    return [
        item
        for item in stored_explanations(database)
        if getattr(item, "kind", None) == KIND_LOCAL
    ]


def artifact_for(model: dict[str, Any]) -> dict[str, Any]:
    path = Path(model["artifact_path"])
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    return joblib.load(path)


def independent_shap(
    model: dict[str, Any], features: dict[str, Any], target_class: str
) -> tuple[dict[str, float], float]:
    """SHAP values for one row, computed here from the registered artifact.

    Deliberately independent of :mod:`app.services.explain`: it loads the file, runs
    the fitted preprocessor, calls shap itself, and maps the transformed columns
    back to features by matching the pipeline's own output names. If the endpoint's
    numbers came from anywhere else -- a stored table, a coefficient, a
    correlation -- the two disagree, and the comparison is tight enough that a
    merely similar value does not pass.

    Both explainer paths are recomputed, because which one a run selects depends on
    which candidate won the held-out score, and a suite that only ever checked the
    one its data happened to produce would leave the other unverified. The two
    imports it needs beyond shap are the evaluation budget and the tolerance, which
    are declared settings rather than results: the budget has to match for two
    sampled runs to be comparable at all, and a change to it should show up here as
    a difference rather than pass unnoticed.
    """
    import pandas as pd
    import shap

    artifact = artifact_for(model)
    names = list(model["feature_names"])
    frame = pd.DataFrame.from_records([{name: features.get(name) for name in names}])
    transformed = np.asarray(
        artifact["preprocessing"].transform(frame.reindex(columns=names)), dtype=float
    )
    classes = [str(label) for label in artifact["classes"]]
    class_index = classes.index(target_class)
    background = np.asarray(artifact["shap_reference"]["background"], dtype=float)
    estimator = artifact["estimator"]

    if _TREE_TYPES.get(f"{type(estimator).__module__}.{type(estimator).__qualname__}"):
        explainer = shap.TreeExplainer(
            estimator, data=background, model_output="probability"
        )
        values = np.asarray(explainer.shap_values(transformed), dtype=float)
        base = np.atleast_1d(np.asarray(explainer.expected_value, dtype=float))
        if values.ndim == 3:  # (rows, transformed columns, classes)
            columns, base_value = values[0, :, class_index], float(base[class_index])
        elif base.size > 1:  # one block, one base value per class
            columns, base_value = values[0, :], float(base[class_index])
        else:  # a binary tree explains the positive class in a single block
            columns, base_value = values[0, :], float(base[0])
    else:
        def probability(values: np.ndarray) -> np.ndarray:
            return estimator.predict_proba(values)[:, class_index]

        budget = max(PERMUTATION_MAX_EVALS, 2 * background.shape[1] + 1)
        explainer = shap.Explainer(
            probability, background, algorithm="permutation", max_evals=budget, seed=0
        )
        result = explainer(transformed)
        values = np.asarray(result.values, dtype=float)
        base = np.atleast_1d(np.asarray(result.base_values, dtype=float)).ravel()
        columns, base_value = values[0, :], float(base[0])

    # The preprocessor is a one-hot encoder over a few categorical features, so one
    # declared feature can arrive as several transformed columns. Matching the tail
    # of the output name, longest feature name first so `depth_change_cm` is not
    # swallowed by `depth_change_cm_x`, is the mapping the pipeline's own naming
    # makes possible.
    produced = [str(n) for n in artifact["preprocessing"].get_feature_names_out()]
    grouped: dict[str, float] = {name: 0.0 for name in names}
    for index, column in enumerate(produced):
        tail = column.split("__", 1)[1] if "__" in column else column
        for feature in sorted(names, key=len, reverse=True):
            if tail == feature or tail.startswith(f"{feature}_"):
                grouped[feature] += float(columns[index])
                break
    return grouped, base_value


# -- a prediction arrives explained ---------------------------------------


def test_a_prediction_arrives_with_its_explanation_stored(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """Every operational prediction is attributed as it is made.

    Not "a prediction can be explained on request": the response carries the status
    and the id, so the attribution exists for every prediction without anyone
    remembering to ask.
    """
    body = predict(client).json()
    assert body["explanation_status"] == "available", body["explanation_refusal_detail"]
    assert body["explanation_id"]
    assert body["explanation_refusal"] is None

    stored = stored_local(database)
    assert len(stored) == 1
    record = stored[0]
    assert record.explanation_id == body["explanation_id"]
    # Linked to the prediction and to the model version, which is the whole point
    # of storing it: an attribution nobody can trace is not evidence.
    assert record.prediction_id == body["prediction_id"]
    assert record.model_id == body["model_id"]
    assert record.model_version == body["model_version"]
    assert record.input_snapshot_id == body["input_snapshot_id"]
    # A simulated attribution would be indistinguishable from a computed one on a
    # screen, so the flag is asserted rather than trusted.
    assert record.is_simulated is False


def test_the_explanation_reports_the_method_that_produced_it(
    client: TestClient, live: dict[str, Any]
) -> None:
    """Which explainer ran, whether it computed or estimated, and against what.

    An approximation presented as an exact attribution is the failure this module
    exists to avoid, so all three travel with the numbers.
    """
    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    assert explanation["method"] in {
        "tree_explainer",
        "permutation_explainer",
    }
    assert isinstance(explanation["approximate"], bool)
    assert explanation["approximate"] is (explanation["method"] != "tree_explainer")
    assert explanation["shap_version"] != "unrecorded"
    assert explanation["background"]["source"] == "training_split_of_the_fitted_dataset"
    assert explanation["background"]["rows"] > 0
    assert explanation["background"]["width"] == len(live["model"]["feature_names"])


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(DecisionTreeClassifier(random_state=0), id="decision_tree"),
        pytest.param(RandomForestClassifier(n_estimators=8, random_state=0), id="forest"),
        pytest.param(GradientBoostingClassifier(random_state=0), id="boosting"),
    ],
)
def test_every_tree_ensemble_is_explained_exactly(
    client: TestClient,
    live: dict[str, Any],
    database: FakeDatabase,
    build: Any,
) -> None:
    """The exact path is checked for each tree the project fits, not just one.

    Which candidate wins the held-out score depends on the data, so on this dataset
    the sampled explainer wins and the exact one is never reached. Leaving it
    unexercised would mean the one path that computes Shapley values rather than
    estimating them is the one path nobody has verified.

    The estimator is replaced with a real model of the type under test, fitted on the
    transformed rows already inside the artifact -- the preprocessor, the classes and
    the feature names are the pipeline's own -- so the explanation computed is of a
    genuine fitted tree on genuine data. What is being varied is which explainer
    runs, not whether there is a model to explain.
    """
    model = stored_model(database, live["model"]["model_id"])
    path = Path(model.artifact_path)
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    artifact = joblib.load(path)
    reference = artifact["shap_reference"]
    rows = np.vstack([reference["background"], reference["holdout"]])
    labels = np.arange(len(rows)) % len(artifact["classes"])
    estimator = build
    estimator.fit(rows, labels)
    artifact["estimator"] = estimator
    joblib.dump(artifact, path)

    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    assert explanation["method"] == "tree_explainer"
    # Computed, not estimated: the distinction the whole module turns on.
    assert explanation["approximate"] is False

    total = explanation["base_value"] + sum(
        item["shap_value"] for item in explanation["attributions"]
    )
    assert total == pytest.approx(body["probability"], abs=ADDITIVITY_TOLERANCE)
    # The exact explainer's residual is far below the tolerance rather than merely
    # inside it, which is what "exact" is worth on a screen.
    assert explanation["attribution_error"] < 1e-8

    stored = stored_predictions(database)[0]
    expected, base = independent_shap(
        live["model"], stored.input_snapshot["features"], body["risk_class"]
    )
    reported = {item["feature"]: item["shap_value"] for item in explanation["attributions"]}
    for feature, value in expected.items():
        assert reported[feature] == pytest.approx(value, rel=0, abs=1e-12), feature
    assert explanation["base_value"] == pytest.approx(base, rel=0, abs=1e-12)


# -- the numbers are real -------------------------------------------------


def test_the_contributions_are_the_shap_library_s_own_values(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """Recomputed here from the registered artifact, and equal.

    This is the test a fabricated attribution fails. The recomputation loads the
    file, calls shap on it, and maps the transformed columns back to features with
    its own mapping, so anything this module made up -- a normalised weight, a
    coefficient, a correlation, a plausible constant per feature -- disagrees.
    """
    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    stored = stored_predictions(database)[0]
    expected, base = independent_shap(
        live["model"], stored.input_snapshot["features"], body["risk_class"]
    )

    reported = {item["feature"]: item["shap_value"] for item in explanation["attributions"]}
    assert set(reported) == set(expected)
    for feature, value in expected.items():
        assert reported[feature] == pytest.approx(value, rel=0, abs=1e-12), feature
    assert explanation["base_value"] == pytest.approx(base, rel=0, abs=1e-12)


def test_the_contributions_add_back_up_to_the_probability(
    client: TestClient, live: dict[str, Any]
) -> None:
    """Base value plus contributions is the model's own probability.

    The property that makes a SHAP value checkable: additivity is not a hope, it
    is arithmetic, and the residual is reported rather than rounded away.
    """
    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    total = explanation["base_value"] + sum(
        item["shap_value"] for item in explanation["attributions"]
    )
    assert total == pytest.approx(body["probability"], abs=ADDITIVITY_TOLERANCE)
    assert explanation["attribution_error"] == pytest.approx(
        abs(body["probability"] - total), abs=1e-12
    )
    assert explanation["attribution_error"] <= ADDITIVITY_TOLERANCE


def test_every_declared_feature_gets_an_attribution(
    client: TestClient, live: dict[str, Any]
) -> None:
    """One row per declared feature, in the model's own order.

    Including a feature whose contribution is exactly zero. A list that dropped it
    would read as "this feature did not matter", when the truth is "this feature was
    not looked at" -- and those are different sentences.
    """
    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    declared = list(live["model"]["feature_names"])
    assert [item["feature"] for item in explanation["attributions"]] == declared
    # A tree assigns several features a zero contribution on a row where they do
    # not change the path, and those must still be present.
    assert all(item["shap_value"] == 0.0 for item in explanation["attributions"]) is False
    for item in explanation["attributions"]:
        assert item["direction"] in {"increases_risk", "decreases_risk", "neutral"}
        assert item["source_kind"] in {SOURCE_TWIN, SOURCE_PHYSICS, SOURCE_IMPUTED, "unknown"}


def test_the_two_factor_lists_are_the_actual_signs(
    client: TestClient, live: dict[str, Any]
) -> None:
    """"Increasing" means a positive contribution, and nothing else.

    The display requirement -- what pushed the risk up, what pulled it down -- is
    only meaningful if the two lists are the sign of the number rather than a
    ranking of absolute size, which would put the same features in both.
    """
    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    increasing = explanation["top_increasing"]
    decreasing = explanation["top_decreasing"]

    assert increasing, "a real model on a real row moves the probability somehow"
    assert all(item["shap_value"] > 0 for item in increasing)
    assert all(item["shap_value"] < 0 for item in decreasing)
    assert [item["shap_value"] for item in increasing] == sorted(
        (item["shap_value"] for item in increasing), reverse=True
    )
    assert [item["shap_value"] for item in decreasing] == sorted(
        item["shap_value"] for item in decreasing
    )
    # Disjoint, and together they account for every contribution that is not zero.
    assert not {item["feature"] for item in increasing} & {
        item["feature"] for item in decreasing
    }
    ranked = {(i["feature"]) for i in increasing} | {d["feature"] for d in decreasing}
    non_zero = {
        item["feature"] for item in explanation["attributions"] if item["shap_value"]
    }
    assert non_zero <= ranked
    assert len(increasing) <= 5 and len(decreasing) <= 5


# -- the three quantities are kept apart -----------------------------------


def test_the_three_quantities_are_reported_apart(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """Probability, contribution and simulation result: three labelled fields.

    A reader who saw one number called "risk" and another called "contribution"
    with no units would have to guess whether they were comparable. They are not: a
    probability is a level, a contribution is a change, and the mass-balance figure
    is an input the model read.
    """
    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    quantities = explanation["quantities"]

    assert quantities["model_probability"]["value"] == pytest.approx(
        body["probability"], abs=0
    )
    assert quantities["model_probability"]["unit"] == "probability"
    assert quantities["model_probability"]["label"] == "Model probability"

    contribution = quantities["shap_contribution"]
    assert contribution["unit"] == "probability points"
    assert contribution["explained_output"] == "probability"
    assert contribution["target_class"] == body["risk_class"]
    assert contribution["base_value"] == pytest.approx(explanation["base_value"])

    simulation = quantities["physical_simulation"]
    assert simulation["unit"] != quantities["model_probability"]["unit"]
    assert simulation["label"] == "Physical simulation result"
    # The physics figure is the one the mass balance produced for this pan, held in
    # the row the model read -- and it is also carried as an attribution of a
    # different kind, which is exactly the distinction being drawn.
    stored = stored_predictions(database)[0]
    expected = stored.input_snapshot["features"][PHYSICS_FEATURE]
    assert simulation["results"][PHYSICS_FEATURE] == pytest.approx(float(expected))
    physics = next(
        item for item in explanation["attributions"] if item["feature"] == PHYSICS_FEATURE
    )
    assert physics["source_kind"] == SOURCE_PHYSICS
    assert physics["shap_value"] != pytest.approx(float(expected))


def test_a_simulated_result_is_not_a_contribution(
    client: TestClient, live: dict[str, Any]
) -> None:
    """The mass-balance number is not presented as a shift the model made.

    The failure this guards is a specific one: a physics figure and a SHAP value
    both being "a number about salinity", and a reader assuming the second is the
    first. Their units differ, and the response says so.
    """
    body = predict(client).json()
    explanation = read_explanation(client, body["prediction_id"]).json()
    simulation = explanation["quantities"]["physical_simulation"]["results"]
    contributions = {
        item["feature"]: item["shap_value"] for item in explanation["attributions"]
    }
    for name, value in simulation.items():
        assert contributions[name] != pytest.approx(float(value)), name
        assert "mass balance" in explanation["quantities"]["physical_simulation"]["note"]


# -- imputed values --------------------------------------------------------


def test_an_imputed_feature_is_marked_and_reports_no_value(
    client: TestClient, database: FakeDatabase
) -> None:
    """A contribution over an imputed column describes the imputer, and says so.

    The value is not reported at all. The preprocessor put the training median
    there, which is a statistic of the training rows and not a measurement of this
    pan, and printing it in the feature's own column would read as one.
    """
    seed(database)
    dataset = build_risk_dataset(client, WITH_AN_IMPUTED_FEATURE)
    deploy_model(client, dataset["dataset_id"])
    refresh_twin(client)

    body = predict(client).json()
    assert "recent_rainfall_mm" in body["features_absent"]
    explanation = read_explanation(client, body["prediction_id"]).json()
    imputed = next(
        item for item in explanation["attributions"] if item["feature"] == "recent_rainfall_mm"
    )
    assert imputed["source_kind"] == SOURCE_IMPUTED
    assert imputed["imputed"] is True
    assert imputed["feature_value"] is None
    assert imputed["source"] is None
    # Only the column this model added is missing: the other seven gaps are features
    # this model never asked for, and a feature it did not declare cannot be
    # imputed into the row it is scored on.
    assert body["features_absent"] == ["recent_rainfall_mm"]


# -- when SHAP cannot be computed ------------------------------------------


def test_a_class_only_model_reports_explainability_unavailable(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """No probability means no probability-space attribution, and none is faked.

    The tempting alternative is to attribute the decision margin: SHAP will happily
    decompose it, it is a real number, and it would fill the gap. It would also
    answer a question about a quantity no operator was ever shown, next to a
    probability that does not exist. The refusal says which quantity is missing.
    """
    model = stored_model(database, live["model"]["model_id"])
    path = Path(model.artifact_path)
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    artifact = joblib.load(path)
    artifact["estimator"] = _ClassOnly("act now")
    joblib.dump(artifact, path)

    body = predict(client).json()
    assert body["probability"] is None
    assert body["explanation_status"] == "unavailable"
    assert body["explanation_refusal"] == "model_reports_no_probability"
    assert "predict_proba" in body["explanation_refusal_detail"]
    assert body["explanation_id"] is None
    assert stored_local(database) == []

    response = explain(client, body["prediction_id"])
    assert response.status_code == 409
    assert response.json()["refusal"] == "model_reports_no_probability"


def test_a_missing_artifact_reports_unavailable_and_stores_nothing(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """A model whose file is not on this host cannot be explained.

    The alternative -- refitting a stand-in to explain it -- would attribute a
    stored number to a model that never produced it.
    """
    body = predict(client).json()
    model = stored_model(database, live["model"]["model_id"])
    path = Path(model.artifact_path)
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    path.unlink()

    before = len(stored_local(database))
    response = explain(client, body["prediction_id"])
    assert response.status_code == 409
    refusal = response.json()
    assert refusal["refusal"] == "artifact_unavailable"
    assert model.model_id in refusal["detail"]
    # A refusal stores nothing. The prediction explained itself before the file
    # went, so the count is held rather than emptied.
    assert len(stored_local(database)) == before


def test_a_prediction_without_a_reference_sample_is_refused(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """An artifact carrying no reference distribution is not explained against one.

    A Shapley value is defined against a reference distribution. Choosing one at
    explain time would silently decide every number in the response.
    """
    body = predict(client).json()
    _strip_reference(database, live["model"])
    response = explain(client, body["prediction_id"])
    assert response.status_code == 409
    refusal = response.json()
    assert refusal["refusal"] == "no_reference_distribution"
    assert "reference" in refusal["detail"].lower()


def test_a_snapshot_that_no_longer_matches_its_hash_is_refused(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """The row explained must be the row the number came from.

    A snapshot edited after the prediction was written no longer hashes to the id
    recorded beside it. Attributing the model's output to that row would be a claim
    about a row nobody can check, so it is refused rather than explained.
    """
    body = predict(client).json()
    _tamper_snapshot(database, body["prediction_id"])
    response = explain(client, body["prediction_id"])
    assert response.status_code == 409
    refusal = response.json()
    assert refusal["refusal"] == "input_snapshot_mismatch"
    assert body["input_snapshot_id"] in refusal["detail"]


def test_a_prediction_survives_a_failed_explanation(
    client: TestClient, live: dict[str, Any], artifacts: Path
) -> None:
    """A SHAP failure must not cost the caller the model's own answer.

    The probability was computed before the explainer was called; it is the answer,
    and a prediction that refuses because its *explanation* failed would be a worse
    system than one that explains what it can.
    """
    broken = _ExplainsNothing()
    app.dependency_overrides[get_prediction_service] = lambda: (
        OperationalPredictionService(
            _database_of(client),
            explainer=broken,
            artifact_directory=artifacts,
        )
    )
    body = predict(client).json()
    assert body["risk_class"] in {"watch", "act now"}
    assert 0.0 <= body["probability"] <= 1.0
    assert body["explanation_status"] == "unavailable"
    assert body["explanation_refusal"] == "shap_computation_failed"
    assert "unaffected" in body["explanation_refusal_detail"]
    assert body["explanation_id"] is None


def test_reading_an_explanation_that_was_never_asked_for(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """"Not stored yet" is a different answer from "cannot be computed".

    A client that cannot tell them apart retries a request that cannot succeed, or
    shows an error where a button would have worked.
    """
    predict(client)
    _drop_explanations(database)
    response = read_explanation(client, _a_stored_prediction_id(database))
    assert response.status_code == 409
    refusal = response.json()
    assert refusal["refusal"] == "explanation_not_stored"
    assert "POST" in refusal["detail"]


def test_an_unknown_prediction_is_a_404(
    client: TestClient, live: dict[str, Any]
) -> None:
    """Asking about a prediction that does not exist is not a conflict."""
    response = explain(client, "pred-does-not-exist")
    assert response.status_code == 404
    assert response.json()["refusal"] == "no_such_prediction"


def test_an_unknown_model_version_is_a_404(
    client: TestClient, live: dict[str, Any]
) -> None:
    response = importance(client, {**live["model"], "version": "v9.9.9"})
    assert response.status_code == 404
    assert response.json()["refusal"] == "no_such_model_version"


# -- one explanation per prediction ----------------------------------------


def test_asking_twice_refreshes_one_explanation(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """The arithmetic is deterministic, so a second row would be a second answer.

    Nothing would say which supersedes which, so a re-run replaces one row and says
    so with a 200 rather than a 201.
    """
    body = predict(client).json()
    # The prediction explained itself, so a stored row is already there.
    assert len(stored_local(database)) == 1

    again = explain(client, body["prediction_id"])
    assert again.status_code == 200
    assert len(stored_local(database)) == 1
    # Deterministic arithmetic: the refresh is the same answer, not a new one.
    assert again.json()["attributions"] == read_explanation(
        client, body["prediction_id"]
    ).json()["attributions"]

    # A prediction with no stored explanation yet -- an older row, or one written
    # when the artifact was missing -- takes the insert path, and only that.
    _drop_explanations(database)
    assert explain(client, body["prediction_id"]).status_code == 201
    assert len(stored_local(database)) == 1


def test_the_explanation_is_readable_afterwards(
    client: TestClient, live: dict[str, Any]
) -> None:
    """What was stored is what is read back, values and provenance included."""
    body = predict(client).json()
    stored = read_explanation(client, body["prediction_id"]).json()
    assert stored["prediction_id"] == body["prediction_id"]
    assert stored["input_snapshot_id"] == body["input_snapshot_id"]
    assert stored["twin_id"] == body["twin_id"]
    assert stored["model_probability"] == pytest.approx(body["probability"], abs=0)
    assert stored["kind"] == KIND_LOCAL


# -- global ----------------------------------------------------------------


def test_the_global_summary_ranks_the_features_it_explained(
    client: TestClient, live: dict[str, Any]
) -> None:
    """Mean absolute contribution per feature, over real held-out rows.

    Held-out, not training: the rows Module 09 scored the model on, so the ranking
    describes the model on data it has not seen rather than on rows it memorised.
    """
    response = importance(client, live["model"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == KIND_GLOBAL
    # Asked for 25, explained what the holdout actually holds. The number reported
    # is the number explained, not the number requested.
    assert body["rows_explained"] == artifact_for(live["model"])["shap_reference"][
        "holdout_rows"
    ]
    assert body["rows_explained"] <= 25
    assert body["background"]["source"] == "held_out_split_scored_by_module_09"
    assert body["explained_output"] == "probability"
    assert body["measure"]["unit"] == "probability points"
    assert sum(body["explained_classes"].values()) == body["rows_explained"]

    features = body["features"]
    assert [item["feature"] for item in features] == sorted(
        (item["feature"] for item in features),
        key=lambda name: -next(
            f["mean_abs_shap"] for f in features if f["feature"] == name
        ),
    )
    assert [item["rank"] for item in features] == list(range(1, len(features) + 1))
    assert all(item["mean_abs_shap"] >= 0 for item in features)
    assert any(item["mean_abs_shap"] > 0 for item in features)
    assert {item["feature"] for item in features} == set(live["model"]["feature_names"])


def test_the_global_summary_is_deterministic(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """Same model, same rows, same numbers -- so a stored figure can be cited.

    And stored once: a re-run refreshes the one row rather than adding a second
    summary that nothing could tell apart from the first.
    """
    predict(client)
    first = importance(client, live["model"]).json()
    second = importance(client, live["model"]).json()
    assert first["explanation_id"] == second["explanation_id"]
    assert first["features"] == second["features"]
    summaries = [
        item for item in stored_explanations(database) if item.kind == KIND_GLOBAL
    ]
    assert len(summaries) == 1


def test_the_global_summary_honours_its_row_budget(
    client: TestClient, live: dict[str, Any]
) -> None:
    """The count asked for is the count used, and it is reported either way."""
    body = importance(client, live["model"], rows=3).json()
    assert body["rows_explained"] == 3
    assert body["background"]["rows"] == 3
    over = importance(client, live["model"], rows=5000)
    assert over.status_code == 422


def test_a_class_only_model_has_no_global_importance(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """With no probability to decompose, the summary is refused, not approximated."""
    model = stored_model(database, live["model"]["model_id"])
    path = Path(model.artifact_path)
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    artifact = joblib.load(path)
    artifact["estimator"] = _ClassOnly("act now")
    joblib.dump(artifact, path)

    response = importance(client, live["model"])
    assert response.status_code == 409
    assert response.json()["refusal"] == "model_reports_no_probability"


def test_the_stored_explanations_are_auditable_by_model(
    client: TestClient, live: dict[str, Any]
) -> None:
    """Which predictions a version answered, and how each was explained."""
    predict(client)
    response = client.get(
        f"/api/explain/models/{live['model']['model_id']}"
        f"/versions/{live['model']['version']}/explanations"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model_id"] == live["model"]["model_id"]
    assert body["count"] == 1
    entry = body["explanations"][0]
    assert entry["kind"] == KIND_LOCAL
    assert entry["method"] in {"tree_explainer", "permutation_explainer"}
    assert entry["is_simulated"] is False
    assert entry["base_value"] is not None
    assert entry["attribution_error"] <= ADDITIVITY_TOLERANCE


# -- the module says what it is -------------------------------------------


def test_the_module_status_states_the_quantity_contract(
    client: TestClient, live: dict[str, Any]
) -> None:
    """The status endpoint is the place a reader is told what is and is not SHAP."""
    before = client.get("/api/explain/").json()
    assert before["implemented"] is True
    assert before["explained"] is False
    assert before["stored_explanations"] == 0
    for phrase in ("model probability", "SHAP contribution", "physical simulation"):
        assert phrase.lower() in before["quantity_contract"].lower()
    assert "will not" in before["note"]

    predict(client)
    after = client.get("/api/explain/").json()
    assert after["explained"] is True
    assert after["stored_explanations"] == 1


# -- helpers that reach behind the API -------------------------------------


class _ExplainsNothing:
    """An explainer that cannot explain anything, for the failure test.

    Raises a plain exception rather than an
    :class:`~app.services.explain.ExplanationUnavailable`, so the prediction path's
    broad guard is exercised too: an unexpected error in Module 11 must still leave
    the model's own answer intact.
    """

    async def explain_prediction(
        self, prediction_id: str, *, stored: Any = None
    ) -> tuple[dict[str, Any], bool]:
        raise RuntimeError("the explainer is not installed on this host")


def _a_stored_prediction_id(database: FakeDatabase) -> str:
    return stored_predictions(database)[0].prediction_id


def _strip_reference(database: FakeDatabase, model: dict[str, Any]) -> None:
    path = Path(model["artifact_path"])
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    artifact = joblib.load(path)
    artifact.pop("shap_reference", None)
    joblib.dump(artifact, path)


def _tamper_snapshot(database: FakeDatabase, prediction_id: str) -> None:
    """Edit a stored snapshot so it no longer hashes to the id recorded beside it."""
    collection = database["ml_predictions"]
    for document in collection._documents:
        if document.get("prediction_id") == prediction_id:
            snapshot = dict(document["input_snapshot"])
            snapshot["features"] = dict(snapshot["features"])
            first = min(snapshot["features"])
            snapshot["features"][first] = 999999.0
            document["input_snapshot"] = snapshot
            return
    raise AssertionError(f"no stored prediction {prediction_id!r}")


def _drop_explanations(database: FakeDatabase) -> None:
    database["explanations"]._documents.clear()


def test_the_explanation_module_imports_without_a_database() -> None:
    """Import-time hygiene: no connection, no filesystem access, no estimator load.

    A module that reached for the database at import time would make every other
    module's tests depend on a database being configured.
    """
    import importlib

    module = importlib.import_module("app.services.explain")
    assert module.ADDITIVITY_TOLERANCE > 0
    assert json.dumps(module.GLOBAL_ROWS_MAX)  # a plain int, not a lazy handle


def test_a_local_explanation_must_name_its_prediction() -> None:
    """A local attribution with no prediction is refused by the model itself.

    The guard is on the model rather than only in the service, so a caller that
    builds an Explanation by hand cannot store an orphan.
    """
    with pytest.raises(ValueError, match="must name the prediction"):
        Explanation(model_version="v1.0.0", kind=KIND_LOCAL)
    orphan_free = Explanation(model_version="v1.0.0", kind=KIND_GLOBAL)
    assert orphan_free.prediction_id is None
