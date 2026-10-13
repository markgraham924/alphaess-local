"""Lab-only Modbus TCP and independent HTTP scenario control, no dependencies."""
import argparse, asyncio, json, struct, time
from model import Battery

class Lab:
    def __init__(self): self.battery=Battery()

    def pdu(self, data):
        fc=data[0]
        try:
            if fc in (3,4):
                address,count=struct.unpack('>HH',data[1:])
                if not 1<=count<=125 or address+count>0x900: return bytes([fc|128,2])
                registers=self.battery.registers()
                return bytes([fc,count*2])+struct.pack('>'+'H'*count,*[registers[i] for i in range(address,address+count)])
            if fc == 6:
                address,value=struct.unpack('>HH',data[1:]); self.battery.write(address,[value]); return data
            if fc == 16:
                address,count,size=struct.unpack('>HHB',data[1:6])
                if count<1 or count>123 or size!=2*count or len(data)!=6+size: return bytes([fc|128,3])
                values=list(struct.unpack('>'+'H'*count,data[6:]))
                self.battery.write(address,values)
                return struct.pack('>BHH',fc,address,count)
            return bytes([fc|128,1])
        except (ValueError,struct.error,IndexError): return bytes([fc|128,3])

    async def modbus(self,reader,writer):
        try:
            while True:
                header=await reader.readexactly(7)
                transaction,protocol,length,unit=struct.unpack('>HHHB',header)
                if protocol or not 2<=length<=254: break
                data=await reader.readexactly(length-1)
                if not self.battery.available: break
                if unit!=85: response=bytes([data[0]|128,11])
                else: response=self.pdu(data)
                writer.write(struct.pack('>HHHB',transaction,0,len(response)+1,unit)+response)
                await writer.drain()
        except (asyncio.IncompleteReadError,ConnectionError): pass
        finally: writer.close(); await writer.wait_closed()

    async def http(self,reader,writer):
        status=200
        try:
            header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),5)
            lines=header.decode().split('\r\n'); method,path,_=lines[0].split()
            headers=dict(line.split(':',1) for line in lines[1:] if ':' in line)
            size=int(next((v for k,v in headers.items() if k.lower()=='content-length'),'0'))
            if size>16384: raise ValueError('Body too large')
            data=json.loads(await reader.readexactly(size)) if size else {}
            if method=='GET' and path in ('/state','/health'): pass
            elif method=='POST' and path=='/scenario': self.battery.configure(data)
            elif method=='POST' and path=='/advance': self.battery.advance(float(data['seconds']))
            elif method=='POST' and path=='/reboot': self.battery.reboot()
            elif method=='POST' and path=='/reset': self.battery=Battery()
            else: status=404; raise ValueError('Unknown endpoint')
            result=self.battery.state()
        except Exception as error: status=400 if status==200 else status; result={'error':str(error)}
        body=json.dumps(result).encode()
        writer.write(f'HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body)
        await writer.drain(); writer.close(); await writer.wait_closed()

    async def tick(self):
        last=time.monotonic()
        while True:
            await asyncio.sleep(0.1)
            now=time.monotonic(); self.battery.advance((now-last)*self.battery.speed); last=now

async def main(args):
    lab=Lab()
    modbus=await asyncio.start_server(lab.modbus,args.host,args.modbus_port)
    http=await asyncio.start_server(lab.http,args.host,args.api_port)
    print(json.dumps({'simulator':'alphaess-lab','host':args.host,'modbus':args.modbus_port,'api':args.api_port,'unit':85}),flush=True)
    async with modbus,http: await asyncio.gather(modbus.serve_forever(),http.serve_forever(),lab.tick())

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--modbus-port',type=int,default=1502); parser.add_argument('--api-port',type=int,default=1503)
    asyncio.run(main(parser.parse_args()))
