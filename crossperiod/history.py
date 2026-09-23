"""Validate and join two saved official ETH minute downloads. No network or orders."""
import hashlib, json
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime, timezone
from inputs44 import aggregate, need, MINUTE, HOUR

ORIGINAL_RUN = '35452535157'
NATIVE_RUN = '35502377024'
ORIGINAL_SHA = '4cc3aa9239383a5a07e4f3305a9b2b17f61c9e57'
NATIVE_SHA = 'e5d8746973ee748a90b05e7aaa91c6287c334a3e'
RAW_DIR = 'experiment/attempts/71e11c05be254544ab7cdc1da3928418/raw'
ENDPOINT = 'https://api.hyperliquid.xyz/info'


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def reject(message): raise ValueError(message)
def pairs(items):
    out = {}
    for k,v in items:
        need(k not in out, 'Duplicate JSON key'); out[k] = v
    return out

def read(path):
    path = Path(path)
    need(path.is_file() and not path.is_symlink() and path.stat().st_size < 12_000_000, 'Unsafe input file')
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=reject)

def child(root, name):
    root = Path(root).resolve(); p = root/name
    need(not Path(name).is_absolute() and '..' not in Path(name).parts and p.resolve().is_relative_to(root), 'Path escape')
    need(not any(x.is_symlink() for x in [p,*p.parents] if x != root.parent), 'Symlink input')
    return p

def verified_logs(folder):
    folder = Path(folder); result = []
    for line in (folder/'requests.jsonl').read_text().splitlines():
        r = json.loads(line, object_pairs_hook=pairs, parse_constant=reject)
        need(r['status']=='OK' and r['endpoint']==ENDPOINT and r['network']=='mainnet', 'Bad request provenance')
        p = child(folder,r['response_file']); need(sha(p)==r['response_sha256'] and p.stat().st_size==r['response_bytes'], 'Raw response hash/size')
        need(r['request']['type'] in ('candleSnapshot','fundingHistory','metaAndAssetCtxs'), 'Unexpected source API')
        result.append((r,read(p)))
    need(result, 'No successful source requests')
    return result

def candle_projection(rows, width=1):
    need(isinstance(rows,list) and rows, 'Empty candle input'); out=[]
    for r in rows:
        need(r['s']=='ETH' and r['i']==f'{width}m', 'Wrong candle market/timeframe')
        t,T = r['t'],r['T']
        need(type(t) is int and type(T) is int and t%(width*MINUTE)==0 and T==t+width*MINUTE-1, 'Invalid candle time')
        p={k:D(str(r[k])) for k in 'ohlcv'}
        need(all(p[k].is_finite() and p[k]>0 for k in 'ohlc') and p['v'].is_finite() and p['v']>=0, 'Invalid price/volume')
        need(p['h']==max(p[k] for k in 'ohlc') and p['l']==min(p[k] for k in 'ohlc'), 'Invalid OHLC')
        need(type(r['n']) is int and r['n']>=0, 'Invalid trade count')
        need((r['n']==0)==(p['v']==0), 'Inconsistent zero volume/count')
        if r['n']==0: need(len({p[k] for k in 'ohlc'})==1, 'Nonflat zero-volume candle')
        out.append(dict(t=t,T=T,**{k:str(p[k]) for k in 'ohlcv'},n=r['n']))
    need(all(b['t']==a['t']+width*MINUTE for a,b in zip(out,out[1:])), 'Gap or duplicate/out of order candles')
    return out

def funding_projection(rows):
    out=[]
    for r in rows:
        need(r['coin']=='ETH' and type(r['time']) is int, 'Wrong funding identity')
        rate=D(str(r['fundingRate']));need(rate.is_finite() and abs(rate)<=D('.04'), 'Invalid funding rate')
        out.append(dict(time=r['time'],rate=str(rate)))
    need(all(a['time']<b['time'] for a,b in zip(out,out[1:])), 'Duplicate/out-of-order funding')
    return out

