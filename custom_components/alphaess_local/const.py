DOMAIN = "alphaess_local"
PLATFORMS = ["sensor", "switch", "binary_sensor"]
DEFAULTS = dict(name="AlphaESS", port=502, slave=85, capacity=10.0,
                charge_power=3000, discharge_power=3000, reserve=10.0,
                export_floor=15.0, reserve_scale=0.1, soc_scale=0.4,
                planner_prefix="predbat", planner_timeout=600)
