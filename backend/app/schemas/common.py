from typing import Annotated

from pydantic import BaseModel, Field

#: A float that cannot be NaN or +/-Infinity. Scientific inputs and stored
#: measurements are facts a computation will consume, and a non-finite value is
#: never a measurement: it either poisons averaging or silently drops out of
#: comparisons. Server-side, every unbounded numeric field the client supplies
#: is declared against this so a NaN cannot slip through JSON's permissive
#: number parsing.
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class SystemInfo(BaseModel):
    name: str
    version: str
    environment: str


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    database: str
    database_health: dict = Field(default_factory=dict)
    indexes_ready: bool = False
    python: str


class PlaceholderResponse(BaseModel):
    module: str
    module_number: int
    status: str = "not_implemented"
    description: str
    planned_endpoints: list[str]
