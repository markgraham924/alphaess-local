# AlphaESS Local (experimental beta)

One Home Assistant custom integration provides local AlphaESS battery telemetry and bounded, guarded battery dispatch. It replaces the required Hillview Modbus YAML and separate household controller for this limited purpose. It does **not** reproduce the whole Hillview dashboard, register browser, clipping, peak shaving, scheduled charge, or feed-in automation feature set.

Predbat plans; AlphaESS Local executes charge/export/freeze requests and verifies Modbus register readback. Cloud access, E.ON, AI, vehicle sensors, MQTT and household helpers are not required.

**Validation status:** simulated AlphaESS Modbus testing; no hardware compatibility certification. Install first on a test HA system or with control left disabled. Firmware and model differences require commissioning against the inverter's actual readings and behaviour. Only Modbus TCP is supported in this release, including a TCP gateway if it implements compatible register behaviour.

This beta requires **Home Assistant 2026.10.1 or newer** and pins the same PyModbus 3.13.1 dependency as that tested HA release. The full acceptance environment is HA Container 2026.10.1; other HA versions and physical inverter/firmware combinations have not been independently validated. It does not configure or guarantee a site/grid export cap: battery power limits are not a limit on combined PV-plus-battery grid export.

## Install

1. Connect the inverter's supported LAN/Modbus interface using manufacturer instructions. Reserve its IP address. Confirm its local Modbus TCP endpoint (usually port 502, unit 85). Do not expose Modbus to the internet.
2. Back up Home Assistant. Stop other local dispatch writers, Hillview dispatch automations and competing cloud battery schedules/controllers before commissioning this controller. Avoid configuring both this integration and a YAML Modbus client to write to the same inverter.
3. HACS: add this GitHub repository as a custom **Integration** repository, download **AlphaESS Local**, and restart Home Assistant. Manual alternative: copy `custom_components/alphaess_local` to `/config/custom_components/alphaess_local` and restart. No YAML package is required.
4. Open **Settings → Devices & services → Add integration → AlphaESS Local**. Enter the host, TCP port, unit/slave ID, device name, usable battery capacity in kWh, maximum charge/discharge battery power in W, reserve percentage and forced-export floor percentage. Use your installed equipment ratings, not another owner's values. Runtime power is limited by both configuration and device-reported maximum battery power; zero/missing device limits prevent control.
5. Verify the register scales. Manufacturer documentation specifies native reserve **0.1 %/bit** and dispatch target **0.4 %/bit**. Hillview and some deployed firmware use native reserve **1 %/bit** and dispatch target **0.392 %/bit**. These are explicit setup choices. Compare displayed native reserve and dispatched target with the inverter/app before proceeding. There is no automatic heuristic or reserve-register write.
6. Observe readings with **Control enabled** off. Battery SOC must agree with the inverter; battery power is positive for discharge and negative for charge, grid power positive for import and negative for export. Verify PV/load values and fault indications. Existing dispatch is only observed while disarmed and is not adopted or renewed; it may continue until its device timeout. Use the explicit **Stop** action to cancel an existing dispatch if appropriate.
7. With other controllers stopped and the device fault-free and in normal mode, turn on **Control enabled**. Test a short low-power charge (for example 300 W for one minute), then **Stop**, then a short discharge with a conservative target above native reserve. Check actual battery/grid power, not just status text. Test both freeze actions only if your model supports them.
8. Install the compatible Predbat release and select its `templates/alphaess_local.yaml` configuration example. Supply your own tariff, forecast, hardware capacity/power and entity names. Start Predbat in monitor mode, check a sensible plan, then enable control deliberately. This integration's switch starts **off after every HA restart/reload**, requiring explicit re-arming. Do not add an automatic startup re-arm until you have commissioned and chosen that policy yourself.

## Settings and limits

Settings → integration → Configure changes battery capacity, power limits, controller reserve/export floor and register scales. Updating settings reloads the integration and leaves control off. Host, port and unit changes currently require removing/re-adding the entry. Unique device/entity identities are based on host, port and unit; names are customisable and multiple devices require an explicit service `entry_id`.

The reserve and export floor are software floors for **forced export**, not writes to the native inverter setting. Effective forced-export target is at least the maximum of requested target, configured reserve, configured export floor and current device native reserve. The native reserve is never changed by this integration. Normal self-consumption and Mode 19 remain governed by the inverter's own reserve. If you need a higher native reserve, set it using a verified manufacturer-supported method before commissioning.

The `effective_reserve` sensor exposes the greater of native and configured reserve; `effective_export_floor` includes the forced-export floor too. Use these in Predbat so its plan reflects the controller's limits. The native reserve sensor remains the unmodified device readback.

Charge/export power means **battery power**, not a guaranteed grid import/export level. House loads and PV affect grid power. Freeze charge is Mode 19 (prevents battery charge but can still supply house loads); freeze export is Mode 2 with zero battery power (holds battery; PV may still export). Neither is a guaranteed zero-grid-export controller.

Daily import/export/PV/load energy sensors use persistent deltas of native lifetime counters and reset on the HA local date. The first day is partial; they do not backfill past consumption or account for increments missed over a midnight outage. Derived load is an energy balance, subject to device counter precision, inverter losses and meter topology. Lifetime energy sensors can be used directly in the HA Energy dashboard. Predbat historical/load forecasting needs enough representative history after installation.

## Actions and Predbat compatibility

All services use the `alphaess_local` domain. `entry_id` is optional only when one device is configured; find it in the Operating state sensor's attributes. Names shown below assume device name `AlphaESS`; use the actual entity IDs created on your installation.

