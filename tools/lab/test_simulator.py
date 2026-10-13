import asyncio, json, struct, unittest
from model import Battery,words32,uint32
from server import Lab

def command(power=2000,target=20,duration=3600,mode=2):
    return [1,*words32(32000+power),0,0,mode,round(target/0.4),*words32(duration)]

class ArithmeticTests(unittest.TestCase):
    def test_charge_soc_and_import_conservation(self):
        b=Battery(); b.write(0x0880,command(-2000,100)); b.advance(1800)
        self.assertAlmostEqual(b.soc,56.25); self.assertAlmostEqual(b.charge_kwh,1)
        self.assertAlmostEqual(b.import_kwh,1.3); self.assertEqual(b.state()['conservation_error_w'],0)

    def test_discharge_export_conservation(self):
        b=Battery(pv_w=400); b.write(0x0880,command()); b.advance(1800)
        self.assertAlmostEqual(b.soc,43.75); self.assertAlmostEqual(b.export_kwh,0.9)
        self.assertAlmostEqual(b.discharge_kwh,1)

    def test_native_reserve_overrides_lower_dispatch_target(self):
        b=Battery(soc=30,native_floor_pct=25); b.write(0x0880,command(3680,5)); b.advance(1800)
        self.assertAlmostEqual(b.soc,25); self.assertEqual(b.battery_w,0)
        self.assertEqual(b.registers()[0x0850],250)
        with self.assertRaises(ValueError): b.write(0x0850,[60])

    def test_charge_target_keeps_dispatch_active_but_idles(self):
        b=Battery(soc=99); b.write(0x0880,command(-3680,100)); b.advance(1800)
        self.assertEqual(b.soc,100); self.assertEqual(b.battery_w,0); self.assertEqual(b.dispatch[0],1)

    def test_deadman_and_renewal(self):
        b=Battery(); b.write(0x0880,command(duration=10)); b.advance(5)
        self.assertEqual(b.registers()[0x0888],5)
        b.write(0x0887,words32(10)); b.advance(9); self.assertEqual(b.dispatch[0],1)
        b.advance(1); self.assertEqual(b.dispatch[0],0)

    def test_mode19_blocks_charge_but_serves_load(self):
        b=Battery(pv_w=2000); b.write(0x0880,command(mode=19)); b.advance(10)
        self.assertEqual(b.battery_w,0); self.assertEqual(b.grid_w,-1400)
        b.configure({'pv_w':0}); self.assertEqual(b.battery_w,600)

    def test_normal_pv_and_load(self):
        b=Battery(pv_w=1600); b.advance(3600)
        self.assertAlmostEqual(b.soc,56.25); self.assertEqual(b.grid_w,0)
        b.configure({'pv_w':0,'load_w':1000}); b.advance(3600)
        self.assertAlmostEqual(b.soc,50)

    def test_fault_stale_mismatch_and_reboot(self):
        b=Battery(); b.write(0x0880,command()); b.configure({'fault':1})
        self.assertEqual(b.battery_w,0); self.assertEqual(b.registers()[0x011F],1)
        b.configure({'fault':0,'stale':True}); before=b.registers(); b.advance(60)
        self.assertEqual(before,b.registers()); b.configure({'stale':False,'readback_mismatch':True})
        self.assertEqual(b.registers()[0x0885],5); self.assertEqual(b.dispatch[5],2)
        b.reboot(); self.assertEqual(b.dispatch[0],0); self.assertEqual(b.boots,1)

    def test_signed_big_endian_register_scaling(self):
        b=Battery(); b.write(0x0880,command(-2000,100)); r=b.registers()
        self.assertEqual(r[0x0102],500); self.assertEqual(r[0x0126],63536)
        self.assertEqual(uint32([r[0x0021],r[0x0022]]),2600)
        self.assertEqual(r[0x0886],250); self.assertEqual(uint32([r[0x0881],r[0x0882]]),30000)

    def test_invalid_scenarios_and_commands(self):
        b=Battery()
        for data in ({'soc':101},{'capacity_wh':0},{'load_w':float('nan')},{'unknown':1}):
            with self.assertRaises(ValueError): b.configure(data)
        with self.assertRaises(ValueError): b.advance(-1)
        with self.assertRaises(ValueError): b.write(0x0880,[1,0,32000,0,0,99,250,0,1])
        b.write(0x0880,[0,0,32000,0,0,0,0,0,0])
        self.assertEqual(b.registers()[0x0885],0)

    def test_hillview_scale_variants(self):
        b=Battery(native_reserve_scale=1,dispatch_soc_scale=0.392)
        self.assertEqual(b.registers()[0x0850],6)
        values=command(-2000,100); values[6]=255
        b.write(0x0880,values); b.advance(3600)
        self.assertAlmostEqual(b.soc,62.5)

    def test_freeze_export_zero_dispatch_power(self):
        b=Battery(pv_w=2000); b.write(0x0880,command(0,50)); b.advance(60)
        self.assertEqual(b.soc,50); self.assertEqual(b.battery_w,0); self.assertEqual(b.grid_w,-1400)

class WireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lab=Lab(); self.server=await asyncio.start_server(self.lab.modbus,'127.0.0.1',0)
        self.port=self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self): self.server.close(); await self.server.wait_closed()

    async def exchange(self,pdu,unit=85):
        reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
        try:
            writer.write(struct.pack('>HHHB',42,0,len(pdu)+1,unit)+pdu); await writer.drain()
            header=await reader.readexactly(7); t,p,n,u=struct.unpack('>HHHB',header)
            self.assertEqual(t,42); self.assertEqual(u,unit)
            return await reader.readexactly(n-1)
        finally: writer.close(); await writer.wait_closed()

    async def test_read_write_roundtrip(self):
        self.assertEqual(await self.exchange(struct.pack('>BHH',3,0x0102,1)),b'\x03\x02\x01\xf4')
        values=command(-2000,100)
        request=struct.pack('>BHHB',16,0x0880,9,18)+struct.pack('>9H',*values)
        self.assertEqual(await self.exchange(request),struct.pack('>BHH',16,0x0880,9))
        self.assertEqual(self.lab.battery.battery_w,-2000)

    async def test_protocol_errors(self):
        self.assertEqual(await self.exchange(struct.pack('>BHH',3,0x0102,1),unit=1),b'\x83\x0b')
        self.assertEqual(await self.exchange(struct.pack('>BHH',3,0x0102,126)),b'\x83\x02')
        self.assertEqual(await self.exchange(struct.pack('>BHH',6,0x0850,60)),b'\x86\x03')
        self.assertEqual(await self.exchange(b'\x11'),b'\x91\x01')

    async def test_unavailable_drops_connection(self):
        self.lab.battery.available=False
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.exchange(struct.pack('>BHH',3,0x0102,1))

    async def test_scenario_http_wire(self):
        api=await asyncio.start_server(self.lab.http,'127.0.0.1',0)
        try:
            reader,writer=await asyncio.open_connection('127.0.0.1',api.sockets[0].getsockname()[1])
            body=json.dumps({'soc':40,'pv_w':1500,'speed':0}).encode()
            writer.write(f'POST /scenario HTTP/1.1\r\nHost: lab\r\nContent-Length: {len(body)}\r\n\r\n'.encode()+body)
            await writer.drain(); result=await reader.read(); writer.close(); await writer.wait_closed()
            header,payload=result.split(b'\r\n\r\n',1)
            self.assertIn(b'200',header); self.assertEqual(json.loads(payload)['soc'],40)
            self.assertEqual(json.loads(payload)['conservation_error_w'],0)
        finally: api.close(); await api.wait_closed()

if __name__=='__main__': unittest.main()
