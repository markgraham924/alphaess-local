"""Combined local AlphaESS telemetry and guarded dispatch integration."""
import voluptuous as vol
from datetime import datetime, timedelta
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from .const import DOMAIN, PLATFORMS
from .coordinator import AlphaCoordinator

SERVICES = {"force_charge": "charge", "force_export": "export", "charge_start": "charge",
            "discharge_start": "export", "freeze_charge": "freeze_charge", "mode19_start": "freeze_charge",
            "freeze_export": "freeze_export", "stop": "stop", "charge_stop": "stop",
            "discharge_stop": "stop", "mode19_stop": "stop"}

async def async_setup_entry(hass, entry):
    coordinator = AlphaCoordinator(hass, entry)
    await coordinator.async_load()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        coordinator.controller.transport.close()
        raise
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle(call: ServiceCall):
        entries = hass.data[DOMAIN]
        identity = call.data.get("entry_id")
        if identity is None:
            if len(entries) != 1:
                raise HomeAssistantError("Select entry_id when multiple AlphaESS devices are installed")
            identity = next(iter(entries))
        if identity not in entries:
            raise HomeAssistantError("Unknown AlphaESS entry_id")
        target = entries[identity]
        kwargs = {}
        action = SERVICES[call.service]
        if action != "stop":
            kwargs = dict(power=call.data.get("power_w", call.data.get("power", 0)),
                target_soc=call.data.get("target_soc", 100), duration=call.data.get("duration_minutes", 1) * 60)
            if "end_time" in call.data:
                end = dt_util.parse_datetime(call.data["end_time"])
                if end is None:
                    try:
                        clock = datetime.strptime(call.data["end_time"], "%H:%M:%S").time()
                    except ValueError as err:
                        raise HomeAssistantError("end_time must be ISO with timezone or local HH:MM:SS") from err
                    now = dt_util.now()
                    end = datetime.combine(now.date(), clock, now.tzinfo)
                    if end <= now:
                        end += timedelta(days=1)
                if end.tzinfo is None:
                    raise HomeAssistantError("ISO end_time must include timezone")
                kwargs["duration"] = (end - dt_util.utcnow()).total_seconds()
        elif call.service != "stop":
            kwargs["stop_if"] = {"charge_stop": ("charge",), "discharge_stop": ("export", "freeze_charge", "freeze_export"), "mode19_stop": ("freeze_charge",)}[call.service]
        try:
            await target.async_command(action, **kwargs)
        except (ValueError, ConnectionError) as err:
            raise HomeAssistantError(str(err)) from err

    schema = vol.Schema({vol.Optional("entry_id"): str, vol.Optional("power_w"): vol.Coerce(float),
        vol.Optional("power"): vol.Coerce(float), vol.Optional("target_soc"): vol.Coerce(float),
        vol.Optional("duration_minutes"): vol.Coerce(float), vol.Optional("end_time"): str})
    for service in SERVICES:
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handle, schema=schema)
    return True

async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    try:
        await coordinator.controller.disable()
    finally:
        coordinator.controller.transport.close()
        await coordinator.store.async_save(coordinator.energy)
    if not hass.data[DOMAIN]:
        for service in SERVICES:
            hass.services.async_remove(DOMAIN, service)
    return True
