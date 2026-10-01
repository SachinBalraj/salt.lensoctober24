"""Module 6 - transparent what-if rainfall simulator.

Two routes, both pure arithmetic over four inputs and neither touching the
database: the mass balance needs no history to answer "if 20 mm fell on this pan,
what would the salinity be", and a route that could fail to reach Mongo would be
a route whose number cannot be trusted.

Every response carries the equations, the assumptions and the disclaimer, so the
limits travel with the result rather than living in a docstring nobody opens.
The six preset depths (0, 5, 10, 20, 50, 100 mm) are always included in a sweep:
0 mm is the "nothing happened" reference the other columns are read against, not
an absence of a scenario.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.schemas.simulation import (
    MASS_BALANCE_EQUATIONS,
    MassBalanceRequest,
    MassBalanceResponse,
    RainfallScenarioRead,
    RainfallScenariosRequest,
    RainfallScenariosResponse,
)
from app.services.mass_balance import (
    MASS_BALANCE_MODEL_NAME,
    SIMULATION_ASSUMPTIONS,
    SIMULATOR_DISCLAIMER,
    STANDARD_RAINFALL_SCENARIOS_MM,
    MassBalanceService,
    RainfallDilutionError,
)

router = APIRouter(prefix="/api/simulator", tags=["06-mass-balance"])

DESCRIPTION = (
    "Physics-based what-if rainfall simulator. Deterministic volume and salt "
    "balance, no machine learning, with the equations and the assumptions "
    "returned alongside every result."
)

#: Re-exported under the name the router's own schemas use. The string itself
#: lives in the physics service, which is what the stored runs and Module 12 quote.
MODEL_NAME = MASS_BALANCE_MODEL_NAME

#: The whole assumption list, as one payload shared by both routes.
ASSUMPTIONS = list(SIMULATION_ASSUMPTIONS)

ENDPOINTS = [
    "GET  /api/simulator/status",
    "POST /api/simulator/mass-balance",
    "POST /api/simulator/rainfall-scenarios",
]


def get_mass_balance_service() -> MassBalanceService:
    """The service, overridable in tests.

    It is constructed with no arguments because the balance is a pure function
    of its inputs: there is nothing here for a database to supply.
    """
    return MassBalanceService()


ServiceDependency = Annotated[MassBalanceService, Depends(get_mass_balance_service)]


@router.get("/status", summary="Module 6 implementation status")
def module_status() -> dict[str, Any]:
    """Report that Module 6 is implemented, and state the model up front.

    This endpoint cannot fail: it touches nothing. The assumptions and the
    disclaimer are in the body so that even a client that only polls status
    learns that these are equations rather than a learned prediction.
    """
    return {
        "module": "mass-balance-simulator",
        "module_number": 6,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
        "model": {
            "name": MODEL_NAME,
            "kind": "deterministic physics",
            "is_ml": False,
            "inputs": ["area_m2", "initial_depth_m", "initial_salinity_g_l", "rainfall_mm"],
            "database_required": False,
        },
        "standard_scenarios_mm": list(STANDARD_RAINFALL_SCENARIOS_MM),
        "disclaimer": SIMULATOR_DISCLAIMER,
        "assumptions": ASSUMPTIONS,
    }


@router.post(
    "/mass-balance",
    response_model=MassBalanceResponse,
    summary="Predict post-rain salinity for one rainfall depth",
)
def simulate_mass_balance(
    payload: MassBalanceRequest, service: ServiceDependency
) -> MassBalanceResponse:
    """Resolve a single rainfall event against a pan of starting brine.

    Raises 422 rather than returning a number when an input makes the balance
    meaningless, such as a zero area or a zero starting depth: both are typed
    digits, and either would otherwise produce a confident-looking result from a
    division by nothing.
    """
    try:
        dilution = service.evaluate(
            area_m2=payload.area_m2,
            initial_depth_m=payload.initial_depth_m,
            initial_salinity_g_l=payload.initial_salinity_g_l,
            rainfall_mm=payload.rainfall_mm,
        )
    except RainfallDilutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return MassBalanceResponse.from_dilution(dilution, assumptions=ASSUMPTIONS)


@router.post(
    "/rainfall-scenarios",
    response_model=RainfallScenariosResponse,
    summary="Sweep the preset and custom rainfall depths",
)
def simulate_rainfall_scenarios(
    payload: RainfallScenariosRequest, service: ServiceDependency
) -> RainfallScenariosResponse:
    """Resolve every rainfall depth in one sweep, for the what-if panel and chart.

    The response is ordered by rainfall ascending, which is the order in which
    the predicted salinity falls, so the chart plots the curve without needing to
    sort or infer anything.
    """
    try:
        dilutions = service.sweep(
            area_m2=payload.area_m2,
            initial_depth_m=payload.initial_depth_m,
            initial_salinity_g_l=payload.initial_salinity_g_l,
            rainfall_scenarios_mm=payload.rainfall_scenarios_mm,
        )
    except RainfallDilutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    scenarios = [
        RainfallScenarioRead.from_dilution(
            dilution, is_standard=dilution.rainfall_mm in STANDARD_RAINFALL_SCENARIOS_MM
        )
        for dilution in dilutions
    ]
    return RainfallScenariosResponse(
        area_m2=payload.area_m2,
        initial_depth_m=payload.initial_depth_m,
        initial_salinity_g_l=payload.initial_salinity_g_l,
        initial_volume_m3=dilutions[0].initial_volume_m3,
        standard_scenarios_mm=list(STANDARD_RAINFALL_SCENARIOS_MM),
        scenarios=scenarios,
        model_name=MODEL_NAME,
        disclaimer=SIMULATOR_DISCLAIMER,
        assumptions=ASSUMPTIONS,
        equations=list(MASS_BALANCE_EQUATIONS),
    )
