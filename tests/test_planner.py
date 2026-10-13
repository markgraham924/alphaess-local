"""Planner health boundaries independent of Home Assistant entity implementation."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import pytest
from test_controller import Device, p, run

spec = importlib.util.spec_from_file_location("alpha_planner", Path(__file__).parents[1] / "custom_components/alphaess_local/planner.py")
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)

def healthy(**overrides):
    args = dict(action="charge", mode="Control charge & discharge", read_only="off", status="Charging",
        attributes=dict(error=False, last_updated=NOW.isoformat()), now=NOW, timeout=600)
    args.update(overrides)
    return planner.authorized(**args)

@pytest.mark.parametrize("override", [
    {"mode": None}, {"mode": "Monitor"}, {"mode": "Control SOC only"},
    {"read_only": "on"}, {"read_only": None}, {"status": None}, {"status": "unavailable"},
    {"attributes": {}}, {"attributes": {"error": True, "last_updated": NOW.isoformat()}},
    {"attributes": {"error": False, "last_updated": (NOW-timedelta(seconds=601)).isoformat()}},
    {"attributes": {"error": False, "last_updated": (NOW+timedelta(seconds=1)).isoformat()}},
    {"attributes": {"error": False, "last_updated": "2026-10-13T15:00:00"}},
    {"attributes": {"error": False, "last_updated": "bad"}},
])
def test_invalid_health_or_control_mode_is_rejected(override):
    assert not healthy(**override)

def test_fresh_heartbeat_and_direction_specific_mode():
    assert healthy()
    assert healthy(mode="Control charge")
    assert not healthy(action="export", mode="Control charge")
    assert not healthy(action="freeze_charge", mode="Control charge")
    assert healthy(action="export")
    assert healthy(attributes=dict(error=False, last_updated="2026-10-13T15:00:00+0000"))

def test_missing_planner_rejects_alias_without_writes():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        with pytest.raises(ValueError, match="Predbat"):
            await c.request("charge", 1000, 80, 600, owner="predbat")
        assert not d.writes and c.intent is None
    run(case())

def test_fresh_planner_accepts_and_loss_stops_without_renewal():
    async def case():
        live = [True]
        now = [0]
        d = Device()
        c = p.Controller(d, p.Limits(), lambda: now[0], lambda action: live[0])
        await c.enable()
        await c.request("charge", 1000, 80, 600, owner="predbat")
        assert c.owner == "predbat"
        live[0] = False
        now[0] = 31
        await c.poll()
        assert not c.enabled and c.intent is None and d.writes[-1][1][0] == 0
        count = len(d.writes)
        now[0] = 100
        await c.poll()
        assert len(d.writes) == count
    run(case())

def test_stale_duplicate_alias_stops_old_window_instead_of_extending_it():
    async def case():
        live = [True]
        d = Device()
        c = p.Controller(d, p.Limits(), planner_authorized=lambda action: live[0])
        await c.enable()
        await c.request("export", 1000, 25, 600, owner="predbat")
        live[0] = False
        with pytest.raises(ValueError, match="Predbat"):
            await c.request("export", 1000, 25, 600, owner="predbat")
        assert not c.enabled and c.intent is None and d.writes[-1][1][0] == 0
    run(case())

def test_manual_operation_is_independent_of_missing_or_stale_planner():
    async def case():
        now = [0]
        d = Device()
        c = p.Controller(d, p.Limits(), lambda: now[0], lambda action: False)
        await c.enable()
        await c.request("charge", 1000, 80, 600)
        now[0] = 31
        await c.poll()
        assert c.enabled and c.intent is not None and c.owner == "manual"
        assert d.writes[-1][1][0] == 1
    run(case())
