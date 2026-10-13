"""Safety invariants with independent fake register transport (no HA required)."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import pytest

spec = importlib.util.spec_from_file_location("alpha_protocol", Path(__file__).parents[1] / "custom_components/alphaess_local/protocol.py")
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)

class Device:
    def __init__(self):
        self.regs = {a: 0 for start, n in p.BLOCKS for a in range(start, start+n)}
        self.regs.update({0x0102: 650, 0x0850: 200, 0x012C: 2500, 0x012D: 2500, 0x0440: 1})
        self.writes = []
        self.offline = False
        self.ignore = False

    async def read(self, address, count):
        if self.offline:
            raise ConnectionError("Disconnected")
        return [self.regs[address+i] for i in range(count)]

    async def write(self, address, values):
        if self.offline:
            raise ConnectionError("Disconnected")
        self.writes.append((address, values))
        if not self.ignore:
            self.regs.update({address+i: v for i, v in enumerate(values)})

def run(coro):
    return asyncio.run(coro)

def test_setup_and_poll_never_write_or_arm():
    async def case():
        d = Device()
        d.regs[0x0880] = 1
        c = p.Controller(d, p.Limits())
        await c.poll()
        assert not c.enabled and not d.writes
        with pytest.raises(ValueError, match="existing dispatch"):
            await c.enable()
        assert not d.writes
    run(case())

def test_disabled_rejects_every_start():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        for a in ("charge", "export", "freeze_charge", "freeze_export"):
            with pytest.raises(ValueError, match="disabled"):
                await c.request(a, 1000, 30)
        assert not d.writes
    run(case())

def test_native_higher_reserve_and_power_limit():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits(discharge_power=1800, reserve=10, export_floor=15))
        await c.enable()
        with pytest.raises(ValueError, match="Power outside"):
            await c.request("export", 1900, 5)
        await c.request("export", 1500, 5)
        address, values = d.writes[-1]
        assert address == 0x0880 and values[2] == 33500 and values[6] == 50
        assert values[8] == 60
        assert all(a == 0x0880 for a, _ in d.writes)
    run(case())

def test_direction_stop_and_duplicate_start():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("export", 1500, 25, 600)
        count = len(d.writes)
        await c.request("stop", stop_if=("charge",))
        await c.request("export", 1500, 25, 600)
        assert len(d.writes) == count
        await c.request("charge", 1500, 90, 600)
        assert d.writes[-2][1][0] == 0 and d.writes[-1][1][2] == 30500
    run(case())

def test_fault_shutdown_and_no_renewal():
    async def case():
        d = Device()
        now = [0]
        c = p.Controller(d, p.Limits(), lambda: now[0])
        await c.enable()
        await c.request("charge", 1500, 90, 600)
        d.regs[0x011F] = 1
        now[0] = 31
        await c.poll()
        assert not c.enabled and not c.intent and d.writes[-1][1][0] == 0
        count = len(d.writes)
        now[0] = 100
        await c.poll()
        assert len(d.writes) == count
    run(case())

def test_loss_disarms_even_if_stop_unreachable():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("export", 1500, 25, 600)
        assert d.writes[-1][1][8] == 90
        d.offline = True
        with pytest.raises(ConnectionError):
            await c.poll()
        assert not c.enabled and not c.intent
        d.offline = False
        await c.poll()
        assert len(d.writes) == 1  # Reconnection never resumes the old command.
    run(case())

def test_acknowledged_but_ignored_write_disarms():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        d.ignore = True
        with pytest.raises(ValueError, match="readback"):
            await c.request("charge", 1000, 80, 600)
        assert not c.enabled and not c.intent
    run(case())

def test_external_setpoint_detected():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("charge", 1000, 80, 600)
        d.regs[0x0882] = 30000
        await c.poll()
        assert not c.enabled and d.writes[-1][1][0] == 0
    run(case())

def test_deadman_renewal_and_expiry_are_bounded():
    async def case():
        now = [0]
        d = Device()
        c = p.Controller(d, p.Limits(), lambda: now[0])
        await c.enable()
        await c.request("charge", 1000, 80, 100)
        now[0] = 31
        await c.poll()
        assert d.writes[-1][1][8] == 69
        now[0] = 101
        await c.poll()
        assert d.writes[-1][1][0] == 0 and c.intent is None
    run(case())

def test_increased_native_reserve_is_applied_before_renewal():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("export", 1000, 25, 600)
        d.regs[0x0850] = 300
        await c.poll()
        assert d.writes[-1][1][6] == 75
        assert d.regs[0x0850] == 300
    run(case())

def test_unsafe_inputs_and_invalid_telemetry():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        for power, target, duration in ((float("nan"), 80, 60), (1000, 101, 60), (1000, 80, 86401)):
            with pytest.raises(ValueError):
                await c.request("charge", power, target, duration)
        assert not d.writes
        d.regs[0x0102] = 65535
        with pytest.raises(ValueError, match="SOC"):
            await c.poll()
    run(case())

def test_unowned_armed_control_disarms_on_fault_without_writes():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        d.regs[0x011F] = 1
        await c.poll()
        assert not c.enabled and not d.writes
    run(case())

def test_export_floor_stops_next_poll_and_latches_window():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("export", 1000, 25, 600)
        d.regs[0x0102] = 250
        await c.poll()
        assert c.intent is None and d.writes[-1][1][0] == 0
        d.regs[0x0102] = 260
        with pytest.raises(ValueError, match="exhausted"):
            await c.request("export", 1000, 25, 600)
    run(case())

def test_discharge_stop_cancels_freeze_not_charge():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("freeze_charge", duration=600)
        await c.request("stop", stop_if=("export", "freeze_charge", "freeze_export"))
        assert c.intent is None
        await c.request("charge", 1000, 80, 600)
        count = len(d.writes)
        await c.request("stop", stop_if=("export", "freeze_charge", "freeze_export"))
        assert c.intent[0] == "charge" and len(d.writes) == count
    run(case())

def test_explicit_stop_failure_never_renews_old_intent():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("charge", 1000, 80, 600)
        d.offline = True
        with pytest.raises(ConnectionError):
            await c.request("stop")
        assert not c.enabled and c.intent is None
    run(case())

def test_natural_window_expiry_does_not_disarm_healthy_controller():
    async def case():
        now = [0]
        d = Device()
        c = p.Controller(d, p.Limits(), lambda: now[0])
        await c.enable()
        await c.request("charge", 1000, 80, 60)
        now[0] = 61
        d.regs[0x0880] = 0  # The device expires the short lease itself.
        await c.poll()
        assert c.enabled and c.intent is None
    run(case())

def test_foreign_dispatch_while_armed_idle_disarms_without_writes():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        d.regs.update({0x0880: 1, 0x0882: 33000, 0x0885: 2, 0x0886: 50, 0x0888: 600})
        await c.poll()
        assert not c.enabled and not d.writes
        assert d.regs[0x0880] == 1 and d.regs[0x0882] == 33000
    run(case())

def test_request_checks_foreign_dispatch_before_poll_without_overwriting():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        d.regs.update({0x0880: 1, 0x0882: 33000, 0x0885: 2, 0x0886: 50, 0x0888: 600})
        with pytest.raises(ValueError, match="Another controller"):
            await c.request("charge", 1000, 80, 600)
        assert not c.enabled and not d.writes
    run(case())

def test_matching_external_request_is_not_adopted_or_renewed():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        d.regs.update({0x0880: 1, 0x0882: 33000, 0x0885: 2, 0x0886: 50, 0x0888: 600})
        with pytest.raises(ValueError, match="Another controller"):
            await c.request("export", 1000, 20, 600)
        assert not c.enabled and not d.writes
        await c.request("stop")  # Explicit stop remains available while disarmed.
        assert d.writes[-1][1][0] == 0
    run(case())

def test_owned_foreign_change_before_next_request_stops_and_disarms():
    async def case():
        d = Device()
        c = p.Controller(d, p.Limits())
        await c.enable()
        await c.request("charge", 1000, 80, 600)
        d.regs[0x0882] = 30000
        with pytest.raises(ValueError, match="changed externally"):
            await c.request("charge", 1000, 80, 600)
        assert not c.enabled and c.intent is None
        assert d.writes[-1][1][0] == 0
    run(case())

def test_new_window_after_natural_expiry_before_poll_is_allowed():
    async def case():
        now = [0]
        d = Device()
        c = p.Controller(d, p.Limits(), lambda: now[0])
        await c.enable()
        await c.request("charge", 1000, 80, 60)
        now[0] = 61
        d.regs[0x0880] = 0
        await c.request("export", 1000, 25, 600)
        assert c.enabled and c.intent[0] == "export"
    run(case())
