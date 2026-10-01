"""All module routers registered here."""

from fastapi import APIRouter

from app.api.routers import (
    auth,
    dashboard,
    datasets,
    decisions,
    digital_twin,
    evaluation,
    experiments,
    explain,
    export,
    feedback,
    field_validation,
    ml,
    model_registry,
    observations,
    outcomes,
    pans,
    risk,
    simulator,
    validation,
    weather,
)

all_routers: list[APIRouter] = [
    auth.router,
    pans.router,
    observations.router,
    weather.router,
    digital_twin.router,
    simulator.router,
    datasets.router,
    ml.router,
    evaluation.router,
    risk.router,
    explain.router,
    decisions.router,
    dashboard.router,
    feedback.router,
    outcomes.router,
    validation.router,
    model_registry.router,
    export.router,
    experiments.router,
    field_validation.router,
]