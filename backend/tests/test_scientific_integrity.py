import math
from datetime import datetime, timezone

from app.services.digital_twin import DigitalTwinService
from app.services.mass_balance import MassBalanceService, simulate_rainfall_dilution


def test_mass_balance_known_values():
    # A = 1000 m2, D0 = 0.1 m (10 cm), C0 = 300 g/L, R = 0.01 m (10 mm)
    A = 1000.0
    D0 = 0.1
    C0 = 300.0
    R = 0.01
    V0 = A * D0
    Vr = A * R
    V1 = V0 + Vr
    C1_expected = C0 * V0 / V1
    dilution_expected = ((C0 - C1_expected) / C0) * 100.0

    result = simulate_rainfall_dilution(
        area_m2=A, initial_depth_m=D0, initial_salinity_g_l=C0, rainfall_m=R
    )

    assert math.isclose(result.initial_volume_m3, V0, rel_tol=1e-12)
    assert math.isclose(result.rainfall_volume_m3, Vr, rel_tol=1e-12)
    assert math.isclose(result.post_rain_volume_m3, V1, rel_tol=1e-12)
    assert math.isclose(result.post_rain_salinity_g_l, C1_expected, rel_tol=1e-12)
    assert math.isclose(result.dilution_percent, dilution_expected, rel_tol=1e-12)


def test_rainfall_unit_conversion():
    # 10 mm -> 0.01 m
    mm = 10.0
    m = mm / 1000.0
    assert m == 0.01


def test_dilution_zero_rainfall():
    A = 500.0
    D0 = 0.05
    C0 = 200.0
    R = 0.0
    result = simulate_rainfall_dilution(
        area_m2=A, initial_depth_m=D0, initial_salinity_g_l=C0, rainfall_m=R
    )
    assert math.isclose(result.post_rain_salinity_g_l, C0, rel_tol=1e-12)
    assert result.dilution_percent == 0.0


def test_mass_balance_service_sweep_sorted():
    svc = MassBalanceService()
    results = svc.sweep(area_m2=100.0, initial_depth_m=0.1, initial_salinity_g_l=100.0)
    rainfall_values = [r.rainfall_mm for r in results]
    assert rainfall_values == sorted(rainfall_values)


def test_digital_twin_latest_selection(monkeypatch):
    # Create a fake database that returns pan and observations
    class FakeRepo:
        def __init__(self, items):
            self._items = items

        async def find(self, *args, **kwargs):
            # simple find returning items
            return list(self._items)

        async def get(self, *args, **kwargs):
            return self._items[0]

        async def insert(self, item):
            return item

        async def count(self, *a, **k):
            return len(self._items)

    class FakeDB:
        def __init__(self, pan, observations, weather=None):
            self.pan = pan
            self.observations = observations
            self.weather = weather

        def __call__(self):
            return self

    # Minimal fake objects matching expected attributes
    class FakePan:
        def __init__(self, pan_id):
            self.pan_id = pan_id
            self.area_m2 = 1000.0
            self.location_name = "Test"

    class Obs:
        def __init__(self, pan_id, ts):
            self.pan_id = pan_id
            self.anchor_timestamp = ts
            self.timestamp = ts
            self.salinity_g_l = 100.0
            self.water_depth_cm = 10.0
            self.brine_temperature_c = 25.0
            self.ambient_temperature_c = 24.0
            self.humidity_percent = 80.0
            self.observation_id = "obs-1"
            self.data_source = "manual"

    pan = FakePan("PAN-1")
    old = Obs("PAN-1", datetime(2025, 1, 1, tzinfo=timezone.utc))
    new = Obs("PAN-1", datetime(2026, 1, 1, tzinfo=timezone.utc))

    db = FakeDB(pan, [old, new])

    # Monkeypatch repository constructors inside DigitalTwinService to use fake repos
    service = DigitalTwinService(lambda: db)

    # Patch inner repositories to return our fake repos
    service._resolved_pans = FakeRepo([pan])
    service._resolved_observations = FakeRepo([new, old])
    service._resolved_weather = FakeRepo([])
    service._resolved_states = FakeRepo([])

    import asyncio

    assembly = asyncio.run(service.assemble("PAN-1"))
    assert assembly.state.observation_timestamp == new.timestamp
