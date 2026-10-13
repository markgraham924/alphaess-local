# Reproduce the isolated controller lab

These original test tools provide a deterministic network test double, not an AlphaESS hardware emulator. The model implements lossless battery/load/PV/grid energy balance, Modbus register reads/dispatch writes and injected faults. It does not validate real inverter firmware, timing, efficiency, electrical protection or hardware compatibility. Protocol references and Hillview credit are in [ATTRIBUTION.md](../../ATTRIBUTION.md).

## Isolated topology

```text
Browser -> explicit SSH tunnel/host proxy -> test Home Assistant 2026.10.1
                                           |
Optional test Predbat -> alphaess_local -> alphaess-simulator:1502 (Modbus)
                                               |
Test runner -> alphaess-simulator:1503 (scenario API)
```

All runtime containers share the explicitly **internal** Docker network `alphaess-release-internal`. They have no normal route to the household LAN or internet and publish no host ports. Do not use host networking or attach these containers to another network. Build/pull dependencies before starting the isolated runtime. This example targets a Linux Docker host, such as a disposable Proxmox guest. Browser access needs an explicit host-side proxy or SSH tunnel to the container's internal IP; ordinary published loopback ports were ineffective on the tested internal-network deployment. Do not publish the simulator scenario API to the LAN.

Run from `tools/lab`:

```sh
mkdir -p state/ha state/predbat reports
printf 'default_config:\n' > state/ha/configuration.yaml
docker compose -f compose.example.yaml build
docker compose -f compose.example.yaml up -d
docker network inspect alphaess-release-internal
```

Verify that the inspect result has `Internal: true`, and that each runtime container belongs only to that network. Check network namespace routes and connectivity before executing automated dispatch tests. A container may still reach Docker's internal gateway; an internal network is not a substitute for checking the actual deployment/firewall configuration. Never point this lab's integration or test drivers at a production inverter or HA instance.

Find HA's internal IP on the Linux Docker host:

```sh
docker inspect "$(docker compose -f compose.example.yaml ps -q homeassistant)" \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

For a remote host, substitute that result and your lab Docker host into `ssh -L 8123:CONTAINER_IP:8123 LAB_DOCKER_HOST`, then visit `http://localhost:8123` on the computer running the SSH client. A deliberately configured host reverse proxy to the same internal IP is an alternative. Keep browser ingress separate from container egress; do not add a second runtime network to make onboarding work. Docker Desktop container IP routing differs; use a Linux test host or an explicitly reviewed ingress arrangement.

Complete test HA onboarding through that ingress, then add **AlphaESS Local** with name `AlphaESS`, host `alphaess-simulator`, port `1502`, unit `85`, capacity `10` kWh and power limits `2500` W. Select native scale `0.1` and dispatch scale `0.4`. Control initially remains off. No Hillview YAML, supplier credentials or production HA config is required. The example's capacity/power choices are test settings; set simulator capacity/power to match when evaluating energy forecasts.

## Offline arithmetic and network checks

The simulator itself uses only the Python standard library. From this folder:

```sh
python -m unittest -v test_simulator
```

The integration unit suite runs from the repository root:

```sh
python -m pip install pytest==8.3.4
python -m pytest
```

For the independent actual-client loopback check, install `pymodbus==3.13.1` in a disposable Python environment and run from this folder:

```sh
python -m pip install pymodbus==3.13.1
python integration_wire_check.py --integration ../../custom_components/alphaess_local --report reports/wire.json
```

This driver loads the original controller and transport against the independent simulator. It proves neither HA setup nor Predbat planning.

## HA controller end-to-end check

Create a token in the **test** HA account and keep it outside version control, for example in `state/ha.token`. The driver takes a token file or environment variable and does not embed credentials. Execute it in a transient container on the same isolated network, with the token and test scripts mounted read-only:

```sh
docker run --rm --network alphaess-release-internal \
  -v "$PWD:/tests:ro" -v "$PWD/state/ha.token:/run/secrets/ha_token:ro" \
  -v "$PWD/reports:/reports" python:3.13-slim \
  python /tests/e2e_driver.py --ha-url http://homeassistant:8123 \
    --sim-url http://alphaess-simulator:1503 --token-file /run/secrets/ha_token \
    --confirm-isolated-lab --restart-ha --report /reports/controller.json
```

