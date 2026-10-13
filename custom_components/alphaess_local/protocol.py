"""Original implementation of publicly documented AlphaESS register facts.

See ATTRIBUTION.md for the manufacturer protocol and Hillview references.
"""
from dataclasses import dataclass
import math
import time
import asyncio

DISPATCH = 0x0880
BLOCKS = ((0x0010, 19), (0x0100, 46), (0x040C, 67), (0x0850, 1), (DISPATCH, 9), (0x08D4, 2))

def u32(regs, address):
    return (regs[address] << 16) | regs[address + 1]

def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value

def decode(regs, reserve_scale=0.1, soc_scale=0.4):
    soc = regs[0x0102] / 10
    reserve = regs[0x0850] * reserve_scale
    if not 0 <= soc <= 100 or not 0 <= reserve <= 100:
        raise ValueError("Invalid SOC or native reserve; check register scaling")
    power = signed(regs[0x0126], 16)
    grid = signed(u32(regs, 0x0021), 32)
    pv = sum(u32(regs, a) for a in (0x041F, 0x0423, 0x0427, 0x042B, 0x042F, 0x0433))
    inverter = signed(u32(regs, 0x040C), 32)
    active = regs[DISPATCH]
    mode = regs[DISPATCH + 5]
    requested = u32(regs, DISPATCH + 1) - 32000
    if active not in (0, 1):
        raise ValueError("Invalid dispatch status")
    action = "normal" if not active else "freeze_charge" if mode == 19 else (
        "charge" if requested < 0 else "export" if requested > 0 else "freeze_export"
    ) if mode == 2 else "external_dispatch"
    fault_addresses = (0x011C, 0x011E, 0x0436, 0x0438, 0x043A, 0x043C, 0x044B, 0x044D, 0x08D4)
    faults = {f"{a:04x}": u32(regs, a) for a in fault_addresses}
    return dict(battery_soc=soc, native_reserve=reserve, battery_power=power, grid_power=grid,
        pv_power=pv, load_power=max(0, grid + inverter), operating_state=action,
        dispatch_active=active, dispatch_mode=mode, dispatch_power=requested,
        dispatch_target_soc=regs[DISPATCH + 6] * soc_scale,
        dispatch_remaining=u32(regs, DISPATCH + 7), faults=faults,
        fault=any(faults.values()) or regs[0x0440] == 4,
        battery_charge_energy=u32(regs, 0x0120) / 10,
        battery_discharge_energy=u32(regs, 0x0122) / 10,
        grid_import_energy=u32(regs, 0x0012) / 100,
        grid_export_energy=u32(regs, 0x0010) / 100,
        pv_energy=u32(regs, 0x043E) / 10,
        device_max_charge_power=regs[0x012C], device_max_discharge_power=regs[0x012D])

def command(action, power, target, duration, soc_scale=0.4):
    if action == "stop":
        return [0, 0, 32000, 0, 0, 0, 0, 0, 0]
    offset = 32000 + (-power if action == "charge" else power if action == "export" else 0)
    # Ceiling prevents export below the requested floor; charge never exceeds its target.
    encoded_soc = math.ceil(target / soc_scale) if action == "export" else math.floor(target / soc_scale)
    return [1, 0, offset, 0, 0, 19 if action == "freeze_charge" else 2,
            min(255, encoded_soc), 0, duration]

@dataclass
class Limits:
    capacity: float = 10
    charge_power: int = 3000
    discharge_power: int = 3000
    reserve: float = 10
    export_floor: float = 15
    reserve_scale: float = 0.1
    soc_scale: float = 0.4

