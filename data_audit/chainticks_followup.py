"""Bounded follow-up of observed Chainticks funding and coverage anomalies."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import urllib.parse
import unittest

from chainticks_check import Client, ROOT, REPO, MIB, PATTERN, decode, timestamp, require, emit

REVISION='54b07b4bebc5574a065d3fbf4432634cf9148f50'
BASE=ROOT+'/datasets/'+REPO+'/resolve/'+REVISION+'/'
FIELDS=('coin','type','time','funding','funding_rate','cum_funding','unclamped_funding','mark_px','oracle_px')


def dec(v):
    if v is None or isinstance(v,bool):return None
    try:
        x=Decimal(str(v))
        return x if x.is_finite() else None
    except (InvalidOperation,ValueError):return None


def funding_rows(rows):
    counts=Counter();examples=[];ranges=defaultdict(list)
    for row in rows:
        if row.get('symbol')!='ETH':continue
        counts['eth_rows']+=1
        raw=json.loads(row.get('raw_json') or '{}')
        require(isinstance(raw,dict),'Funding raw must be object')
        n=dec(row.get('funding_rate'))
        if n is None:counts['normalized_null_or_invalid']+=1
        elif n==0:counts['normalized_zero']+=1
        for key in ('funding','funding_rate','cum_funding','unclamped_funding'):
            r=dec(raw.get(key))
            if r is not None:
                ranges[key].append(r)
                counts['raw_'+key+'_numeric']+=1
                if n==0 and r!=0:counts['normal_zero_raw_'+key+'_nonzero']+=1
                if n is not None and n!=r:counts['normal_diff_raw_'+key]+=1
        if len(examples)<3:
            examples.append({'timestamp':row.get('exchange_time') or row.get('recorded_at'),
                'normalized_funding_rate':str(row.get('funding_rate')),
                'normalized_mark_price':row.get('mark_price'),
                'normalized_index_price':row.get('index_price'),
                'raw_selected_fields':{k:raw[k] for k in FIELDS if k in raw}})
    return {'counts':dict(counts),'raw_numeric_ranges':{k:[str(min(v)),str(max(v))] for k,v in ranges.items()},
        'examples':examples,'funding_units_and_settlement_semantics':'NOT_VERIFIED'}


def read_funding(raw):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    pf=pq.ParquetFile(pa.BufferReader(raw))
    require(pf.metadata.num_rows<=2000000,'Row cap')
    cols=[k for k in ('symbol','exchange_time','recorded_at','funding_rate','mark_price','index_price','raw_json') if k in pf.schema_arrow.names]
    def rows():
        total=0
        for b in pf.iter_batches(batch_size=4096,columns=cols,use_threads=False):
            eth=pc.filter(b,pc.equal(b.column(b.schema.get_field_index('symbol')),pa.scalar('ETH')))
            total+=eth.num_rows
            require(total<=200000,'ETH cap')
            yield from eth.to_pylist()
    return funding_rows(rows())


def entries(manifest):
    fs=manifest['files']
    if isinstance(fs,dict):
        return [dict(v,path=k) if isinstance(v,dict) else {'path':k,'metadata':v} for k,v in fs.items()]
    require(isinstance(fs,list),'Unknown manifest file container')
    return fs


def main():
    c=Client()
    manifest,me=c.json(BASE+'_manifest.json')
    es=entries(manifest)
    require(es and isinstance(es[0],dict),'Unknown file entry format')
    types=Counter();byday=defaultdict(list)
    for x in es:
        p=x.get('path') or x.get('file') or x.get('filename')
        m=PATTERN.fullmatch(p or '')
        if m:byday[(m[1],m[2])].append(x)
        types.update(x.keys())
    emit('FOLLOWUP_MANIFEST',{'revision':REVISION,'evidence':me,'entry_count':len(es),
        'file_entry_keys':dict(types),'first_entry':es[0],
        'trades_daily_rows':{day:sum(int(x.get('rows',x.get('row_count',0))) for x in xs)
            for (kind,day),xs in sorted(byday.items()) if kind=='trades'}})
    selected=[]
    for kind,dates in (('funding',('2023-05-20','2026-01-03','2026-08-11')),
                       ('trades',('2026-04-10','2026-04-12'))):
        for day in dates:
            paths=[x.get('path') or x.get('file') or x.get('filename') for x in byday.get((kind,day),[])]
            require(paths,'Expected day absent')
            require(len(paths)<=4,'Target day exceeds bounded follow-up part count')
            for p in sorted(paths):selected.append((kind,day,p,len(paths)))
    require(len(selected)<=12,'Follow-up sample cap')
    status=[]
    for kind,day,p,n in selected:
        item={'kind':kind,'date':day,'path':p,'parts_for_date':n}
        try:
            raw,ev=c.get(BASE+urllib.parse.quote(p,safe='/='),96*MIB)
            item['evidence']=ev
            if kind=='funding':item.update(read_funding(raw))
            else:
                result=decode(raw,kind,day)
                result.pop('examples',None)
                item.update(result)
            item['status']='DECODED'
        except Exception as exc:
            item.update(status='NOT_DECODED',error_type=type(exc).__name__)
            if isinstance(exc,ValueError):item['detail']=str(exc)[:160]
        emit('CHAINTICKS_FOLLOWUP_SAMPLE',item);status.append(item['status'])
    emit('CHAINTICKS_FOLLOWUP_RESULT',{'revision':REVISION,'selected_files':len(selected),
        'decoded_files':status.count('DECODED'),'bytes':c.total,'new_backtests':0,
        'whole_dataset_decoded':False})
    return 0 if status and all(x=='DECODED' for x in status) else 1


class Tests(unittest.TestCase):
    def test_detect_raw_funding_rate(self):
        r=funding_rows([{'symbol':'ETH','funding_rate':0,'raw_json':'{"funding_rate":"0.00001"}'}])
        self.assertEqual(r['counts']['normal_zero_raw_funding_rate_nonzero'],1)
    def test_do_not_equate_cumulative_and_period_rate(self):
        r=funding_rows([{'symbol':'ETH','funding_rate':.01,'raw_json':'{"cum_funding":12}'}])
        self.assertEqual(r['funding_units_and_settlement_semantics'],'NOT_VERIFIED')
        self.assertNotIn('raw_funding_rate_numeric',r['counts'])
    def test_account_not_exported(self):
        r=funding_rows([{'symbol':'ETH','raw_json':'{"user":"private","funding_rate":0.001}'}])
        self.assertNotIn('user',r['examples'][0]['raw_selected_fields'])
    def test_noneth(self):self.assertEqual(funding_rows([{'symbol':'BTC'}])['counts'],{})
    def test_nonfinite(self):self.assertIsNone(dec('nan'))
    def test_manifest_dict(self):self.assertEqual(entries({'files':{'a':{'rows':1}}}),[{'path':'a','rows':1}])
    def test_manifest_list(self):self.assertEqual(entries({'files':[{'path':'a'}]}),[{'path':'a'}])


if __name__=='__main__':
    import sys
    if '--self-test' in sys.argv:
        raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests)).wasSuccessful() else 1)
    raise SystemExit(main())
