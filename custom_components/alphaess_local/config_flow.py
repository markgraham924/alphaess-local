"""UI setup with explicit register scaling and conservative runtime limits."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, DEFAULTS
from .transport import Transport
from .protocol import Controller, Limits

def fields(defaults, connection=True):
    schema = {}
    if connection:
        schema.update({vol.Required("name", default=defaults["name"]): str,
                       vol.Required("host", default=defaults.get("host", "")): str,
                       vol.Required("port", default=defaults["port"]): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                       vol.Required("slave", default=defaults["slave"]): vol.All(vol.Coerce(int), vol.Range(min=1, max=247))})
    for key, minimum, maximum, coerce in (("capacity", 0.1, 500, float), ("charge_power", 1, 30000, int),
        ("discharge_power", 1, 30000, int), ("reserve", 0, 100, float), ("export_floor", 0, 100, float)):
        schema[vol.Required(key, default=defaults[key])] = vol.All(vol.Coerce(coerce), vol.Range(min=minimum, max=maximum))
    schema[vol.Required("reserve_scale", default=defaults["reserve_scale"])] = vol.In([0.1, 1.0])
    schema[vol.Required("soc_scale", default=defaults["soc_scale"])] = vol.In([0.4, 0.392])
    return vol.Schema(schema)

class AlphaFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            identity = f"{user_input['host'].strip().lower()}:{user_input['port']}:{user_input['slave']}"
            await self.async_set_unique_id(identity)
            self._abort_if_unique_id_configured()
            user_input["host"] = user_input["host"].strip()
            transport = Transport(user_input["host"], user_input["port"], user_input["slave"])
            try:
                controller = Controller(transport, Limits(**{k: user_input[k] for k in Limits.__dataclass_fields__}))
                await controller.poll()  # Read-only probe: never writes during setup.
            except Exception:
                errors["base"] = "cannot_connect"
            finally:
                transport.close()
            if not errors:
                return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(step_id="user", data_schema=fields({**DEFAULTS, **(user_input or {})}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AlphaOptions()

class AlphaOptions(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=fields({**DEFAULTS, **self.config_entry.data,
                                                   **self.config_entry.options}, connection=False))
