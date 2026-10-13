"""Local asynchronous Modbus transport; no HA Modbus YAML required."""
import asyncio
from pymodbus.client import AsyncModbusTcpClient

class Transport:
    def __init__(self, host, port, slave):
        self.client = AsyncModbusTcpClient(host, port=port, timeout=10, retries=0)
        self.slave = slave
        self.lock = asyncio.Lock()

    async def _connect(self):
        if not self.client.connected and not await self.client.connect():
            raise ConnectionError("Unable to connect to AlphaESS Modbus")

    async def read(self, address, count):
        async with self.lock:
            await self._connect()
            reply = await self.client.read_holding_registers(address, count=count, device_id=self.slave)
            await asyncio.sleep(0.12)
            if reply.isError():
                raise ConnectionError("AlphaESS register read failed")
            return reply.registers

    async def write(self, address, values):
        if address != 0x0880 or len(values) != 9:
            raise ValueError("Only bounded dispatch writes are allowed")
        async with self.lock:
            await self._connect()
            reply = await self.client.write_registers(address, values, device_id=self.slave)
            await asyncio.sleep(0.12)
            if reply.isError():
                raise ConnectionError("AlphaESS dispatch write failed")

    def close(self):
        self.client.close()
