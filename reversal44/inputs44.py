"""Read only fixed A-class 44 dates. Never bridge a missing/rejected date."""
import gzip
import hashlib
import json
from pathlib import Path
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal as D

MINUTE=60000
FIVE=300000
HOUR=3600000
DAY=86400000


def need(ok, why):
    if not ok:
        raise ValueError(why)


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ms(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)


def json_file(p):
    need(p.is_file() and not p.is_symlink() and p.stat().st_size < 8_000_000, 'Unsafe/oversized input')
    return json.loads(p.read_bytes())


def blocks_for(days):
    groups=[]
    for day in sorted(days):
        if groups and date.fromisoformat(day) == date.fromisoformat(groups[-1][-1])+timedelta(days=1):
            groups[-1].append(day)
        else:
            groups.append([day])
    return groups


def aggregate(bars, step=5):
    need(len(bars)%step == 0, 'Partial aggregation')
    out=[]
    for i in range(0,len(bars),step):
        xs=bars[i:i+step]
        need(xs[0]['t']%(step*MINUTE)==0 and all(b['t']==xs[0]['t']+j*MINUTE for j,b in enumerate(xs)), 'Aggregation gap')
        out.append(dict(t=xs[0]['t'],T=xs[-1]['T'],o=xs[0]['o'],h=str(max(D(b['h']) for b in xs)),
                        l=str(min(D(b['l']) for b in xs)),c=xs[-1]['c'],v=str(sum((D(b['v']) for b in xs),D(0))),n=sum(b['n'] for b in xs)))
    return out


def load(root, index_path):
    root=Path(root).resolve();index=json_file(Path(index_path))
    for path, sha in index['files'].items():
        p=root/path
        need(not Path(path).is_absolute() and '..' not in Path(path).parts and p.resolve().is_relative_to(root), 'Path escape')
        need(p.is_file() and not p.is_symlink() and p.stat().st_size < 8_000_000 and digest_file(p)==sha, 'Pinned file mismatch '+path)
    details=json_file(root/'full-audit/DAILY_DETAILS.json')
    selected=[d['date'] for d in details if d['status']=='PRICE_RESEARCH_CANDIDATE']
    need(selected==index['days'] and len(selected)==44, 'Incorrect A-class selection')
    source=json_file(root/'full-audit/SUMMARY.json')
    need(source['provenance']==index['provenance'], 'Incorrect source run')
    byday={}
    for day,path in index['bars'].items():
        with gzip.open(root/path,'rb') as f:
            b=f.read(1_000_001)
        need(len(b)<=1_000_000,'Oversized decompressed day')
        rows=[json.loads(x) for x in b.splitlines()]
        need(len(rows)==1440,'Incomplete day')
        for i,r in enumerate(rows):
            need(r['t']==ms(day)+i*MINUTE and isinstance(r['t'],int), 'Duplicate/gap/time offset')
            prices=[D(str(r[k])) for k in 'ohlc']
            need(all(v.is_finite() and v>0 for v in prices) and prices[1]==max(prices) and prices[2]==min(prices),'Invalid OHLC')
            need(isinstance(r['n'],int) and r['n']>0 and D(str(r['v'])).is_finite() and D(str(r['v']))>0,'Invalid volume/count')
            for k in 'ohlcv':r[k]=str(r[k])
            r['T']=r['t']+MINUTE-1
        byday[day]=rows
    fund={}
    for path in index['funding']:
        for f in json_file(root/path):
            t=f['time'];need(isinstance(t,int) and f['coin']=='ETH','Bad funding identity')
            r=D(f['fundingRate']);need(r.is_finite() and abs(r)<=D('.04'),'Bad funding rate')
            if t in fund:need(fund[t]==str(r),'Conflicting funding')
            fund[t]=str(r)
    blocks=[]
    for i,days in enumerate(blocks_for(selected)):
        bars=[b for d in days for b in byday[d]];start=bars[0]['t'];end=bars[-1]['T']+1
        rates=[dict(time=t,rate=r) for t,r in sorted(fund.items()) if start<=t<end]
        need([r['time']//HOUR for r in rates]==list(range(start//HOUR,end//HOUR)),'Funding hourly gap or duplicate')
        need(len(bars)==(end-start)//MINUTE,'Block gap')
        blocks.append(dict(id=f'block_{i:02d}',days=days,candles=bars,signals=aggregate(bars),funding=rates,
                           metadata=index['metadata'],start=start+120*FIVE,end=end))
    need(len(blocks)==27 and sum(len(b['candles']) for b in blocks)==63360,'44-day shape changed')
    return blocks, dict(days=selected,blocks=[dict(id=b['id'],days=b['days'],start=b['start'],end=b['end']) for b in blocks],
                       candles=63360,warmup_minutes=27*600,executable_minutes=sum((b['end']-b['start'])//MINUTE for b in blocks),
                       source=index['provenance'],hash_checked_files=len(index['files']),index_sha256=digest_file(index_path))
