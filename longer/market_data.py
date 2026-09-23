"""Fixed public ETH inputs. Reuse reviewed TLS/retry transport; never a wallet client."""
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import hashlib
import time

from audit_data import PublicClient, write_json, strict_json, digest

MINUTE=60000
HOUR=3600000
DAY=24*HOUR
INTERVALS={'1m':MINUTE,'5m':5*MINUTE,'15m':15*MINUTE}
END='2026-09-20T00:00:00+00:00'
STARTS={'1m':'2026-09-18T00:00:00+00:00','5m':'2026-09-05T00:00:00+00:00',
        '15m':'2026-08-02T00:00:00+00:00'}

def ms(text):return int(datetime.fromisoformat(text).timestamp()*1000)
def iso(t):return datetime.fromtimestamp(t/1000,timezone.utc).isoformat()
def need(ok,message):
    if not ok:raise ValueError(message)
def number(x):
    need(not isinstance(x,bool),'Boolean is not a number')
    v=D(str(x));need(v.is_finite() and abs(v)<D('1e18'),'Nonfinite/out-of-range value');return v

class Client(PublicClient):
    def __init__(self,evidence,**kwargs):super().__init__('mainnet',evidence,**kwargs)
    @staticmethod
    def validate_body(body):
        need(isinstance(body,dict),'Request object required')
        if body.get('type')=='candleSnapshot':
            need(set(body)=={'type','req'} and isinstance(body['req'],dict),'Candle keys')
            r=body['req']
            need(set(r)=={'coin','interval','startTime','endTime'} and r['coin']=='ETH' and r['interval'] in INTERVALS,'Candle whitelist')
            need(type(r['startTime']) is int and type(r['endTime']) is int and 0<r['startTime']<=r['endTime'],'Time range')
            need(r['endTime']-r['startTime']<5000*INTERVALS[r['interval']],'Max 5000 candles')
        elif body.get('type')=='l2Book':
            need(body=={'type':'l2Book','coin':'ETH'},'Only public ETH book')
        else:PublicClient.validate_body(body)

def metadata(raw):
    need(isinstance(raw,list) and len(raw)==2 and isinstance(raw[0],dict) and isinstance(raw[1],list),'Metadata response')
    u=raw[0].get('universe');need(isinstance(u,list) and len(u)==len(raw[1]),'Context alignment')
    hits=[m for m in u if m.get('name')=='ETH'];need(len(hits)==1,'Unique ETH')
    m=hits[0]
    need(not m.get('isDelisted') and not m.get('onlyIsolated') and m.get('marginMode') not in ('strictIsolated','noCross'),'Unsupported market state')
    sd,ml=m['szDecimals'],m['maxLeverage']
    need(type(sd) is int and 0<=sd<=8 and type(ml) is int and 2<=ml<=100,'Metadata precision')
    return {'sz_decimals':sd,'max_leverage':ml,'point_in_time':False}

