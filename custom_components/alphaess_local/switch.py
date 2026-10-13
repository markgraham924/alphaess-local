from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from .entity import AlphaEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ControlSwitch(hass.data[DOMAIN][entry.entry_id], "control_enabled")])

class ControlSwitch(AlphaEntity, SwitchEntity):
    _attr_name = "Control enabled"
    _attr_icon = "mdi:battery-lock"
    @property
    def is_on(self):
        return self.coordinator.controller.enabled

    async def async_turn_on(self, **kwargs):
        try:
            await self.coordinator.async_enable(True)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_enable(False)
        self.async_write_ha_state()
