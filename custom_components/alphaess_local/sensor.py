from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfEnergy
from .entity import AlphaEntity
from .const import DOMAIN

NAMES = {"battery_soc": "Battery SOC", "battery_power": "Battery power", "grid_power": "Grid power",
    "pv_power": "PV power", "load_power": "Load power", "battery_capacity": "Battery capacity",
    "native_reserve": "Native reserve", "max_charge_power": "Max charge power", "max_discharge_power": "Max discharge power",
    "effective_reserve": "Effective reserve", "effective_export_floor": "Effective export floor",
    "max_power": "Max power", "operating_state": "Operating state", "dispatch_target_soc": "Dispatch target SOC",
    "dispatch_remaining": "Dispatch remaining", "battery_charge_energy": "Battery charge energy",
    "battery_discharge_energy": "Battery discharge energy", "grid_import_energy": "Grid import energy",
    "grid_export_energy": "Grid export energy", "pv_energy": "PV energy", "import_energy_today": "Import energy today",
    "export_energy_today": "Export energy today", "pv_energy_today": "PV energy today", "load_energy_today": "Load energy today"}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(AlphaSensor(coordinator, key, name) for key, name in NAMES.items())

class AlphaSensor(AlphaEntity, SensorEntity):
    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key)
        self.key = key
        self._attr_name = name
        if key.endswith("power"):
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif "soc" in key or key in ("native_reserve", "effective_reserve", "effective_export_floor"):
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            if key == "battery_soc":
                self._attr_device_class = SensorDeviceClass.BATTERY
        elif "energy" in key or key == "battery_capacity":
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_device_class = SensorDeviceClass.ENERGY
            if key != "battery_capacity":
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif key == "dispatch_remaining":
            self._attr_native_unit_of_measurement = "s"

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        if self.key == "operating_state":
            return dict(entry_id=self.coordinator.entry.entry_id, control_enabled=self.coordinator.controller.enabled,
                        last_error=self.coordinator.controller.error)
        return None
