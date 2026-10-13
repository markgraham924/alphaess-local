"""Run candidate controller + actual pymodbus transport against loopback server.

This is an independent protocol-controller check, without HA or planner runtime.
"""
import argparse,asyncio,importlib.util,json,pathlib,sys,time
from server import Lab

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec); sys.modules[name]=module
    spec.loader.exec_module(module); return module

async def run(args):
    source=pathlib.Path(args.integration)
    protocol=load('lab_protocol',source/'protocol.py')
    transport_module=load('lab_transport',source/'transport.py')
    lab=Lab(); server=await asyncio.start_server(lab.modbus,'127.0.0.1',0)
    transport=transport_module.Transport('127.0.0.1',server.sockets[0].getsockname()[1],85)
    controller=protocol.Controller(transport,protocol.Limits(capacity=10,charge_power=2500,discharge_power=2500))
    checks=[]
    def check(name,condition):
        checks.append({'name':name,'passed':bool(condition)})
        if not condition: raise AssertionError(name)
    try:
        data=await controller.poll()
        check('first poll is passive',not lab.battery.writes and not controller.enabled)
        check('load independently conserved',data['load_power']==600)
        await controller.enable(); await controller.request('charge',power=2000,target_soc=80,duration=600)
        check('actual client wire charges and imports',lab.battery.battery_w==-2000 and lab.battery.grid_w==2600)
        lab.battery.advance(30); check('battery physics increases SoC',lab.battery.soc>50)
        # Accelerated physical time can expire90sec lease; stop clears ownership explicitly.
        await controller.request('stop')
        check('stop acknowledged and read back',not lab.battery.dispatch[0])
        lab.battery.configure({'soc':60,'native_floor_pct':25,'pv_w':400})
        await controller.request('export',power=2000,target_soc=10,duration=600)
        check('higher native reserve applied',lab.battery.dispatch[6]*0.4>=25)
        check('export independent net grid flow',lab.battery.battery_w==2000 and lab.battery.grid_w==-1800)
        lab.battery.configure({'fault':1}); await controller.poll()
        check('fault disables and stops',not controller.enabled and not lab.battery.dispatch[0])
        lab.battery.configure({'fault':0,'pv_w':2000}); await controller.enable()
        await controller.request('freeze_charge',target_soc=100,duration=600)
        check('mode19 blocks charging while exports PV',lab.battery.dispatch[5]==19 and lab.battery.battery_w==0 and lab.battery.grid_w==-1400)
        await controller.request('stop'); await controller.request('freeze_export',target_soc=60,duration=600)
        check('zero power mode2 freezes battery',lab.battery.dispatch[5]==2 and lab.battery.battery_w==0)
        lab.battery.configure({'readback_mismatch':True}); await controller.poll()
        check('mismatch safe stop',not controller.enabled and not lab.battery.dispatch[0])
        check('native register never written',all(w['address']!=0x850 for w in lab.battery.writes))
        return {'passed':True,'checks':checks,'scope':'candidate controller with actual pymodbus TCP, loopback, without HA/Predbat'}
    finally:
        transport.close(); server.close(); await server.wait_closed()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--integration',required=True); parser.add_argument('--report')
    args=parser.parse_args(); result=asyncio.run(run(args))
    if args.report:
        pathlib.Path(args.report).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