| Action | Fields | Behaviour |
|---|---|---|
| `force_charge` | `power_w`, `target_soc`, `duration_minutes` | Mode 2 negative battery power |
| `force_export` | `power_w`, `target_soc`, `duration_minutes` | Mode 2 positive battery power with enforced floors |
| `freeze_charge` | `duration_minutes` | Mode 19, model-dependent |
| `freeze_export` | `duration_minutes` | Mode 2 zero battery power |
| `stop` | optional `entry_id` | Explicit verified normal request; permitted while control is off |
| `charge_start` | `power`, `target_soc`, `end_time` | Predbat-compatible charge |
| `discharge_start` | `power`, `target_soc`, `end_time` | Predbat-compatible export |
| `charge_stop` | optional `entry_id` | Stops an owned charge only |
| `discharge_stop` | optional `entry_id` | Stops an owned export or either freeze only |
| `mode19_start` | `target_soc`, `end_time` | Predbat-compatible freeze charge; target accepted for API compatibility |
| `mode19_stop` | optional `entry_id` | Stops owned Mode 19 only |

`end_time` accepts a timezone-qualified ISO datetime or Predbat's local `HH:MM:SS`; a passed local clock time means the next day. Each requested operation must end within 24 hours. `power` and `target_soc` numeric strings from Predbat are accepted. Semantic duration actions default to one minute when omitted. Opposite-direction stop actions deliberately do not cancel each other's owned operations. An explicit `stop` is the way to cancel an external or startup dispatch.

The planner aliases `charge_start`, `discharge_start` and `mode19_start` require a healthy, authorised Predbat instance. Set **Predbat entity prefix** to its configured prefix (default `predbat`). The integration checks `select.<prefix>_mode`, `switch.<prefix>_set_read_only`, and `<prefix>.status` with explicit `error: false` and a fresh timezone-qualified `last_updated` attribute. Charging permits `Control charge` or `Control charge & discharge`; export/freeze aliases require `Control charge & discharge`. Monitor, SOC-only, read-only, missing entities, errors, stale timestamps and future timestamps reject planner start/renewal. Default heartbeat timeout is 600 seconds; normal Predbat calculations publish every five minutes. Configure 30–3600 seconds deliberately; a value below the calculation interval is suitable only for a test with additional heartbeat updates.

An accepted planner window retains planner ownership. Every telemetry poll rechecks planner health before renewing its device lease. If Predbat stops publishing, becomes read-only, switches to Monitor/SOC-only, loses the required control mode or reports an error, the integration stops the owned command and disarms; recovery requires explicit re-arming. This prevents an otherwise healthy HA controller from keeping a dead planner's long window alive. Manual `force_charge`, `force_export` and `freeze_*` actions are independent of Predbat and still work without its entities. They deliberately transfer command ownership to the user.

Core entities: `switch.alphaess_control_enabled`; `binary_sensor.alphaess_fault`; sensors `alphaess_battery_soc`, `alphaess_battery_power`, `alphaess_grid_power`, `alphaess_pv_power`, `alphaess_load_power`, `alphaess_battery_capacity`, `alphaess_native_reserve`, `alphaess_max_charge_power`, `alphaess_max_discharge_power`, `alphaess_max_power`, `alphaess_operating_state`, plus lifetime/daily energy and dispatch target/time sensors. IDs can change with device name or user renaming. Never assume a status label proves physical response.

## Failure behaviour

Only the nine dispatch registers starting at `0x0880` are writable. Each active dispatch has a device lease of at most **90 seconds**, renewed every 30 seconds while owned, healthy and inside the requested window. Telemetry is polled every ten seconds. Charge/export transitions stop and verify neutral, wait two seconds, then re-read before starting the new operation. Status, mode, power and target are read back; write acknowledgement alone is not success.

Faults/warnings, unexpected dispatch direction/power/target, failed write verification and failed telemetry reads disable control, clear ownership and attempt an explicit stop. If the connection is unavailable, that stop cannot be guaranteed; the existing device dispatch lease must expire. Recovery never automatically resumes ownership. Reaching the forced-export floor ends and exhausts the current export window to avoid repeated re-arming after an SOC rebound.

If another controller starts dispatch while this integration is armed but idle, AlphaESS Local disarms and observes without stopping, adopting or renewing that external command. A new planner request also checks fresh telemetry before writing and rejects such takeover, even when its requested settings match. The explicit `stop` action remains available to the user; re-arm only after normal mode is verified.

Modbus has no authentication or encryption. Anyone able to reach the endpoint may issue commands; control ownership is advisory. A gateway that serves stale but successful responses can evade connection-loss checks because these documented registers do not provide a verified sample timestamp. Readback confirms register acceptance, not actual wattage or firmware enforcement of safety limits/deadman. A simulator validates software paths but does not establish physical inverter compatibility. These are commissioning limitations, not promises of hardware fault protection.

## Update and rollback

Stop Predbat control, call `alphaess_local.stop`, confirm normal mode, then turn off the control switch. Back up the integration folder and HA config/storage. Update through HACS or replace the integration folder and restart HA. Verify readings before re-enabling. To roll back, restore the prior integration version and restart. To remove, stop explicitly, disable control and remove the config entry/folder; only then re-enable an alternative controller. No native reserve, schedules, cloud linkage, grid protection or persistent inverter settings are modified.

## Development

Run `python -m pytest --confcutdir=tests tests` from this repository with pytest installed. The safety tests require no Home Assistant installation; full integration verification uses a real isolated HA Container and an independent network simulator. See release evidence for the tested HA/image versions and end-to-end results. This repository's original code is MIT licensed; manufacturer protocol PDFs and Hillview resources are linked, not redistributed. See [ATTRIBUTION.md](ATTRIBUTION.md).

The original simulator, independent network driver and isolated Compose example are included in [tools/lab](tools/lab/README.md). That guide distinguishes controller testing from real Predbat planning and documents how to reproduce each without production devices or supplier credentials.