def extract(logs, interval):
    matches=[(r,x) for r,x in logs if r['request']['type']=='candleSnapshot' and r['request']['req']['interval']==interval]
    need(len(matches)==1,'Missing/duplicate candle response');r,raw=matches[0]
    need(r['request']['req']['coin']=='ETH', 'Wrong request coin')
    result=candle_projection(raw,int(interval[:-1]))
    need(result[-1]['T']<=r['request']['req']['endTime'] and result[-1]['T']<r['received_ms'], 'Unclosed candle')
    return result

def metadata(logs):
    values=[x for r,x in logs if r['request']['type']=='metaAndAssetCtxs'];need(len(values)==1,'Metadata response missing')
    entries=[x for x in values[0][0]['universe'] if x['name']=='ETH'];need(len(entries)==1,'Metadata ETH count')
    e=entries[0];need(e['szDecimals']==4 and e['maxLeverage']==25,'Unsupported saved market precision')
    return dict(sz_decimals=e['szDecimals'],max_leverage=e['maxLeverage'],point_in_time=False)

def same(a,b):
    if isinstance(a,dict):return a.keys()==b.keys() and all(same(v,b[k]) for k,v in a.items())
    if isinstance(a,list):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
    if isinstance(a,str):
        try:return D(a)==D(b)
        except Exception:return a==b
    return a==b

def merge_rows(parts, key):
    out={};overlap=0
    for rows in parts:
        for r in rows:
            k=r[key]
            if k in out:
                need(same(out[k],r),f'Conflicting overlap at {k}');overlap+=1
            else:out[k]=r
    return [out[k] for k in sorted(out)],overlap

def normalize_match(dataset, candles, rates, meta, rich):
    need(dataset['network']=='mainnet' and dataset['coin']=='ETH' and dataset['interval']=='1m', 'Normalized market identity')
    keys=['t','T','o','h','l','c']+(['v','n'] if rich else [])
    projected=[{k:r[k] for k in keys} for r in candles]
    need(same(projected,dataset['candles']) and same(rates,dataset['funding']) and dataset['metadata']==meta,'Normalized differs from raw')

