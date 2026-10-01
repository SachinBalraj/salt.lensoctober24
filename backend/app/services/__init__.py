"""Domain service layer.

Each module owns one part of the domain and keeps its rules in one place, so
the HTTP layer stays a thin translation of requests into service calls.

Implemented modules:

- :mod:`app.services.salt_pans` - pan registration and lookup.
- :mod:`app.services.observations` - manual pan observations and latest state.
- :mod:`app.services.weather` - weather records, manual and provider-sourced.
- :mod:`app.services.weather_providers` - the seam a real weather API plugs into.

Modules that are not implemented yet still raise ``NotImplementedError`` so
that no behaviour is ever faked.
"""