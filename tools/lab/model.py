"""Original deterministic AlphaESS protocol test double; not a hardware emulator."""
from dataclasses import dataclass, field, asdict
import math

def words32(value):
    value = int(value) & 0xFFFFFFFF
    return [value >> 16, value & 0xFFFF]

def uint32(words):
    return words[0] * 65536 + words[1]

@dataclass
class Battery:
    capacity_wh: float = 16000
    soc: float = 50
    load_w: float = 600
    pv_w: float = 0
    max_power_w: float = 3680
    native_floor_pct: float = 6
    native_reserve_scale: float = 0.1
    dispatch_soc_scale: float = 0.4
    available: bool = True
    stale: bool = False
    readback_mismatch: bool = False
    fault: int = 0
    warning: int = 0
    speed: float = 1
    elapsed: float = 0
    battery_w: float = 0
    grid_w: float = 600
    import_kwh: float = 0
    export_kwh: float = 0
    charge_kwh: float = 0
    discharge_kwh: float = 0
    pv_kwh: float = 0
    dispatch: list = field(default_factory=lambda: [0,0,32000,0,0,5,250,0,0])
    writes: list = field(default_factory=list)
    expires_at: float = 0
    boots: int = 0
    _frozen: dict | None = None

    def configure(self, changes):
        allowed = {'capacity_wh','soc','load_w','pv_w','max_power_w','native_floor_pct',
                   'native_reserve_scale','dispatch_soc_scale','available','stale',
                   'readback_mismatch','fault','warning','speed'}
        if not set(changes) <= allowed:
            raise ValueError('Unknown scenario field')
        positive = {'capacity_wh','max_power_w','native_reserve_scale','dispatch_soc_scale'}
        for key, value in changes.items():
            if key in {'available','stale','readback_mismatch'}:
                if not isinstance(value, bool): raise ValueError('Boolean required')
            elif not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError('Finite numeric value required')
            if key in positive and value <= 0: raise ValueError('Positive required')
            if key in {'soc','native_floor_pct'} and not 0 <= value <= 100: raise ValueError('SoC out of range')
            if key in {'load_w','pv_w','speed','fault','warning'} and value < 0: raise ValueError('Negative value')
        if changes.get('stale') and not self.stale: self._frozen = self.registers()
        for key,value in changes.items(): setattr(self,key,value)
        if not self.stale: self._frozen = None
        self.advance(0)

    def advance(self, seconds):
        if not math.isfinite(seconds) or seconds < 0 or seconds > 86400: raise ValueError('Invalid duration')
        # One-second substeps preserve expiry and SoC crossing behavior.
        remaining = seconds
        while remaining > 0:
            step = min(remaining, 1)
            self._step(step)
            remaining -= step
        if seconds == 0: self._step(0)

    def _step(self, seconds):
        if self.dispatch[0] and self.elapsed >= self.expires_at: self.dispatch[0] = 0
        active = bool(self.dispatch[0])
        mode = self.dispatch[5] if active else 5
        power = uint32(self.dispatch[1:3]) - 32000
        target = self.dispatch[6] * self.dispatch_soc_scale
        if self.fault: battery = 0
        elif active and mode == 2:
            battery = max(-self.max_power_w,min(self.max_power_w,power))
            if battery > 0 and self.soc <= max(target,self.native_floor_pct): battery = 0
            if battery < 0 and self.soc >= target: battery = 0
        else:
            battery = max(-self.max_power_w,min(self.max_power_w,self.load_w-self.pv_w))
            if mode == 19 and battery < 0: battery = 0
        floor = max(self.native_floor_pct, target if active and mode == 2 and battery > 0 else 0)
        ceiling = min(100,target if active and mode == 2 and battery < 0 else 100)
        if seconds:
            battery = min(battery,max(0,(self.soc-floor)/100*self.capacity_wh*3600/seconds)) if battery>0 else battery
            battery = max(battery,-max(0,(ceiling-self.soc)/100*self.capacity_wh*3600/seconds)) if battery<0 else battery
        elif (battery > 0 and self.soc <= floor) or (battery < 0 and self.soc >= ceiling): battery = 0
        self.battery_w = battery
        self.grid_w = self.load_w-self.pv_w-battery
        kwh_factor = seconds / 3600000
        self.import_kwh += max(0,self.grid_w)*kwh_factor
        self.export_kwh += max(0,-self.grid_w)*kwh_factor
        self.charge_kwh += max(0,-battery)*kwh_factor
        self.discharge_kwh += max(0,battery)*kwh_factor
        self.pv_kwh += self.pv_w*kwh_factor
        self.soc = min(100,max(0,self.soc-battery*kwh_factor*100000/self.capacity_wh))
        self.elapsed += seconds
        if self.dispatch[0] and self.elapsed >= self.expires_at: self.dispatch[0] = 0

    def write(self,address,values):
        if not values or any(not isinstance(v,int) or not 0<=v<=65535 for v in values): raise ValueError('Invalid words')
        if not (0x0880 <= address and address+len(values)<=0x0889):
            # Reserve deliberately read-only: tests must prove it is preserved.
            raise ValueError('Write outside dispatch block')
        candidate = self.dispatch.copy()
        start = address-0x0880
        candidate[start:start+len(values)] = values
        if candidate[0] not in (0,1) or (candidate[0] and candidate[5] not in (2,5,19)): raise ValueError('Unsupported dispatch')
        if candidate[6]*self.dispatch_soc_scale > 100.01: raise ValueError('Invalid target')
        self.dispatch = candidate
        if candidate[0]: self.expires_at = self.elapsed + uint32(candidate[7:9])
        self.writes.append({'time':self.elapsed,'address':address,'values':values.copy()})
        self.writes[:] = self.writes[-200:]
        self.advance(0)

    def reboot(self):
        self.dispatch[0] = 0
        self.boots += 1
        self.advance(0)

    def registers(self):
        if self.stale and self._frozen is not None: return self._frozen.copy()
        out = {i:0 for i in range(0x0900)}
        def put(address,value):
            for i,v in enumerate(words32(value)): out[address+i]=v
        out[0x0100]=520; out[0x0101]=int(self.battery_w/52*10)&65535
        out[0x0102]=round(self.soc*10); out[0x0119]=round(self.capacity_wh/100)
        out[0x011B]=1000; out[0x0126]=round(self.battery_w)&65535
        out[0x012C]=out[0x012D]=round(self.max_power_w)
        put(0x0010,round(self.export_kwh*100)); put(0x0012,round(self.import_kwh*100))
        put(0x0021,round(self.grid_w)); put(0x0120,round(self.charge_kwh*10)); put(0x0122,round(self.discharge_kwh*10))
        put(0x040C,round(self.pv_w+self.battery_w)); put(0x041F,round(self.pv_w)); put(0x0453,round(self.pv_w))
        put(0x08D2,round(self.pv_kwh*10)); put(0x043E,round(self.pv_kwh*10)); put(0x011C,self.warning); put(0x011E,self.fault)
        put(0x043A,self.fault); put(0x08D4,self.fault)
        out[0x0440]=2 if self.dispatch[0] else 1
        out[0x0850]=round(self.native_floor_pct/self.native_reserve_scale)
        out[0x0855]=100; out[0x041C]=5000
        for i,v in enumerate(self.dispatch): out[0x0880+i]=v
        remaining=math.ceil(max(0,self.expires_at-self.elapsed)) if self.dispatch[0] else 0
        put(0x0887,remaining)
        if self.readback_mismatch and self.dispatch[0]: out[0x0885]=5
        return out

    def state(self):
        result = asdict(self); result.pop('_frozen')
        result['conservation_error_w']=self.load_w-self.pv_w-self.battery_w-self.grid_w
        return result