def trim_full_15m(bars):
    need(all(b['t']==a['t']+MINUTE for a,b in zip(bars,bars[1:])), 'Union candle gap')
    start=((bars[0]['t']+15*MINUTE-1)//(15*MINUTE))*(15*MINUTE)
    end=((bars[-1]['T']+1)//(15*MINUTE))*(15*MINUTE)
    kept=[r for r in bars if start<=r['t']<end]
    need(len(kept)>=615 and len(kept)%15==0, 'Insufficient aligned history')
    return kept,dict(leading_minutes=(start-bars[0]['t'])//MINUTE,trailing_minutes=(bars[-1]['T']+1-end)//MINUTE)

def funding_window(rows,start,end):
    selected=[r for r in rows if start<=r['time']<end]
    # Partial first hour has already settled before warmup. Keep actual millisecond offsets.
    expected=list(range((start+HOUR-1)//HOUR,(end-1)//HOUR+1))
    need([r['time']//HOUR for r in selected]==expected,'Missing/duplicate hourly funding in union')
    return selected

def iso(t):return datetime.fromtimestamp(t/1000,timezone.utc).isoformat()

def load(original,native):
    original,native=Path(original),Path(native)
    pins=read(Path(__file__).with_name('source-pins.json'))
    for root,part in ((original,'original'),(native,'native')):
        for name,digest in pins[part].items():need(sha(child(root,name))==digest,'Pinned source file mismatch '+name)
    prov=read(original/'github-provenance.json')
    need(prov['GITHUB_RUN_ID']==ORIGINAL_RUN and prov['GITHUB_SHA']==ORIGINAL_SHA and prov['GITHUB_RUN_ATTEMPT']=='1','Wrong original run')
    ready=read(native/'ready.json');expected=dict(GITHUB_SHA=NATIVE_SHA,GITHUB_RUN_ID=NATIVE_RUN,GITHUB_RUN_ATTEMPT='1')
    need(ready['status']=='READY' and ready['provenance']==expected and read(native/'frozen.json')['provenance']==expected,'Wrong native run')
    manifest=read(native/'manifest.json')
    for name,digest in manifest.items():need(sha(child(native,name))==digest,'Native evidence hash '+name)
    l0=verified_logs(original/RAW_DIR);l1=verified_logs(native/'market/round1');l2=verified_logs(native/'market/round2')
    a,b,b2=extract(l0,'1m'),extract(l1,'1m'),extract(l2,'1m')
    need(len(a)==5000 and len(b)==3000 and same(b,b2),'Minute source count/re-download mismatch')
    metas=[metadata(x) for x in (l0,l1,l2)];need(metas[0]==metas[1]==metas[2],'Metadata mismatch')
    funds=[]
    for logs in (l0,l1,l2):
        fs=[]
        for r,x in logs:
            if r['request']['type']=='fundingHistory':
                need(r['request']['coin']=='ETH','Funding request market')
                fs.extend(funding_projection(x))
        unique,duplicate=merge_rows([fs],'time');need(duplicate==0,'Duplicated funding source pages');funds.append(unique)
    data=read(native/'market/data.json')['1m'];first=b[0]['t'];end=b[-1]['T']+1
    f1=[r for r in funds[1] if first-HOUR<=r['time']<end]
    f2=[r for r in funds[2] if first-HOUR<=r['time']<end]
    need(same(f1,f2),'Repeated funding mismatch')
    normalize_match(read(original/'experiment/dataset.json'),a,funds[0],metas[0],False)
    normalize_match(data,b,f1,metas[1],True)
    joined,overlap=merge_rows([a,b],'t');rates,foverlap=merge_rows([funds[0],f1],'time')
    need(len(joined)==5499 and overlap==2501,'Unexpected saved union')
    bars,trim=trim_full_15m(joined);start=bars[0]['t'];end=bars[-1]['T']+1
    rates=funding_window(rates,start,end)
    fifteen=aggregate(bars,15);raw15=extract(l1,'15m');raw15b=extract(l2,'15m');need(same(raw15,raw15b),'Repeated15m mismatch')
    official={r['t']:r for r in raw15};diff=[]
    for r in fifteen:
        q=official.get(r['t']);need(q is not None,'Official15m coverage missing')
        for k in ('o','h','l','c','v'):
            tolerance=max(D('0.00001') if k=='v' else D('0.000001'),abs(D(q[k]))*(D('0.000001') if k=='v' else D('0.000000001')))
            if abs(D(r[k])-D(q[k]))>tolerance:diff.append(dict(t=r['t'],field=k))
        if r['n']!=q['n']:diff.append(dict(t=r['t'],field='n'))
    need(not diff,'Minute/official15m cross-resolution conflict')
    days=sorted({iso(r['t'])[:10] for r in bars})
    block=dict(id='september_union',days=days,candles=bars,signals=aggregate(bars),funding=rates,metadata=metas[0],start=start+600*MINUTE,end=end)
    checks=dict(status='SAVED_API_INTERNAL_CONSISTENCY',sources=pins['artifacts'],raw_calls_checked=sum(map(len,(l0,l1,l2))),
        input_file_pins=sum(len(pins[k]) for k in ('original','native')),native_manifest_files=len(manifest),
        original_minutes=len(a),native_minutes=len(b),overlap_minutes=overlap,union_minutes=len(joined),**trim,
        input_minutes=len(bars),warmup_minutes=600,execution_window_minutes=len(bars)-600,
        input_start=iso(start),execution_start=iso(block['start']),end_exclusive=iso(end),dates=days,
        funding_records=len(rates),funding_overlap=foverlap,cross_resolution_15m_bars=len(fifteen),
        official_zero_volume_minutes=[iso(r['t']) for r in bars if r['n']==0],
        limitations=['Same API repeated observations, not independent exchange truth','Saved market metadata is not historical point-in-time rules',
                    'Old September prices were used for other strategy research, not a never-seen holdout',
                    'No fresh L2/mark/oracle or live execution evidence'])
    return block,checks
