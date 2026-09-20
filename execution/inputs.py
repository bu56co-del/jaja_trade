"""Read pinned public artifacts, never access a network or account."""
import gzip
import hashlib
from pathlib import Path
from audit_data import strict_json, digest
import market_data as md

PRIOR_RUN='35502377024'
PRIOR_SHA='e5d8746973ee748a90b05e7aaa91c6287c334a3e'
DATA_SHA='1038f92f4e114abca0ee489455e67866755ee622354d7af4a2d1a878ad3c5db6'
FROZEN_FILE_SHA='0ff4dcc5f9a04f0e9cd538b8b4df24db01db190a4e3cbcd27cfbae363fa7d486'
START=1789689600000
END=1789862400000


def read(path):
    path=Path(path)
    md.need(path.is_file() and not path.is_symlink() and path.stat().st_size<40_000_000,'Invalid input file')
    return strict_json(path.read_bytes())


def manifest(root):
    root=Path(root)
    files=read(root/'manifest.json')
    md.need(isinstance(files,dict) and 1<=len(files)<1000,'Invalid manifest')
    for name,h in files.items():
        p=Path(name)
        md.need(not p.is_absolute() and '..' not in p.parts,'Manifest traversal')
        p=root/p
        md.need(p.resolve().is_relative_to(root.resolve()) and not p.is_symlink(),'Manifest symlink/escape')
        md.need(p.is_file() and p.stat().st_size<40_000_000,'Invalid manifest file')
        md.need(hashlib.sha256(p.read_bytes()).hexdigest()==h,'Manifest hash mismatch '+name)
    return len(files)


def validate_pair(data,start=START,end=END):
    for tf in ('1m','15m'):
        d=data[tf];dt=md.INTERVALS[tf];rows=d['candles']
        md.need(d['coin']=='ETH' and d['network']=='mainnet' and d['interval']==tf,'Market/network mismatch')
        md.need(len(rows)>120 and all(b['t']%dt==0 and b['T']==b['t']+dt-1 for b in rows),'Bar boundaries')
        md.need(all(b['t']==rows[0]['t']+i*dt for i,b in enumerate(rows)),'Candle gap/duplicate')
        for b in rows:
            p=[md.number(b[k]) for k in 'ohlc']
            md.need(min(p)>0 and md.number(b['h'])==max(p) and md.number(b['l'])==min(p),'OHLC invalid')
        md.need(rows[0]['t']<=start and rows[-1]['T']>=end-1,'Execution window incomplete')
        md.need(rows[-1]['T']<d['downloaded_ms']-2000,'Unclosed bar')
        fund=d['funding'];times=[r['time'] for r in fund]
        md.need(times==sorted(set(times)),'Funding times')
        md.need(len({t//md.HOUR for t in times})==len(times),'Duplicate funding hour')
        md.need(set(range(start//md.HOUR,end//md.HOUR))<={t//md.HOUR for t in times},'Missing funding')
        md.need(all(abs(md.number(r['rate']))<=md.number('.04') for r in fund),'Funding cap')
    md.need(data['1m']['metadata']==data['15m']['metadata'],'Mismatched metadata')
    md.need(data['15m']['candles'][0]['t']<=start-120*900000,'Missing 15m warmup')
    return md.cross_resolution(data['1m'],data['15m'])


def load_inputs(prior,prior_final,code):
    prior,prior_final,code=map(Path,(prior,prior_final,code))
    counts={'prepared':manifest(prior),'final':manifest(prior_final)}
    for root, expected in ((prior,'f5c1c5d826a98976ec1d718a5b951eea64727ae0d2c27885597fc8501dac862b'),
                           (prior_final,'29b289f638770ac8a61126229de2c3bbba72eb3fbddf76c7db355ab9a2ccfc29')):
        md.need(hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()==expected,'Wrong pinned manifest')
    md.need(hashlib.sha256((prior/'frozen.json').read_bytes()).hexdigest()==FROZEN_FILE_SHA,'Wrong prior freeze')
    frozen=read(prior/'frozen.json')
    md.need(frozen['provenance']==dict(GITHUB_SHA=PRIOR_SHA,GITHUB_RUN_ID=PRIOR_RUN,GITHUB_RUN_ATTEMPT='1'),'Wrong prior run')
    for name,h in frozen['sources'].items():
        md.need('..' not in Path(name).parts and not Path(name).is_absolute(),'Source escape')
        for p in (prior/'source'/name,code/name):
            md.need(p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==h,'Changed source '+name)
    data=read(prior/'market/data.json')
    md.need(digest(data)==DATA_SHA,'Different saved dataset')
    checks={'manifests':counts,'source_files':len(frozen['sources']),
        'first_raw':md.audit_raw(prior/'market/round1',data),
        'second_raw':md.audit_raw(prior/'market/round2',read(prior/'market/second-download.json')),
        'redownload':md.compare(data,read(prior/'market/second-download.json')),
        '1m_to_15m':validate_pair(data)}
    compressed=(prior_final/'all-results.json.gz').read_bytes()
    with gzip.GzipFile(fileobj=__import__('io').BytesIO(compressed)) as f: raw=f.read(40_000_001)
    md.need(len(raw)<=40_000_000,'Oversized results')
    rows=strict_json(raw)
    md.need(len(rows)==264,'Prior result coverage')
    return data,rows,checks
