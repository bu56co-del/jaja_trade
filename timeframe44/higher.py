"""Read-only ETH4h context and causal multi-timeframe decisions, no order API."""
from bisect import bisect_right
from decimal import Decimal as D
from pathlib import Path
import hashlib,json,time,urllib.request
from inputs44 import need,aggregate
import filters_bb as old

H4=14400000
MINUTE=60000
BASE_FILTER=dict(id='BB15_NONE_ROOM_LOWER_HALF',trend='NONE',room='ROOM',body='LOWER_HALF')
BASELINE='NONE_HARD_360'
PRIMARY='DOWN_ONLY_MID15_240'
EPS=D('1e-12')


def specs():
    return [dict(id=f'{trend}_{exit}_{hold}',trend=trend,exit=exit,hold=hold)
            for trend in ('NONE','UP_VETO','DOWN_ONLY') for exit in ('HARD','MID15') for hold in (360,240)]


def dump(path,x):path.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(raw,start,end):
    need(isinstance(raw,list) and 0<len(raw)<=5000,'Missing/oversized 4h list')
    rows=[]
    for b in raw:
        need(isinstance(b,dict) and all(k in b for k in ('t','T','s','i','o','h','l','c','v','n')),'4h schema')
        need(type(b['t']) is int and type(b['T']) is int and b['t']%H4==0 and b['T']==b['t']+H4-1,'4h timestamp')
        need(b['s']=='ETH' and b['i']=='4h','Wrong market or interval')
        vals={k:D(str(b[k])) for k in 'ohlcv'}
        need(all(x.is_finite() for x in vals.values()) and min(vals[k] for k in 'ohlc')>0,'4h finite/positive prices')
        need(vals['h']>=max(vals['o'],vals['c']) and vals['l']<=min(vals['o'],vals['c']) and vals['h']>=vals['l'] and vals['v']>=0,'4h OHLC/volume')
        need(type(b['n']) is int and b['n']>=0,'4h count')
        if start<=b['t']<end:rows.append(dict(t=b['t'],T=b['T'],s='ETH',i='4h',**{k:str(v) for k,v in vals.items()},n=b['n']))
    rows.sort(key=lambda b:b['t'])
    need([b['t'] for b in rows]==list(range(start,end,H4)),'4h missing/duplicate/gap history')
    return rows


def request(out,number,start,end):
    url='https://api.hyperliquid.xyz/info'
    payload=dict(type='candleSnapshot',req=dict(coin='ETH',interval='4h',startTime=start,endTime=end-1))
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('Market POST redirect refused')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Accept-Encoding':'identity','User-Agent':'A44-4h-context/1.0'})
    last=None
    for attempt in range(3):
        try:
            with opener.open(req,timeout=25) as response:
                need(response.status==200 and response.geturl()==url,'Market response')
                raw=response.read(4*1024*1024+1)
                need(len(raw)<=4*1024*1024,'4h download cap')
            p=out/f'raw-{number}.json';p.write_bytes(raw)
            dump(out/f'request-{number}.json',dict(url=url,payload=payload,sha256=digest(p),bytes=len(raw),received_ms=time.time_ns()//1000000))
            return normalize(json.loads(raw),start,end)
        except Exception as exc:
            last=exc
            if attempt<2:time.sleep(2*(attempt+1))
    raise RuntimeError('Official4h request failed: '+type(last).__name__) from last


def check_overlap(rows,blocks):
    by={b['t']:b for b in rows};compared=0;mismatch=[]
    for block in blocks:
        for a in aggregate(block['candles'],240):
            b=by[a['t']];wrong=[]
            for k in 'ohlc':
                if abs(D(a[k])-D(b[k]))>max(D('0.000001'),abs(D(b[k]))*D('1e-9')):wrong.append(k)
            if abs(D(a['v'])-D(b['v']))>max(D('.00001'),abs(D(b['v']))*D('1e-6')):wrong.append('v')
            if a['n']!=b['n']:wrong.append('n')
            if wrong:mismatch.append(dict(t=a['t'],fields=wrong,derived=a,official=b))
            compared+=1
    return dict(compared=compared,mismatches=mismatch)


def prepare(blocks,out):
    out.mkdir(parents=True,exist_ok=False)
    start=min(b['candles'][0]['t'] for b in blocks)-120*H4
    end=max(b['end'] for b in blocks)
    a=request(out,1,start,end);time.sleep(2);b=request(out,2,start,end)
    need(a==b,'Official4h redownload mismatch')
    overlap=check_overlap(a,blocks);dump(out/'overlap.json',overlap)
    need(overlap['compared']==264 and not overlap['mismatches'],'4h vs A44 cross-check failed')
    dump(out/'candles.json',a)
    info=dict(start_ms=start,end_ms=end,rows=len(a),warmup_context_bars=120,redownload_equal=True,compared_a44_4h=264,
              candles_sha256=digest(out/'candles.json'),raw_sha256=[digest(out/f'raw-{k}.json') for k in (1,2)])
    dump(out/'summary.json',info);return a,info


class Context:
    def __init__(self,rows):
        need(len(rows)>=120 and all(b['t']==rows[0]['t']+k*H4 and b['T']==b['t']+H4-1 for k,b in enumerate(rows)),'Context gaps/order')
        self.rows=rows;self.ends=[b['T'] for b in rows];self.cache={}
    def at(self,decision_ms):
        # At15:59:59.999, a bar ending16:00 is unavailable. At16:00 it is closed.
        n=bisect_right(self.ends,decision_ms-1)
        need(n>=120,'Not enough CLOSED4h warmup')
        if n not in self.cache:
            part=self.rows[n-120:n];c=[D(b['c']) for b in part];fast=old.ema(c,9);slow=old.ema(c,21)
            tr=[max(D(b['h'])-D(b['l']),abs(D(b['h'])-c[j-1]),abs(D(b['l'])-c[j-1])) for j,b in enumerate(part) if j]
            self.cache[n]=dict(last_closed_ms=part[-1]['T'],fast=fast[-1],slow=slow[-1],rise1=fast[-1]-fast[-2],rise4=fast[-1]-fast[-5],atr=sum(tr[-14:])/14)
        f=self.cache[n];need(f['last_closed_ms']<decision_ms and decision_ms-f['last_closed_ms']<=H4,'Stale4h context')
        return f


def allow(f,mode):
    if mode=='NONE':return True
    if mode=='UP_VETO':return not(f['fast']-f['slow']>EPS and f['rise4']-D('.5')*f['atr']>EPS)
    if mode=='DOWN_ONLY':return f['fast']-f['slow']<=EPS and f['rise1']<=EPS
    raise ValueError('Unknown4h filter')


def choices(block,s,base,extras,context):
    need(s in specs(),'Unfrozen definition');out={};events=[]
    for j,c in base.items():
        d=c['direction'];f=None;ok=True
        if d:
            f=context.at(block['candles'][j]['t']);ok=allow(f,s['trend'])
            events.append(dict(index=j,decision_ms=block['candles'][j]['t'],allowed=ok,context={k:str(v) for k,v in f.items()}))
            if not ok:d=0
        exit_short=bool(s['exit']=='MID15' and j in extras and c['close']<=extras[j]['mean'])
        out[j]=dict(c,direction=d,exit_long=False,exit_short=exit_short)
    return out,events