class Controller:
    """Serialized guarded dispatch. A lease expires unless the owner is healthy."""
    def __init__(self, transport, limits, clock=time.monotonic, planner_authorized=None):
        self.transport, self.limits, self.clock = transport, limits, clock
        self.enabled = False
        self.data = None
        self.last_good = None
        self.intent = None
        self.end_at = 0
        self.last_renewal = 0
        self.error = None
        self.lock = asyncio.Lock()
        self.exhausted_export_until = 0
        self.owner = None
        self.planner_authorized = planner_authorized

    def _planner_ok(self, action):
        try:
            return self.planner_authorized is not None and bool(self.planner_authorized(action))
        except Exception:
            return False

    async def _read(self):
        regs = {}
        for address, count in BLOCKS:
            values = await self.transport.read(address, count)
            if len(values) != count:
                raise ValueError("Incomplete register response")
            regs.update({address + i: value for i, value in enumerate(values)})
        self.data = decode(regs, self.limits.reserve_scale, self.limits.soc_scale)
        self.data.update(battery_capacity=self.limits.capacity,
            max_charge_power=min(self.limits.charge_power, self.data["device_max_charge_power"]),
            max_discharge_power=min(self.limits.discharge_power, self.data["device_max_discharge_power"]))
        self.data["max_power"] = max(self.data["max_charge_power"], self.data["max_discharge_power"])
        self.data["effective_reserve"] = max(self.data["native_reserve"], self.limits.reserve)
        self.data["effective_export_floor"] = max(self.data["effective_reserve"], self.limits.export_floor)
        self.last_good = self.clock()
        return self.data

    async def _write_verify(self, values):
        await self.transport.write(DISPATCH, values)
        # A Modbus acknowledgement is insufficient: verify status, direction, mode and target.
        actual = await self.transport.read(DISPATCH, 9)
        if actual[:7] != values[:7] or (values[0] and not 0 < u32(dict(enumerate(actual)), 7) <= values[8]):
            raise ValueError("Dispatch readback did not confirm command")

    async def _safe_stop(self):
        self.intent = None
        self.owner = None
        self.enabled = False
        try:
            await self._write_verify(command("stop", 0, 0, 0))
            await self._read()
        except Exception:
            # Never renew an unverified command; the device's existing lease must expire.
            self.error = "Stop unverified; waiting for device dispatch expiry"

    async def poll(self):
        async with self.lock:
            try:
                data = await self._read()
                if self.intent and self.owner == "predbat" and not self._planner_ok(self.intent[0]):
                    self.error = "Predbat missing, stale or not authorized; control disabled"
                    await self._safe_stop()
                if data["fault"] and not self.intent:
                    self.enabled = False
                    self.error = "Device fault or warning; control disabled"
                if self.enabled and not self.intent and data["dispatch_active"]:
                    # Another owner started dispatch after commissioning. Observe without
                    # modifying their command; only the user's explicit stop may cancel it.
                    self.enabled = False
                    self.error = "Unowned dispatch detected; control disabled without taking over"
                if self.intent:
                    action, power, target = self.intent
                    expected = command(action, power, target, 90, self.limits.soc_scale)
                    inconsistent = (data["operating_state"] != action or data["dispatch_power"] != (-power if action == "charge" else power if action == "export" else 0)
                        or abs(data["dispatch_target_soc"] - expected[6] * self.limits.soc_scale) > 0.001)
                    if data["fault"]:
                        self.error = "Fault or external control detected; control disabled"
                        await self._safe_stop()
                    elif self.clock() >= self.end_at:
                        await self._write_verify(command("stop", 0, 0, 0))
                        self.intent = None
                        await self._read()
                    elif inconsistent:
                        self.error = "External control detected; control disabled"
                        await self._safe_stop()
                    elif action == "export" and data["battery_soc"] <= max(target, data["native_reserve"], self.limits.reserve, self.limits.export_floor):
                        self.exhausted_export_until = self.end_at
                        await self._write_verify(command("stop", 0, 0, 0))
                        self.intent = None
                        await self._read()
                    elif self.clock() - self.last_renewal >= 30 or (action == "export" and data["native_reserve"] > target):
                        if action == "export":
                            target = max(target, data["native_reserve"], self.limits.reserve, self.limits.export_floor)
                            if data["battery_soc"] <= target:
                                self.exhausted_export_until = self.end_at
                                await self._write_verify(command("stop", 0, 0, 0))
                                self.intent = None
                                await self._read()
                                return self.data
                        lease = min(90, max(1, math.ceil(self.end_at - self.clock())))
                        await self._write_verify(command(action, power, target, lease, self.limits.soc_scale))
                        self.intent = (action, power, target)
                        self.last_renewal = self.clock()
                return self.data
            except Exception:
                if self.intent:
                    self.error = "Telemetry or command failure; control disabled"
                    await self._safe_stop()
                else:
                    self.enabled = False
                    self.error = "Telemetry failure; control disabled"
                raise

    async def enable(self):
        async with self.lock:
            data = await self._read()
            if data["fault"] or data["dispatch_active"]:
                raise ValueError("Clear faults and stop existing dispatch before enabling control")
            self.enabled = True
            self.error = None

    async def disable(self):
        async with self.lock:
            if self.intent:
                await self._safe_stop()
            self.enabled = False

    async def request(self, action, power=0, target_soc=100, duration=60, stop_if=None, owner="manual"):
        async with self.lock:
            # Explicit stop is permitted while disarmed, including stopping pre-existing dispatch.
            if action == "stop":
                if stop_if is not None and (not self.intent or self.intent[0] not in stop_if):
                    return self.data
                try:
                    await self._write_verify(command("stop", 0, 0, 0))
                    self.intent = None
                    self.owner = None
                    return await self._read()
                except Exception:
                    self.error = "Stop failed; control disabled"
                    await self._safe_stop()
                    raise
            if not self.enabled:
                raise ValueError("Control is disabled; commission and enable it explicitly")
            if owner == "predbat" and not self._planner_ok(action):
                if self.intent and self.owner == "predbat":
                    self.error = "Predbat missing, stale or not authorized; control disabled"
                    await self._safe_stop()
                raise ValueError("Predbat is missing, stale, in monitor/read-only mode, or has errors")
            if action not in ("charge", "export", "freeze_charge", "freeze_export"):
                raise ValueError("Unsupported action")
            if not all(math.isfinite(float(x)) for x in (power, target_soc, duration)):
                raise ValueError("Finite values required")
            if not 0 <= target_soc <= 100 or not 1 <= duration <= 86400:
                raise ValueError("Target SOC or duration out of range")
            data = await self._read()
            if self.intent and self.clock() >= self.end_at and not data["dispatch_active"]:
                # The device may expire a completed window before the next poll.
                self.intent = None
            if not self.intent and data["dispatch_active"]:
                self.enabled = False
                self.error = "Unowned dispatch detected; control disabled without taking over"
                raise ValueError("Another controller owns active dispatch; stop it explicitly before re-arming")
            if data["fault"]:
                await self._safe_stop()
                raise ValueError("Device fault or warning; control disabled")
            if self.intent:
                old_action, old_power, old_target = self.intent
                expected = command(old_action, old_power, old_target, 90, self.limits.soc_scale)
                old_power_signed = -old_power if old_action == "charge" else old_power if old_action == "export" else 0
                if (data["operating_state"] != old_action or data["dispatch_power"] != old_power_signed
                    or abs(data["dispatch_target_soc"] - expected[6] * self.limits.soc_scale) > 0.001):
                    self.error = "External control detected before request; control disabled"
                    await self._safe_stop()
                    raise ValueError("Owned dispatch changed externally; controller stopped and disarmed")
            if action in ("charge", "export"):
                maximum = data["max_charge_power" if action == "charge" else "max_discharge_power"]
                if not 1 <= power <= maximum:
                    raise ValueError("Power outside configured/device limit")
            if action == "export":
                if self.clock() < self.exhausted_export_until:
                    raise ValueError("Export floor reached; this export window is exhausted")
                target_soc = max(target_soc, data["native_reserve"], self.limits.reserve, self.limits.export_floor)
                if data["battery_soc"] <= target_soc:
                    raise ValueError("Battery is at or below effective export floor")
            try:
                if self.intent == (action, int(power), target_soc) and data["operating_state"] == action:
                    self.owner = owner
                    self.end_at = self.clock() + duration
                    return data
                if data["dispatch_active"]:
                    await self._write_verify(command("stop", 0, 0, 0))
                    await asyncio.sleep(2)
                    if (await self._read())["dispatch_active"]:
                        raise ValueError("Device did not remain neutral before transition")
                lease = min(90, int(duration))
                await self._write_verify(command(action, int(power), target_soc, lease, self.limits.soc_scale))
                self.intent = (action, int(power), target_soc)
                self.owner = owner
                self.end_at = self.clock() + duration
                self.last_renewal = self.clock()
                self.error = None
                return await self._read()
            except Exception:
                self.error = "Command verification failed; control disabled"
                await self._safe_stop()
                raise
