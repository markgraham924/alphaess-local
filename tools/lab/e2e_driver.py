"""Explicit lab HA -> controller -> network simulator evidence driver.

Does not create config entries or claim a Predbat calculation has executed.
Predbat planner assertions can consume the same state/writes after a real run.
"""
import argparse,json,os,socket,struct,time,urllib.request,urllib.parse
from datetime import datetime,timezone

def request(url,method='GET',data=None,token=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)

def run(args):
    ha=args.ha_url.rstrip('/'); sim=args.sim_url.rstrip('/')
    for url in (ha,sim):
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname:
            raise ValueError('Only explicitly designated lab endpoints are permitted')
    if not args.confirm_isolated_lab: raise ValueError('--confirm-isolated-lab required')
    if args.token_file:
        with open(args.token_file,encoding='utf-8') as source: token=source.read().strip()
    else: token=os.environ[args.token_env]
    initial=request(sim+'/health')
    if not {'dispatch','boots','conservation_error_w','writes'}<=set(initial): raise ValueError('Not lab simulator')
    report={'started':datetime.now(timezone.utc).isoformat(),'checks':[],
            'scope':'HA controller service to actual Modbus simulator; planner integration requires separate Predbat calculation evidence'}
    def check(name,condition,details=None):
        report['checks'].append({'name':name,'passed':bool(condition),'evidence':details})
        if not condition: raise AssertionError(name)
    def state(): return request(sim+'/state')
    def scenario(**fields): return request(sim+'/scenario','POST',fields)
    def service(domain,name,data=None): return request(ha+'/api/services/'+domain+'/'+name,'POST',data or {},token)
    def wait(name,predicate,timeout=45):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            current=state()
            if predicate(current): check(name,True,current); return current
            time.sleep(1)
        check(name,False,state())
    def command(name,**fields): return service(args.domain,name,fields)
    def wait_off(name):
        until=time.monotonic()+60
        while time.monotonic()<until:
            entity=request(ha+'/api/states/'+args.control_entity,token=token)
            if entity['state']=='off': check(name,True,{'state':'off'}); return
            time.sleep(2)
        check(name,False,{'state':entity['state']})
    def foreign_dispatch(power,target):
        # Independent raw Modbus client represents another network controller.
        # This endpoint derives ONLY from the confirmed lab simulator URL.
        words=[1,0,32000+power,0,0,2,round(target/0.4),0,90]
        pdu=struct.pack('>BHHB9H',16,0x0880,9,18,*words)
        with socket.create_connection((urllib.parse.urlparse(sim).hostname,args.modbus_port),timeout=10) as connection:
            connection.sendall(struct.pack('>HHHB',987,0,len(pdu)+1,85)+pdu)
            reply=b''
            while len(reply)<12:
                part=connection.recv(12-len(reply))
                if not part: raise ConnectionError('Foreign lab dispatch not acknowledged')
                reply+=part
            if reply[7:]!=struct.pack('>BHH',16,0x0880,9): raise ValueError('Foreign lab dispatch failed')
    try:
        # User/parent must first configure the lab integration with this simulator.
        service('switch','turn_off',{'entity_id':args.control_entity})
        request(sim+'/reset','POST',{})
        scenario(speed=0,soc=50,load_w=600,pv_w=0)
        time.sleep(args.settle)
        baseline=len(state()['writes'])
        try: command('force_charge',power_w=2000,target_soc=80,duration_minutes=10)
        except Exception: pass # A disabled controller can reject the service explicitly.
        time.sleep(3)
        check('disabled controller sends no dispatch',len(state()['writes'])==baseline)
        service('switch','turn_on',{'entity_id':args.control_entity})
        command('force_charge',power_w=2000,target_soc=80,duration_minutes=10)
        wait('charge reaches inverter',lambda s:s['dispatch'][0]==1 and s['dispatch'][5]==2 and s['battery_w']<0)
        before=state()['soc']; request(sim+'/advance','POST',{'seconds':120})
        check('charge increases SoC',state()['soc']>before)
        service('switch','turn_off',{'entity_id':args.control_entity})
        wait('disable stops owned dispatch',lambda s:s['dispatch'][0]==0)
        scenario(soc=60,pv_w=400,load_w=600,native_floor_pct=25)
        time.sleep(args.settle)
        service('switch','turn_on',{'entity_id':args.control_entity})
        command('force_export',power_w=2000,target_soc=30,duration_minutes=10)
        wait('discharge exports surplus',lambda s:s['dispatch'][0]==1 and s['battery_w']>0 and s['grid_w']<0)
        check('higher native reserve preserved',state()['native_floor_pct']==25)
        check('controller never writes native reserve',all(w['address']!=0x0850 for w in state()['writes']))
        command('stop')
        wait('explicit stop releases dispatch',lambda s:s['dispatch'][0]==0)
        command('force_export',power_w=2000,target_soc=30,duration_minutes=10)
        wait('second export starts',lambda s:s['dispatch'][0]==1 and s['battery_w']>0)
        scenario(fault=1)
        wait('fault removes dispatch',lambda s:s['dispatch'][0]==0,timeout=60)
        check('fault stops physical battery',state()['battery_w']==0)
        scenario(fault=0,soc=60,pv_w=2000,load_w=600)
        time.sleep(args.settle)
        service('switch','turn_on',{'entity_id':args.control_entity})
        command('freeze_charge',target_soc=100,duration_minutes=10)
        wait('mode19 reaches inverter and blocks charging',lambda s:s['dispatch'][0]==1 and s['dispatch'][5]==19 and s['battery_w']==0)
        command('stop')
        wait('freeze charge stop',lambda s:s['dispatch'][0]==0)
        command('freeze_export',target_soc=60,duration_minutes=10)
        wait('freeze export holds battery with mode2 zero power',lambda s:s['dispatch'][0]==1 and s['dispatch'][5]==2 and s['battery_w']==0)
        command('stop'); wait('freeze export stop',lambda s:s['dispatch'][0]==0)
        scenario(pv_w=0,load_w=600)
        time.sleep(args.settle)
        command('force_charge',power_w=2000,target_soc=80,duration_minutes=10)
        wait('charge before mismatch',lambda s:s['dispatch'][0]==1 and s['battery_w']<0)
        scenario(readback_mismatch=True)
        wait('mismatch releases owned dispatch',lambda s:s['dispatch'][0]==0,timeout=60)
        scenario(readback_mismatch=False)
        time.sleep(args.settle)
        service('switch','turn_on',{'entity_id':args.control_entity})
        command('force_charge',power_w=2000,target_soc=80,duration_minutes=10)
        wait('charge before telemetry loss',lambda s:s['dispatch'][0]==1 and s['battery_w']<0)
        scenario(available=False)
        # Physical lease expiration is independent of HA's clock and its errors.
        request(sim+'/advance','POST',{'seconds':91})
        check('deadman expires without communication',state()['dispatch'][0]==0)
        time.sleep(args.settle+10)
        scenario(available=True)
        # HA backs off unavailable coordinator polling; fixed12sec is too short.
        # Require observed recovery AND disarming before issuing another action.
        until=time.monotonic()+90; entity=None; recovery_states=[]
        while time.monotonic()<until:
            entity=request(ha+'/api/states/'+args.control_entity,token=token)
            recovery_states.append(entity['state'])
            if entity['state']=='off': break
            time.sleep(2)
        check('telemetry failure disables controller',entity and entity['state']=='off',
              {'entity_id':args.control_entity,'state':entity['state'] if entity else None,
               'observed_recovery_states':recovery_states})
        service('switch','turn_on',{'entity_id':args.control_entity})
        command('force_charge',power_w=2000,target_soc=80,duration_minutes=10)
        wait('charge before inverter reboot',lambda s:s['dispatch'][0]==1 and s['battery_w']<0)
        request(sim+'/reboot','POST',{})
        time.sleep(args.settle)
        check('inverter reboot is not overwritten',state()['dispatch'][0]==0)
        service('switch','turn_off',{'entity_id':args.control_entity})
        scenario(soc=60,pv_w=0,load_w=600)
        time.sleep(args.settle)
        service('switch','turn_on',{'entity_id':args.control_entity})
        foreign_dispatch(2000,20)
        baseline=len(state()['writes'])
        wait_off('foreign dispatch after idle arming disarms controller')
        check('foreign idle command not stopped or overwritten',state()['dispatch'][0]==1 and len(state()['writes'])==baseline)
        command('stop'); wait('explicit user stop can release foreign dispatch',lambda s:s['dispatch'][0]==0)
        service('switch','turn_on',{'entity_id':args.control_entity})
        foreign_dispatch(-2000,80)
        baseline=len(state()['writes']); rejected=False
        try: command('force_charge',power_w=2000,target_soc=80,duration_minutes=10)
        except Exception: rejected=True
        check('fresh planner request refuses matching foreign dispatch',rejected and len(state()['writes'])==baseline)
        wait_off('foreign request rejection disarms controller')
        command('stop'); wait('explicit user stop clears matching foreign dispatch',lambda s:s['dispatch'][0]==0)
        if args.restart_ha:
            baseline=len(state()['writes'])
            try: service('homeassistant','restart')
            except Exception: pass # HA may close request during its authorized lab restart.
            until=time.monotonic()+120; recovered=False
            time.sleep(10)
            while time.monotonic()<until:
                try:
                    entity=request(ha+'/api/states/'+args.control_entity,token=token)
                    if entity['state']=='off': recovered=True; break
                except Exception: pass
                time.sleep(2)
            check('HA restart returns with control disabled',recovered)
            check('HA restart produces no inverter writes',len(state()['writes'])==baseline)
        check('conservation maintained',state()['conservation_error_w']==0)
        # Prevent controller renewal; disable clears it, then direct simulator expiry
        # is covered by wire/model tests rather than mislabelled as a HA assertion.
        report['passed']=True
    except Exception as error:
        report['passed']=False; report['failure']=type(error).__name__+': '+str(error)
        raise
    finally:
        try: service('switch','turn_off',{'entity_id':args.control_entity})
        except Exception: pass
        report['finished']=datetime.now(timezone.utc).isoformat()
        if args.report:
            with open(args.report,'w',encoding='utf-8') as out: json.dump(report,out,indent=2)
        print(json.dumps({'passed':report.get('passed',False),'checks':len(report['checks']),'report':args.report}))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--ha-url',required=True); parser.add_argument('--sim-url',required=True)
    parser.add_argument('--confirm-isolated-lab',action='store_true')
    parser.add_argument('--token-env',default='ALPHAESS_LAB_HA_TOKEN')
    parser.add_argument('--token-file')
    parser.add_argument('--domain',default='alphaess_local')
    parser.add_argument('--control-entity',default='switch.alphaess_control_enabled')
    parser.add_argument('--settle',type=float,default=12)
    parser.add_argument('--report',default='ha-controller-e2e.json')
    parser.add_argument('--restart-ha',action='store_true',help='Restart only the explicitly designated lab HA')
    parser.add_argument('--modbus-port',type=int,default=1502)
    run(parser.parse_args())
