"""Read-only, bounded Chainticks inventory and ETH Parquet sample audit.

Only public market data is read. No trade execution, remote-code loading,
accounts, API keys, scheduling or paid services. Inventory is not completeness.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
import re
import statistics
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

REPO = 'Chainticks/perp-data'
ROOT = 'https://huggingface.co'
MIB = 1024 * 1024
HOSTS = frozenset(('huggingface.co', 'cdn-lfs.huggingface.co',
    'cdn-lfs-us-1.hf.co', 'cas-bridge.xethub.hf.co',
    'us.aws.cdn.hf.co', 'us.gcp.cdn.hf.co'))
KINDS = ('trades', 'markets', 'funding', 'open_interest', 'liquidations')
PUBLIC = frozenset(('chain_rpc', 'on_chain_event', 'hypercore_s3'))
PATTERN = re.compile(r'^hyperliquid_chain/([a-z_]+)/date=(\d{4}-\d{2}-\d{2})/(part-[\w-]+\.parquet)$')
COLUMNS = ('symbol', 'provider', 'recorded_at', 'exchange_time', 'source_kind',
    'price', 'size', 'notional_usd', 'trade_id', 'side', 'mark_price',
    'index_price', 'oracle_price', 'funding_rate', 'open_interest_base',
    'open_interest_usd', 'raw_json')
RAW_NUMERIC = ('coin', 'time', 'funding', 'mark_px', 'oracle_px', 'mid_px',
    'impact_bid_px', 'impact_ask_px', 'open_interest', 'px', 'sz', 'side', 'tid')


def require(condition, msg):
    if not condition:
        raise ValueError(msg)


def safe_url(url):
    p = urllib.parse.urlsplit(url)
    require(p.scheme == 'https' and p.hostname in HOSTS and p.port in (None, 443)
        and not p.username and not p.password and not p.fragment,
        'Rejected unexpected host or URL')
    return p


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self):
        self.total = 0
        self.started = time.monotonic()
        self.requests = []
        self.opener = urllib.request.build_opener(Redirect())

    def get(self, url, cap=8*MIB):
        safe_url(url)
        require(time.monotonic() - self.started < 680, 'Overall network deadline')
        req = urllib.request.Request(url, headers={
            'User-Agent': 'jaja-trade-chainticks-audit/1.0', 'Accept-Encoding': 'identity'})
        before = time.monotonic()
        data = bytearray()
        with self.opener.open(req, timeout=20) as r:
            require(r.status == 200, 'Unexpected response status')
            final = safe_url(r.geturl())
            size = r.headers.get('Content-Length')
            require(not size or int(size) <= cap, 'File exceeds byte cap')
            while True:
                require(time.monotonic()-before < 80, 'File deadline')
                chunk = r.read(min(65536, cap-len(data)+1))
                if not chunk:
                    break
                data.extend(chunk)
                self.total += len(chunk)
                require(len(data) <= cap, 'File exceeds streaming cap')
                require(self.total <= 768*MIB, 'Total download budget')
        entry = {'source_url': url, 'final_host': final.hostname,
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        self.requests.append(entry)
        return bytes(data), entry

    def json(self, url):
        raw, evidence = self.get(url)
        return json.loads(raw), evidence


def segments(dates):
    values = sorted({date.fromisoformat(d) for d in dates})
    result = []
    for d in values:
        if not result or d != result[-1][1] + timedelta(days=1):
            result.append([d, d])
        else:
            result[-1][1] = d
    return [{'start': a.isoformat(), 'end': b.isoformat(), 'days': (b-a).days+1} for a,b in result]


def inventory(files):
    out = defaultdict(lambda: defaultdict(list))
    for path in files:
        match = PATTERN.fullmatch(path)
        if match and match[1] in KINDS:
            date.fromisoformat(match[2])
            out[match[1]][match[2]].append(path)
    answer = {}
    for kind in KINDS:
        parts = out[kind]
        days = sorted(parts)
        runs = segments(days)
        answer[kind] = {'files': sum(map(len, parts.values())), 'partition_days': len(days),
            'first_date': days[0] if days else None, 'last_date': days[-1] if days else None,
            'date_runs': runs, 'longest_date_run': max(runs, key=lambda r:r['days']) if runs else None,
            'missing_days_within_range': ((date.fromisoformat(days[-1])-date.fromisoformat(days[0])).days+1-len(days)) if days else 0,
            'date_partition_not_eth_row_coverage': True}
    return out, answer


def timestamp(value):
    require(isinstance(value, str), 'Timestamp not a string')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(dt.tzinfo is not None and dt.utcoffset() == timedelta(0), 'Timestamp not UTC')
    return int(dt.timestamp()*1_000_000)


def finite(v, positive=False):
    if v is None or isinstance(v, bool):
        return False
    try:
        n = Decimal(str(v))
        return n.is_finite() and (not positive or n > 0)
    except (InvalidOperation, ValueError):
        return False


def inspect_rows(rows, kind, day):
    """Only decoded ETH rows; no raw account/party fields are logged."""
    tvals, ids = [], []
    numeric, bad, nulls = defaultdict(list), Counter(), Counter()
    sources, providers, sides, rawkeys, rawtypes = Counter(), Counter(), Counter(), Counter(), Counter()
    fieldset = set()
    examples = []
    count = 0
    different_capture = 0
    rawdiff = Counter()
    for row in rows:
        if row.get('symbol') != 'ETH':
            continue
        count += 1
        fieldset.update(row)
        source = row.get('source_kind')
        sources[str(source)] += 1
        providers[str(row.get('provider'))] += 1
        if source not in PUBLIC:
            bad['source_kind'] += 1
        stamp = row.get('exchange_time') or row.get('recorded_at')
        try:
            t = timestamp(stamp)
            tvals.append(t)
            if datetime.fromtimestamp(t/1e6, timezone.utc).date().isoformat() != day:
                bad['outside_partition_date'] += 1
            if row.get('recorded_at') and row.get('exchange_time') and row['recorded_at'] != row['exchange_time']:
                different_capture += 1
        except (ValueError, TypeError, OverflowError):
            bad['timestamp'] += 1
        for k in ('price','size','notional_usd','mark_price','index_price','oracle_price',
                  'funding_rate','open_interest_base','open_interest_usd'):
            if k not in row:
                continue
            if row[k] is None:
                nulls[k] += 1
            elif not finite(row[k], positive=k in ('price','size','mark_price','index_price','oracle_price')):
                bad[k] += 1
            else:
                numeric[k].append(float(row[k]))
        if 'trade_id' in row:
            ids.append(row['trade_id'])
        if 'side' in row:
            sides[str(row['side'])] += 1
        raw = {}
        if row.get('raw_json'):
            try:
                decoded = json.loads(row['raw_json'])
                if isinstance(decoded, dict):
                    rawkeys.update(decoded.keys())
                    if 'type' in decoded:
                        rawtypes[str(decoded['type'])] += 1
                    raw = {k:decoded[k] for k in RAW_NUMERIC if k in decoded and isinstance(decoded[k], (str,int,float,bool))}
                    for normalized, original in (('funding_rate','funding'),('mark_price','mark_px'),
                            ('index_price','oracle_px'),('oracle_price','oracle_px'),('price','px'),('size','sz')):
                        if row.get(normalized) is not None and decoded.get(original) is not None:
                            if Decimal(str(row[normalized])) != Decimal(str(decoded[original])):
                                rawdiff[normalized] += 1
            except (ValueError, TypeError, InvalidOperation):
                bad['raw_json'] += 1
        if len(examples) < 2:
            example = {k:(str(v) if isinstance(v,float) and not math.isfinite(v) else v) for k,v in row.items() if k not in ('raw_json',) and isinstance(v,(str,int,float,type(None)))}
            # Only schema-whitelisted columns were read; do not output arbitrary raw fields.
            examples.append({'normalized':example, 'raw_market_fields':raw})
    unique = sorted(set(tvals))
    gaps = [b-a for a,b in zip(unique,unique[1:])]
    minute_ids = {t//60_000_000 for t in unique}
    r = {'eth_rows':count,'fields':sorted(fieldset),'first_timestamp': min(tvals) if tvals else None,
         'last_timestamp': max(tvals) if tvals else None,'timestamps_unit':'microseconds_UTC',
         'unique_timestamps':len(unique),'duplicate_timestamps':len(tvals)-len(unique),
         'input_timestamp_reversals':sum(a>b for a,b in zip(tvals,tvals[1:])),
         'minutes_with_observations':len(minute_ids),'day_minutes':1440,
         'max_gap_seconds':max(gaps)/1e6 if gaps else None,
         'median_gap_seconds':statistics.median(gaps)/1e6 if gaps else None,
         'gap_over_60_seconds':sum(g>60_000_000 for g in gaps),
         'invalid_counts':dict(bad),'null_counts':dict(nulls),'sources':dict(sources),
         'providers':dict(providers),'sides':dict(sides),'raw_keys':sorted(rawkeys),
         'raw_type_counts':dict(rawtypes),'normalized_raw_numeric_differences':dict(rawdiff),
         'different_recorded_exchange_times':different_capture,
         'numeric_ranges':{k:[min(v),max(v)] for k,v in numeric.items()},'examples':examples}
    if ids:
        nonnull=[str(x) for x in ids if x is not None]
        r['null_trade_ids']=len(ids)-len(nonnull)
        r['duplicate_nonnull_trade_ids']=len(nonnull)-len(set(nonnull))
    return r


def decode(raw, kind, day):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(pa.BufferReader(raw))
    names = pf.schema_arrow.names
    require('symbol' in names, 'No symbol column')
    require(pf.metadata.num_rows <= 2_000_000, 'Row cap exceeded')
    require(sum(pf.metadata.row_group(i).total_byte_size for i in range(pf.num_row_groups)) <= 1024*MIB,
            'Decoded-size metadata cap exceeded')
    chosen = [k for k in COLUMNS if k in names]
    batches=[]
    scanned=0
    for batch in pf.iter_batches(batch_size=8192, columns=chosen, use_threads=False):
        scanned += batch.num_rows
        eth = pc.filter(batch, pc.equal(batch.column(batch.schema.get_field_index('symbol')), pa.scalar('ETH')))
        batches.extend(eth.to_pylist())
        require(len(batches)<=200000,'ETH row cap exceeded')
    require(scanned==pf.metadata.num_rows,'Decoded row count mismatch')
    return dict(parquet_rows=pf.metadata.num_rows, parquet_columns=names,
        arrow_schema=str(pf.schema_arrow.remove_metadata()),decoded_all_file_rows=True,
        **inspect_rows(batches,kind,day))


def emit(tag, value):
    print(tag+'='+json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False), flush=True)


def main():
    client=Client()
    info, meta=client.json(ROOT+'/api/datasets/'+REPO)
    require(info.get('id')==REPO and not info.get('private') and not info.get('gated'), 'Dataset is not public ungated')
    revision=info.get('sha','')
    require(re.fullmatch('[0-9a-f]{40}',revision) is not None,'Bad revision')
    files=[x['rfilename'] for x in info.get('siblings',[]) if isinstance(x,dict) and isinstance(x.get('rfilename'),str)]
    require(files and len(files)==len(set(files)), 'No inventory or duplicate paths')
    base=ROOT+'/datasets/'+REPO+'/resolve/'+revision+'/'
    manifest,manifest_ev=client.json(base+'_manifest.json')
    schema,schema_ev=client.json(base+'_schema.json')
    bykind, inv=inventory(files)
    manifest_files=manifest.get('files')
    first_entry=(manifest_files[0] if manifest_files else None) if isinstance(manifest_files,list) else list(manifest_files.items())[:1] if isinstance(manifest_files,dict) else None
    header={'mode':'CHAINTICKS_DATA_AUDIT_NOT_BACKTEST','repo':REPO,'revision':revision,
        'license_from_card':(info.get('cardData') or {}).get('license'),
        'inventory_file_count':len(files),'inventory':inv,'metadata':meta,
        'manifest_evidence':manifest_ev,'schema_evidence':schema_ev,'schema':schema,
        'manifest_row_counts':manifest.get('row_counts'),'manifest_time_ranges':manifest.get('time_ranges'),
        'manifest_file_entry_example':first_entry,'manifest_generated_at':manifest.get('generated_at'),
        'whole_history_row_validation':False,'paid_services':0,'scheduled_tasks':False}
    emit('CHAINTICKS_INVENTORY',header)
    selected=[]
    for kind in KINDS:
        days=sorted(bykind[kind])
        for day in sorted({days[j] for j in (0,len(days)//2,len(days)-1)}) if days else []:
            paths=sorted(bykind[kind][day])
            picks=paths if len(paths)<=2 else [paths[0],paths[-1]]
            selected.extend((kind,day,p,len(paths)) for p in picks)
    require(len(selected)<=30,'Sample count cap')
    samples=[]
    for kind,day,p,parts in selected:
        item={'kind':kind,'date':day,'path':p,'total_day_parts':parts,
              'all_day_parts_selected':parts<=2}
        try:
            raw,ev=client.get(base+urllib.parse.quote(p,safe='/='),96*MIB)
            item.update(evidence=ev, **decode(raw,kind,day), status='PARQUET_ROWS_DECODED')
        except Exception as e:
            item.update(status='NOT_DECODED',error_type=type(e).__name__)
            if isinstance(e,urllib.error.HTTPError):item['http_status']=e.code
            if isinstance(e,ValueError):item['detail']=str(e)[:160]
        samples.append(item)
        emit('CHAINTICKS_SAMPLE',item)
    result={'revision':revision,'sample_files_requested':len(selected),
        'sample_files_decoded':sum(x['status']=='PARQUET_ROWS_DECODED' for x in samples),
        'sample_files_containing_eth':sum(x.get('eth_rows',0)>0 for x in samples),
        'eth_rows_decoded':sum(x.get('eth_rows',0) for x in samples),
        'download_bytes':client.total,'whole_dataset_checked':False,'new_backtests':0,
        'status':'INVENTORY_AND_SAMPLES_COMPLETE' if all(x['status']=='PARQUET_ROWS_DECODED' for x in samples) else 'INVENTORY_COMPLETE_SAMPLE_LIMITS',
        'limits':['Partition dates are not ETH continuity evidence','Sampled days may contain unsampled parts',
            'Provider manifest is not independent market truth','Funding snapshots are not assumed settled funding',
            'Temporary runner downloads; summaries and hashes kept in logs, no artifact storage']}
    emit('CHAINTICKS_AUDIT_RESULT',result)
    return 0 if result['sample_files_decoded'] else 1


class Tests(unittest.TestCase):
    def test_url_accept(self):self.assertEqual(safe_url('https://us.aws.cdn.hf.co/a').hostname,'us.aws.cdn.hf.co')
    def test_url_reject(self):
        for u in ('http://huggingface.co/x','https://huggingface.co.evil.test/x','https://u:p@huggingface.co/x','https://127.0.0.1/x','https://huggingface.co:444/x'):
            with self.assertRaises(ValueError):safe_url(u)
    def test_segments(self):self.assertEqual([r['days'] for r in segments(['2024-01-01','2024-01-02','2024-01-04'])],[2,1])
    def test_segments_empty(self):self.assertEqual(segments([]),[])
    def test_inventory(self):
        _,r=inventory(['hyperliquid_chain/trades/date=2024-01-01/part-0000.parquet','hyperliquid_chain/trades/date=2024-01-03/part-0000.parquet'])
        self.assertEqual(r['trades']['missing_days_within_range'],1)
        self.assertEqual(r['markets']['partition_days'],0)
    def test_invalid_date(self):
        with self.assertRaises(ValueError):inventory(['hyperliquid_chain/trades/date=2024-02-31/part-0000.parquet'])
    def test_timestamp_zone(self):
        with self.assertRaises(ValueError):timestamp('2024-01-01T00:00:00')
    def test_numeric(self):
        self.assertTrue(finite(-1));self.assertFalse(finite(-1,True));self.assertFalse(finite(float('nan')));self.assertFalse(finite('inf'))
    def test_only_eth(self):self.assertEqual(inspect_rows([{'symbol':'BTC'}],'trades','2024-01-01')['eth_rows'],0)
    def test_anomalies(self):
        r=inspect_rows([{'symbol':'ETH','exchange_time':'2024-01-02T00:00:00Z','price':-1,'size':None,'source_kind':'api'}],'trades','2024-01-01')
        self.assertEqual(r['invalid_counts']['outside_partition_date'],1);self.assertEqual(r['invalid_counts']['price'],1);self.assertEqual(r['null_counts']['size'],1)
    def test_raw_differences(self):
        r=inspect_rows([{'symbol':'ETH','recorded_at':'2024-01-01T00:00:00Z','source_kind':'hypercore_s3','funding_rate':.01,'raw_json':'{"funding":"0.02"}'}],'funding','2024-01-01')
        self.assertEqual(r['normalized_raw_numeric_differences']['funding_rate'],1)
    def test_duplicate_ids(self):
        row={'symbol':'ETH','recorded_at':'2024-01-01T00:00:00Z','source_kind':'hypercore_s3','trade_id':'a'}
        self.assertEqual(inspect_rows([row,row],'trades','2024-01-01')['duplicate_nonnull_trade_ids'],1)
    def test_gap(self):
        rs=[{'symbol':'ETH','recorded_at':f'2024-01-01T00:{i:02}:00Z','source_kind':'hypercore_s3'} for i in (0,1,3)]
        r=inspect_rows(rs,'markets','2024-01-01');self.assertEqual(r['max_gap_seconds'],120);self.assertEqual(r['minutes_with_observations'],3)
    def test_raw_no_account_export(self):
        r=inspect_rows([{'symbol':'ETH','recorded_at':'2024-01-01T00:00:00Z','source_kind':'hypercore_s3','raw_json':'{"user":"private","px":"1"}'}],'trades','2024-01-01')
        self.assertNotIn('user',r['examples'][0]['raw_market_fields'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
        raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
    raise SystemExit(main())