def bars(raw,interval,start,end,downloaded_ms):
    dt=INTERVALS[interval]
    need(isinstance(raw,list) and 120<len(raw)<=5000,'Candle count')
    out=[]
    for b in sorted(raw,key=lambda b:b['t']):
        need(isinstance(b,dict) and b.get('s')=='ETH' and b.get('i')==interval,'Wrong candle identity')
        t,T=b['t'],b['T']
        need(type(t) is int and type(T) is int and t%dt==0 and T==t+dt-1,'Candle time')
        need(start<=t<=T<end and T<downloaded_ms-2000,'Unclosed/out-of-range bar')
        p={k:number(b[k]) for k in 'ohlc'}
        need(min(p.values())>0 and p['h']==max(p.values()) and p['l']==min(p.values()),'Invalid OHLC')
        if out:need(t==out[-1]['T']+1,'Gap or duplicate')
        volume=number(b.get('v','0'));need(volume>=0,'Invalid volume')
        n=b.get('n',0);need(type(n) is int and n>=0,'Invalid trade count')
        out.append({'t':t,'T':T,**{k:str(v) for k,v in p.items()},'v':str(volume),'n':n})
    need(out[0]['t']==start and out[-1]['T']==end-1 and len(out)==(end-start)//dt,'Fixed history incomplete; never trim or fill')
    return out

def rates(raw,start,end):
    need(isinstance(raw,list),'Rates list')
    out=[];hours=set()
    for r in sorted(raw,key=lambda r:r['time']):
        t=r['time'];need(type(t) is int and start<=t<end and r.get('coin')=='ETH','Funding identity/range')
        rate=number(r['fundingRate']);need(abs(rate)<=D('.04'),'Funding rate cap')
        need(t//HOUR not in hours,'Duplicate funding hour');hours.add(t//HOUR)
        out.append({'time':t,'rate':str(rate)})
    required=set(range((start+HOUR-1)//HOUR,(end-1)//HOUR+1))
    need(required<=hours,'Missing funding; never zero-fill')
    return out

def download_round(client,end=ms(END),starts=None):
    starts=starts or {k:ms(v)-120*INTERVALS[k] for k,v in STARTS.items()}
    need(end<=client.clock()-2000,'Evaluation end not closed')
    meta=metadata(client.read({'type':'metaAndAssetCtxs'}));c={}
    for interval,start in starts.items():
        request={'type':'candleSnapshot','req':{'coin':'ETH','interval':interval,'startTime':start,'endTime':end-1}}
        c[interval]=bars(client.read(request),interval,start,end,client.clock());client.sleep(1.2)
    low=min(starts.values())-HOUR;cursor=low;rawfund=[]
    while cursor<end:
        finish=min(cursor+7*DAY,end)
        page=client.read({'type':'fundingHistory','coin':'ETH','startTime':cursor,'endTime':finish-1})
        # Each <=168-hour page must be complete, so silent endpoint truncation fails.
        rates(page,cursor,finish)
        rawfund.extend(page);cursor=finish;client.sleep(1.2)
    funding=rates(rawfund,low,end)
    return {interval:{'schema':'longer-eth-history-v1','network':'mainnet','coin':'ETH','interval':interval,
                'downloaded_ms':client.clock(),'source':'DIRECT_HYPERLIQUID_PUBLIC_API',
                'metadata':meta,'candles':cb,'funding':[r for r in funding if r['time']>=cb[0]['t']-HOUR]}
            for interval,cb in c.items()}

def compare(first,second):
    counts={}
    need(set(first)==set(second),'Timeframe mismatch')
    for tf,data in first.items():
        for k in ('schema','network','coin','interval','metadata','candles','funding'):
            need(data[k]==second[tf][k],'Second download differs: '+tf+'/'+k)
        counts[tf]={'bars':len(data['candles']),'funding':len(data['funding'])}
    return {'status':'PASS_SAME_API_SECOND_DOWNLOAD','counts':counts,'second_independent_source':'NOT_VERIFIED'}

def cross_resolution(lower,higher):
    a,b=INTERVALS[lower['interval']],INTERVALS[higher['interval']]
    need(b>a and b%a==0,'Wrong resolutions')
    groups={}
    for r in lower['candles']:groups.setdefault(r['t']//b*b,[]).append(r)
    high={r['t']:r for r in higher['candles']};n=0
    for t,group in groups.items():
        if len(group)!=b//a or t not in high:continue
        need([r['t'] for r in group]==list(range(t,t+b,a)),'Unaligned group')
        expected={'o':D(group[0]['o']),'h':max(D(x['h']) for x in group),
                  'l':min(D(x['l']) for x in group),'c':D(group[-1]['c'])}
        need(all(expected[k]==D(high[t][k]) for k in expected),'Cross-resolution OHLC differs')
        n+=1
    need(n>0,'No complete resolution overlap')
    return {'status':'PASS_SAME_API_CROSS_RESOLUTION_OHLC','groups':n}

def audit_raw(root,data):
    """Check recorded bytes/request identities against each normalized record."""
    root=Path(root);seen_c={};seen_f=[];meta=None;calls=0
    for line in (root/'requests.jsonl').read_text().splitlines():
        item=strict_json(line)
        if item['status']!='OK':continue
        need(item['network']=='mainnet' and item['endpoint']=='https://api.hyperliquid.xyz/info','Raw endpoint mismatch')
        name=item['response_file'];need(Path(name).name==name,'Raw path escape')
        raw=(root/name).read_bytes();need(hashlib.sha256(raw).hexdigest()==item['response_sha256'],'Raw digest mismatch')
        value=strict_json(raw);body=item['request'];Client.validate_body(body);calls+=1
        if body['type']=='candleSnapshot':
            tf=body['req']['interval'];need(tf not in seen_c,'Unexpected repeated candles in round')
            seen_c[tf]=bars(value,tf,body['req']['startTime'],body['req']['endTime']+1,item['received_ms'])
        elif body['type']=='fundingHistory':seen_f.extend(value)
        elif body['type']=='metaAndAssetCtxs':meta=metadata(value)
    need(set(data)==set(seen_c),'Raw frames missing')
    for tf,d in data.items():
        need(d['candles']==seen_c[tf] and d['metadata']==meta,'Projection mismatch')
        low=d['candles'][0]['t']-HOUR;end=d['candles'][-1]['T']+1
        f=[r for r in seen_f if low<=r['time']<end]
        need(rates(f,low,end)==d['funding'],'Funding projection mismatch')
    return {'status':'PASS_RAW_BYTES_TO_NORMALIZED','successful_calls':calls,'exact_L2_oracle':'NOT_VERIFIED'}

def collect(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    first=download_round(Client(root/'round1'));write_json(root/'data.json',first)
    second=download_round(Client(root/'round2'));write_json(root/'second-download.json',second)
    checks={'redownload':compare(first,second),'raw1':audit_raw(root/'round1',first),'raw2':audit_raw(root/'round2',second),
            '1m_to_5m':cross_resolution(first['1m'],first['5m']),'5m_to_15m':cross_resolution(first['5m'],first['15m'])}
    # One current observation only: never use it to fabricate historical spreads.
    try:
        book=Client(root/'current-book').read({'type':'l2Book','coin':'ETH'})
        need(book['coin']=='ETH','Book market')
        bid,ask=number(book['levels'][0][0]['px']),number(book['levels'][1][0]['px'])
        need(0<bid<ask,'Book crossed')
        checks['current_book']={'time':book['time'],'spread_bps':str((ask-bid)/((ask+bid)/2)*10000),
            'use':'CONTEXT_ONLY_NOT_HISTORICAL_CALIBRATION'}
    except Exception as exc:checks['current_book']={'status':'NOT_VERIFIED','error':str(exc)}
    write_json(root/'checks.json',checks)
    return first,checks