Pull the Python image before isolation if necessary. The required confirmation flag is an assertion by the operator, not automatic proof of isolation. The driver also checks the simulator's characteristic health response before operating. Use only a fresh disposable HA whose configured inverter points to this simulator. The optional restart flag restarts that HA. The final step disables controller control. If the device name differs, pass `--control-entity` with the real test entity ID.

These tests exercise **manual controller actions**, including first-install passivity, physical charge/export flow, reserve enforcement, directional stop ownership, freeze, command readback, fault/connection loss, recovery, restart and foreign dispatch. They do not fabricate healthy Predbat entities or claim a real planning calculation has run. Stop the optional Predbat container before running this manual suite so it cannot compete with the driver.

## Real Predbat planning and liveness

Build the compatible Predbat fork's local image separately, set `PREDBAT_LAB_IMAGE` if its name differs, and use the portable `templates/alphaess_local.yaml` configuration from [batpred-alphaess](https://github.com/markgraham924/batpred-alphaess). Store only lab-specific HA access details in the ignored `state/predbat` directory. Configure HA URL `http://homeassistant:8123` and the actual created entity IDs. Install representative load history, deterministic tariff scenarios and PV forecasts without linking supplier accounts or production devices.

After the compatible release tag is published, build its test runtime from the Predbat repository root:

```sh
git clone https://github.com/markgraham924/batpred-alphaess.git
cd batpred-alphaess
git checkout v9.3.8-alphaess.6
docker build -f fixtures/Dockerfile.alphaess-lab -t alphaess-lab-predbat:v9.3.8-alphaess.6 .
```

The fork supplies `fixtures/alphaess-lab-runtime-requirements.txt`, the portable template and deterministic test-scenario generator. Its standalone image runs Predbat directly; it is not a Supervisor add-on. Return to this repository's `tools/lab` folder for Compose. The exact source tag should match the published release and evidence you intend to reproduce.

```sh
docker compose -f compose.example.yaml --profile planner up -d
```

Initially run Predbat in Monitor and obtain an error-free completed calculation. Check its real `<prefix>.status` attributes and plan before selecting a control mode and arming the integration. Charge requires `Control charge` or `Control charge & discharge`; export/freeze aliases require the latter. Read-only must be off. The integration's configurable prefix defaults to `predbat`; its planner timeout defaults to 600 seconds to cover the usual five-minute heartbeat. A 60-second timeout is appropriate for a short controlled crash test only, and would disarm normal five-minute planning if left set that way.

Record the real calculation's plan, service requests, integration readback, simulator battery/grid flow and resulting SOC. Then separately verify Monitor, read-only, error/missing status and stopped-Predbat heartbeat cases: each must stop and disarm planner-owned commands without renewal. Re-arm explicitly after a healthy calculation. Manual force/freeze actions must continue to work without Predbat. An isolated successful software test does not establish hardware validation.

## Simulator scenario API

`GET /state` and `/health` return physical state, dispatch words, write history, energy totals and conservation error. `POST /scenario` changes `soc`, `capacity_wh`, `max_power_w`, `load_w`, `pv_w`, `native_floor_pct`, scales, `available`, `stale`, `readback_mismatch`, `fault`, `warning` or `speed`. `POST /advance` takes `{"seconds": 60}`; use `speed: 0` for explicitly controlled time. `/reboot` clears dispatch while retaining battery/counters; `/reset` restores model defaults and clears writes.

Unit `85`, big-endian word order, TCP `1502`, function codes 3/4 for reads and 6/16 for dispatch writes. Defaults are 16 kWh, 50% SOC, 600 W load, zero PV, 3680 W battery limit and 6% native reserve; change them explicitly for your scenario. Native reserve writes are rejected so tests can check the controller leaves it intact. A stale gateway scenario freezes successful register responses while physical time continues; detection of that failure is **not claimed** by this integration because the consumed protocol has no verified freshness timestamp.

No private lab tokens, `.storage` files, supplier credentials, virtual environments or original local evidence are included here. Run artifacts go into ignored directories; review and redact them deliberately before publication.
