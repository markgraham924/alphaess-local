from datetime import timedelta
import logging
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from .protocol import Controller, Limits
from .transport import Transport
from .const import DOMAIN, DEFAULTS
from .planner import authorized

ENERGY_KEYS = ("grid_import_energy", "grid_export_energy", "pv_energy", "battery_charge_energy", "battery_discharge_energy")

class AlphaCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, logging.getLogger(__name__), name=entry.title,
                         update_interval=timedelta(seconds=10))
        self.entry = entry
        cfg = {**DEFAULTS, **entry.data, **entry.options}
        self.planner_prefix = cfg["planner_prefix"]
        self.planner_timeout = cfg["planner_timeout"]
        self.controller = Controller(Transport(cfg["host"], cfg["port"], cfg["slave"]),
            Limits(**{key: cfg[key] for key in Limits.__dataclass_fields__}), planner_authorized=self._planner_authorized)
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.energy")
        self.energy = {}

    def _planner_authorized(self, action):
        prefix = self.planner_prefix
        mode = self.hass.states.get(f"select.{prefix}_mode")
        read_only = self.hass.states.get(f"switch.{prefix}_set_read_only")
        status = self.hass.states.get(f"{prefix}.status")
        return authorized(action, mode.state if mode else None, read_only.state if read_only else None,
            status.state if status else None, status.attributes if status else {}, dt_util.utcnow(), self.planner_timeout)

    async def async_load(self):
        self.energy = await self.store.async_load() or {}

    def _daily_energy(self, data):
        day = dt_util.now().date().isoformat()
        if self.energy.get("day") != day:
            self.energy = dict(day=day, baseline={k: data[k] for k in ENERGY_KEYS}, totals={k: 0.0 for k in ENERGY_KEYS})
        for key in ENERGY_KEYS:
            baseline = self.energy["baseline"].get(key, data[key])
            # Device counter reset: preserve energy already measured today, then establish new baseline.
            if data[key] < baseline:
                baseline = 0
            self.energy["totals"][key] += max(0, data[key] - baseline)
            self.energy["baseline"][key] = data[key]
        totals = self.energy["totals"]
        data.update(import_energy_today=totals["grid_import_energy"], export_energy_today=totals["grid_export_energy"],
            pv_energy_today=totals["pv_energy"],
            load_energy_today=max(0, totals["grid_import_energy"] - totals["grid_export_energy"] + totals["pv_energy"] +
                                  totals["battery_discharge_energy"] - totals["battery_charge_energy"]))
        self.store.async_delay_save(lambda: self.energy, 30)
        return data

    async def _async_update_data(self):
        try:
            data = await self.controller.poll()
            data["max_power"] = max(data["max_charge_power"], data["max_discharge_power"])
            return self._daily_energy(data)
        except Exception as err:
            raise UpdateFailed(f"Local AlphaESS telemetry failed: {type(err).__name__}") from err

    async def async_command(self, action, **kwargs):
        data = await self.controller.request(action, **kwargs)
        if data:
            self.async_set_updated_data(self._daily_energy(data))

    async def async_enable(self, enabled):
        if enabled:
            await self.controller.enable()
        else:
            await self.controller.disable()
        await self.async_request_refresh()
