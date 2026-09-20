"""Bounded follow-up of observed Chainticks funding and coverage anomalies."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import urllib.parse
import unittest

from chainticks_check import Client, ROOT, REPO, MIB, PATTERN, decode, timestamp, require, emit, inventory

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
    counts=Counter();examples=[];ranges=defaultdict(list);offsets=Counter()
    for row in rows:
        if row.get('symbol')!='ETH':continue
        counts['eth_rows']+=1
        raw=json.loads(row.get('raw_json') or '{}')
        require(isinstance(raw,dict),'Funding raw must be object')
        n=dec(row.get('funding_rate'))
        if n is None:counts['normalized_null_or_invalid']+=1
        elif n==0:counts['normalized_zero']+=1
        else:counts['normalized_nonzero']+=1
        for key in ('funding','funding_rate','cum_funding','unclamped_funding'):
            r=dec(raw.get(key))
            if r is not None:
                ranges[key].append(r)
                counts['raw_'+key+'_numeric']+=1
                # Cumulative funding is intentionally NOT compared to a per-period rate.
                if key in ('funding','funding_rate') and n is not None and n!=r:
                    counts['normal_diff_raw_'+key]+=1
        stamp=row.get('exchange_time') or row.get('recorded_at')
        if isinstance(raw.get('time'),str) and stamp:
            try:offsets[str((timestamp(raw['time'])-timestamp(stamp))/1e6)]+=1
            except (ValueError,TypeError):counts['raw_time_parse_failure']+=1
        if len(examples)<3:
            examples.append({'timestamp':stamp,
                'normalized_funding_rate':str(row.get('funding_rate')),
                'normalized_mark_price':row.get('mark_price'),
                'normalized_index_price':row.get('index_price'),
                'raw_selected_fields':{k:raw[k] for k in FIELDS if k in raw and isinstance(raw[k],(str,int,float,bool,type(None)))}})
    return {'counts':dict(counts),'raw_numeric_ranges':{k:[str(min(v)),str(max(v))] for k,v in ranges.items()},
        'raw_minus_normalized_time_seconds':dict(offsets),'examples':examples,
        'funding_units_and_settlement_semantics':'NOT_VERIFIED'}


def parquet_batches(raw, requested):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pf=pq.ParquetFile(pa.BufferReader(raw))
    require(pf.metadata.num_rows<=2000000,'Row cap')
    require(sum(pf.metadata.row_group(i).total_byte_size for i in range(pf.num_row_groups))<=1024*MIB,'Decoded size cap')
    require('symbol' in pf.schema_arrow.names,'No symbol column')
    cols=[k for k in requested if k in pf.schema_arrow.names]
    yield from pf.iter_batches(batch_size=4096,columns=cols,use_threads=False)


def read_funding(raw):
    import pyarrow as pa
    import pyarrow.compute as pc
    def rows():
        total=0
        for b in parquet_batches(raw,('symbol','exchange_time','recorded_at','funding_rate','mark_price','index_price','raw_json')):
            eth=pc.filter(b,pc.equal(b.column(b.schema.get_field_index('symbol')),pa.scalar('ETH')))
            total+=eth.num_rows
            require(total<=200000,'ETH cap')
            yield from eth.to_pylist()
    return funding_rows(rows())


def minute_coverage(rows, day):
    start=timestamp(day+'T00:00:00Z');end=start+86400000000
    allminutes=set();ethminutes=set();counts=Counter();offsets=Counter()
    first=None;last=None
    for row in rows:
        stamp=row.get('exchange_time') or row.get('recorded_at')
        t=timestamp(stamp)
        counts['all_rows']+=1
        if not start<=t<end:
            counts['outside_partition_date']+=1
            continue
        m=(t-start)//60000000
        allminutes.add(m)
        if row.get('symbol')=='ETH':
            ethminutes.add(m);counts['eth_rows']+=1
            first=t if first is None else min(first,t)
            last=t if last is None else max(last,t)
            raw=json.loads(row.get('raw_json') or '{}')
            rt=raw.get('time') if isinstance(raw,dict) else None
            if isinstance(rt,(int,float)) and not isinstance(rt,bool):
                # Hyperliquid raw trade time is milliseconds, not an inferred timestamp unit.
                offsets[str(int(rt)*1000-t)]+=1
            elif isinstance(rt,str):
                try:offsets[str(timestamp(rt)-t)]+=1
                except (ValueError,TypeError):counts['raw_time_not_iso']+=1
    def coverage(ms):
        return {'observed_minutes':len(ms),'even_utc_minutes':sum(m%2==0 for m in ms),
            'odd_utc_minutes':sum(m%2==1 for m in ms),
            'observed_by_6h':[sum(a<=m<a+360 for m in ms) for a in (0,360,720,1080)],
            'presence_1440_bits':''.join('1' if i in ms else '0' for i in range(1440))}
    def iso(t):return datetime.fromtimestamp(t/1e6,timezone.utc).isoformat() if t is not None else None
    return {'counts':dict(counts),'all_symbols':coverage(allminutes),'ETH':coverage(ethminutes),
        'minutes_no_eth_but_other_symbols':len(allminutes-ethminutes),'minutes_with_no_rows_any_symbol':1440-len(allminutes),
        'first_eth_utc':iso(first),'last_eth_utc':iso(last),
        'raw_minus_normalized_microseconds':dict(offsets),
        'absence_does_not_prove_exchange_inactivity':True}


def read_minutes(raw,day):
    def rows():
        for b in parquet_batches(raw,('symbol','exchange_time','recorded_at','raw_json')):
            yield from b.to_pylist()
    return minute_coverage(rows(),day)


def entries(manifest):
    fs=manifest['files']
    if isinstance(fs,dict):
        return [dict(v,path=k) if isinstance(v,dict) else {'path':k,'metadata':v} for k,v in fs.items()]
    require(isinstance(fs,list),'Unknown manifest file container')
    require(all(isinstance(x,(str,dict)) for x in fs),'Bad manifest file entry')
    return [{'path':x} if isinstance(x,str) else x for x in fs]


def select_targets(bykind):
    """Select only existing dates; never guess a date from another data category."""
    selected=[]
    for kind in ('funding','trades','markets'):
        days=sorted(bykind[kind])
        if not days:
            continue
        # Prefer whole-day samples when at most two physical shards exist.
        eligible=[d for d in days if len(bykind[kind][d])<=2] or days
        indexes=(0,len(eligible)//2,len(eligible)-1) if kind=='funding' else (0,len(eligible)-1) if kind=='trades' else (len(eligible)-1,)
        for day in sorted({eligible[j] for j in indexes}):
            paths=sorted(bykind[kind][day])
            picks=paths if len(paths)<=2 else [paths[0],paths[-1]]
            for path in picks:
                selected.append((kind,day,path,len(paths),len(picks)==len(paths)))
    require(len(selected)<=12,'Follow-up sample cap')
    return selected


def main():
    c=Client()
    manifest,me=c.json(BASE+'_manifest.json')
    es=entries(manifest)
    require(es,'Empty manifest')
    filepaths=[x.get('path') or x.get('file') or x.get('filename') for x in es]
    require(all(isinstance(p,str) for p in filepaths) and len(filepaths)==len(set(filepaths)), 'Invalid/duplicate manifest paths')
    bykind, inv=inventory(filepaths)
    emit('FOLLOWUP_MANIFEST',{'revision':REVISION,'evidence':me,'entry_count':len(es),
        'file_entry_example':es[0],'inventory_recomputed_from_manifest':{k:{x:y for x,y in v.items() if x!='date_runs'} for k,v in inv.items()},
        'manifest_row_counts_all_symbols_unverified':manifest.get('row_counts'),
        'manifest_time_ranges':manifest.get('time_ranges'),
        'no_per_file_row_count_assumed':True})
    selected=select_targets(bykind)
    emit('FOLLOWUP_SELECTED_EXISTING_PATHS',selected)
    status=[]
    for kind,day,p,n,all_parts in selected:
        item={'kind':kind,'date':day,'path':p,'parts_for_date':n,'all_day_parts_selected':all_parts}
        try:
            raw,ev=c.get(BASE+urllib.parse.quote(p,safe='/='),96*MIB)
            item['evidence']=ev
            if kind=='funding':item.update(read_funding(raw))
            else:
                result=decode(raw,kind,day);result.pop('examples',None)
                item.update(result)
                item['minute_coverage']=read_minutes(raw,day)
                for coverage in ('ETH','all_symbols'):
                    item['minute_coverage'][coverage].pop('presence_1440_bits',None)
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
    def test_selection_only_existing_dates(self):
        b=defaultdict(dict)
        b['funding']={'2024-01-03':['a'],'2024-06-02':['b'],'2025-04-15':['c']}
        b['trades']={'2025-01-07':['t0','t1'],'2025-06-05':['t2']}
        picks=select_targets(b)
        self.assertEqual(len(picks),6)
        self.assertTrue(all(d in b[k] and path in b[k][d] for k,d,path,n,full in picks))
        self.assertTrue(all(full for k,d,path,n,full in picks))
    def test_selection_marks_partial(self):
        b=defaultdict(dict);b['trades']={'2025-01-01':['t0','t1','t2']}
        picks=select_targets(b)
        self.assertEqual(len(picks),2)
        self.assertTrue(all(not full for k,d,path,n,full in picks))
    def test_detect_raw_funding_rate(self):
        r=funding_rows([{'symbol':'ETH','funding_rate':0,'raw_json':'{"funding_rate":"0.00001"}'}])
        self.assertEqual(r['counts']['normal_diff_raw_funding_rate'],1)
    def test_do_not_equate_cumulative_and_period_rate(self):
        r=funding_rows([{'symbol':'ETH','funding_rate':.01,'raw_json':'{"cum_funding":12}'}])
        self.assertEqual(r['funding_units_and_settlement_semantics'],'NOT_VERIFIED')
        self.assertNotIn('normal_diff_raw_cum_funding',r['counts'])
    def test_account_not_exported(self):
        r=funding_rows([{'symbol':'ETH','raw_json':'{"user":"private","funding_rate":0.001}'}])
        self.assertNotIn('user',r['examples'][0]['raw_selected_fields'])
    def test_noneth(self):self.assertEqual(funding_rows([{'symbol':'BTC'}])['counts'],{})
    def test_nonfinite(self):self.assertIsNone(dec('nan'))
    def test_manifest_dict(self):self.assertEqual(entries({'files':{'a':{'rows':1}}}),[{'path':'a','rows':1}])
    def test_manifest_list(self):self.assertEqual(entries({'files':[{'path':'a'}]}),[{'path':'a'}])
    def test_manifest_strings_regression(self):self.assertEqual(entries({'files':['a','b']}),[{'path':'a'},{'path':'b'}])
    def test_manifest_reject_number(self):
        with self.assertRaises(ValueError):entries({'files':[1]})
    def test_inventory_from_string_manifest(self):
        es=entries({'files':['hyperliquid_chain/trades/date=2026-01-28/part-0000.parquet']})
        _, inv=inventory([x['path'] for x in es])
        self.assertEqual(inv['trades']['partition_days'],1)
    def test_funding_time_offset(self):
        r=funding_rows([{'symbol':'ETH','recorded_at':'2026-02-02T00:00:00Z','funding_rate':.01,
            'raw_json':'{"time":"2026-02-02T01:00:00Z"}'}])
        self.assertEqual(r['raw_minus_normalized_time_seconds'],{'3600.0':1})
    def test_minute_parity(self):
        rows=[{'symbol':'ETH','recorded_at':f'2026-01-28T00:{m:02d}:00Z'} for m in (0,2,4)]
        r=minute_coverage(rows,'2026-01-28')
        self.assertEqual(r['ETH']['even_utc_minutes'],3);self.assertEqual(r['ETH']['odd_utc_minutes'],0)
        self.assertEqual(len(r['ETH']['presence_1440_bits']),1440)
    def test_market_vs_eth_absence(self):
        rows=[{'symbol':'ETH','recorded_at':'2026-01-28T00:00:00Z'},
              {'symbol':'BTC','recorded_at':'2026-01-28T00:01:00Z'}]
        r=minute_coverage(rows,'2026-01-28')
        self.assertEqual(r['minutes_no_eth_but_other_symbols'],1)
        self.assertEqual(r['minutes_with_no_rows_any_symbol'],1438)
    def test_millisecond_raw_time(self):
        r=minute_coverage([{'symbol':'ETH','recorded_at':'2026-01-28T00:00:00Z',
            'raw_json':'{"time":1769558400000}'}],'2026-01-28')
        self.assertEqual(r['raw_minus_normalized_microseconds'],{'0':1})


if __name__=='__main__':
    import sys
    if '--self-test' in sys.argv:
        raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests)).wasSuccessful() else 1)
    raise SystemExit(main())
