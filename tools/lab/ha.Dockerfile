FROM ghcr.io/home-assistant/home-assistant:2026.10.1
# Install during build, before the runtime joins a network without external egress.
RUN pip install --no-cache-dir pymodbus==3.13.1
