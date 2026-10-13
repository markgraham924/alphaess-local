from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

class AlphaEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.unique_id}_{key}"
        self._attr_device_info = dict(identifiers={(DOMAIN, coordinator.entry.unique_id)},
            name=coordinator.entry.title, manufacturer="AlphaESS", model="Local Modbus (experimental)")
