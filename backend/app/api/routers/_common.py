"""Helper used by all module routers to expose honest placeholder responses."""

from app.schemas.common import PlaceholderResponse


def placeholder_response(
    module_name: str,
    module_number: int,
    description: str,
    planned_endpoints: list[str],
) -> PlaceholderResponse:
    """Describe a not-yet-implemented module without fabricating data."""
    return PlaceholderResponse(
        module=module_name,
        module_number=module_number,
        description=description,
        planned_endpoints=planned_endpoints,
    )