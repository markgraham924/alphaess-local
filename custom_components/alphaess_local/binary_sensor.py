from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from .entity import AlphaEntity
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FaultSensor(hass.data[DOMAIN][entry.entry_id], "fault")])

class FaultSensor(AlphaEntity, BinarySensorEntity):
    _attr_name = "Fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    @property
    def is_on(self):
        return self.coordinator.data["fault"]
    @property
    def extra_state_attributes(self):
        return self.coordinator.data["faults"]
