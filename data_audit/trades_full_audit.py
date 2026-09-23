"""Finite, anonymous ETH trade-data audit; no trading or strategy evaluation.

Reads every published trade shard in one immutable Chainticks revision.
Raw party/account data is never exported. 1h official candles are used only
as a data cross-check, not as a replacement for the original 1m strategy.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

REV = '54b07b4bebc5574a065d3fbf4432634cf9148f50'
MANIFEST_SHA = '7e83d2524f3300c430242b52d66a269566c9ad16a5a340ac25485be5da30d15a'
BASE = 'https://huggingface.co/datasets/Chainticks/perp-data/resolve/' + REV + '/'
INFO = 'https://api.hyperliquid.xyz/info'
HOSTS = {'huggingface.co', 'cdn-lfs.huggingface.co', 'cdn-lfs-us-1.hf.co',
         'cas-bridge.xethub.hf.co', 'us.aws.cdn.hf.co', 'us.gcp.cdn.hf.co', 'api.hyperliquid.xyz'}
PAT = re.compile(r'^hyperliquid_chain/trades/date=(\d{4}-\d{2}-\d{2})/part-(\d+)\.parquet$')
UTC_PATTERN = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$'
MIB = 1024 ** 2
SHARDS = 12
COLS = ('symbol', 'exchange_time', 'recorded_at', 'price', 'size', 'notional_usd',
        'trade_id', 'side', 'provider', 'source_kind')
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def require(ok, text):
    if not ok:
        raise ValueError(text)


def safe_url(url):
    p = urllib.parse.urlsplit(url)
    require(p.scheme == 'https' and p.hostname in HOSTS and p.port in (None, 443)
            and not p.username and not p.password and not p.fragment, 'URL not allowlisted')
    return p


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl)
        require(req.get_method() == 'GET', 'POST redirect refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self):
        self.start = time.monotonic()
        self.total = 0
        self.calls = 0
        # No ambient proxy/auth/cookie handler or credentials.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect())

    def read(self, url, payload=None, cap=96*MIB):
        safe_url(url)
        if payload is not None:
            require(url == INFO and payload.get('type') in ('candleSnapshot', 'fundingHistory'), 'Non-market POST refused')
        body = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(3):
            require(time.monotonic()-self.start < 1300 and self.calls < 650, 'Request/time budget exceeded')
            self.calls += 1
            request = urllib.request.Request(url, data=body, headers={
                'User-Agent': 'public-ETH-data-audit/2.0', 'Accept-Encoding': 'identity',
                'Content-Type': 'application/json'})
            try:
                started = time.monotonic()
                data = bytearray()
                with self.opener.open(request, timeout=25) as response:
                    require(response.status == 200, 'Unexpected response status')
                    host = safe_url(response.geturl()).hostname
                    length = response.headers.get('Content-Length')
                    require(not length or int(length) <= cap, 'File size cap exceeded')
                    while True:
                        require(time.monotonic()-started < 90, 'Per-file deadline exceeded')
                        chunk = response.read(min(1024*1024, cap-len(data)+1))
                        if not chunk:
                            break
                        data.extend(chunk)
                        self.total += len(chunk)
                        require(len(data) <= cap and self.total <= 10*1024*MIB, 'Download cap exceeded')
                return bytes(data), {'url': url, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'host': host}
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
                time.sleep(min(30, 3*(2**attempt)))
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(3*(attempt+1))
        raise RuntimeError('Download exhausted')


def stamp(value):
    require(isinstance(value, str) and bool(re.fullmatch(UTC_PATTERN, value)), 'Invalid UTC timestamp')
    d = datetime.fromisoformat(value.replace('Z', '+00:00')) - EPOCH
    return (d.days*86400+d.seconds)*1000000+d.microseconds


def iso(us):
    return (EPOCH+timedelta(microseconds=us)).isoformat()


def number(value, positive=False):
    require(isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
            and (not positive or value > 0), 'Invalid numeric market value')
    return value


def close(a, b, relative=1e-9, absolute=1e-8):
    a, b = Decimal(str(a)), Decimal(str(b))
    return a.is_finite() and b.is_finite() and abs(a-b) <= max(Decimal(str(absolute)), abs(b)*Decimal(str(relative)))


def manifest_paths(manifest):
    fs = manifest.get('files')
    if isinstance(fs, dict):
        paths = list(fs)
    else:
        require(isinstance(fs, list), 'Unknown manifest file container')
        paths = [x if isinstance(x, str) else x.get('path') or x.get('file') or x.get('filename') for x in fs]
    require(all(isinstance(p, str) for p in paths) and len(paths) == len(set(paths)), 'Invalid/duplicate manifest paths')
    days = defaultdict(list)
    for p in paths:
        m = PAT.fullmatch(p)
        if m:
            date.fromisoformat(m[1])
            days[m[1]].append(p)
    return {d: sorted(ps, key=lambda p: int(PAT.fullmatch(p)[2])) for d, ps in sorted(days.items())}


def date_runs(days):
    result = []
    for d in sorted(set(days)):
        if result and date.fromisoformat(d) == date.fromisoformat(result[-1]['end'])+timedelta(days=1):
            result[-1]['end'] = d
            result[-1]['days'] += 1
        else:
            result.append({'start': d, 'end': d, 'days': 1})
    return result


def missing_runs(observed):
    result = []
    for m in range(1440):
        if m in observed:
            continue
        if result and result[-1][1] == m-1:
            result[-1][1] = m
        else:
            result.append([m, m])
    return result


def shard_dates(days, shard):
    require(0 <= shard < SHARDS, 'Invalid shard')
    n = (len(days)+SHARDS-1)//SHARDS
    return list(days)[shard*n:(shard+1)*n]


def raw_fill(raw):
    require(isinstance(raw, dict), 'Raw object expected')
    e = raw.get('event', raw)
    if isinstance(e, list) and len(e) == 2 and isinstance(e[1], dict):
        return e[1]
    if isinstance(e, dict):
        return e.get('fill', e)
    return {}


def inspect_raw(rows):
    counts = Counter()
    pair_flags = defaultdict(set)
    for r in rows:
        counts['sampled_eth_rows'] += 1
        try:
            require(isinstance(r.get('raw_json'), str) and len(r['raw_json']) <= 65536, 'Raw size/type')
            f = raw_fill(json.loads(r['raw_json']))
            require(all(k in f for k in ('coin', 'px', 'sz', 'time', 'tid', 'side')), 'Unsupported raw shape')
            counts['recognized_fill_rows'] += 1
            if f['coin'] != 'ETH' or str(f['tid']) != str(r['trade_id']):
                counts['identity_mismatch'] += 1
            if not close(f['px'], r['price']) or not close(f['sz'], r['size']):
                counts['numeric_mismatch'] += 1
            t = f['time']*1000 if isinstance(f['time'], int) else stamp(f['time'])
            if t != stamp(r['exchange_time']):
                counts['time_mismatch'] += 1
            if isinstance(f.get('crossed'), bool):
                counts['taker_rows' if f['crossed'] else 'maker_rows'] += 1
                pair_flags[str(f['tid'])].add(f['crossed'])
            else:
                counts['missing_crossed'] += 1
            mapped = {'B': 'buy', 'A': 'sell'}.get(f['side'])
            if mapped != r['side']:
                counts['side_mismatch'] += 1
        except (ValueError, TypeError, KeyError, ArithmeticError):
            counts['unrecognized_or_invalid_raw'] += 1
    counts['sample_trade_ids_with_maker_and_taker'] = sum(v == {False, True} for v in pair_flags.values())
    return dict(counts)


class Day:
    def __init__(self, day):
        self.day = day
        self.start = stamp(day+'T00:00:00Z')
        self.events = {}
        self.counts = Counter()
        self.all_minutes = set()
        self.last_input = None
        self.raw = {}

    def add(self, r):
        self.counts['eth_rows'] += 1
        try:
            require(r.get('provider') == 'hyperliquid_chain' and r.get('source_kind') == 'hypercore_s3', 'Unexpected provenance label')
            t = stamp(r['exchange_time'])
            require(self.start <= t < self.start+86400000000, 'ETH outside partition date')
            p, q = number(r['price'], True), number(r['size'], True)
            require(close(number(r['notional_usd'], True), Decimal(str(p))*Decimal(str(q)), relative=1e-9), 'Notional mismatch')
            tid = r['trade_id']
            require(isinstance(tid, (str, int)) and not isinstance(tid, bool) and 0 < len(str(tid)) <= 128, 'Missing/invalid trade id')
            require(r['side'] in ('buy', 'sell'), 'Invalid side')
            if self.last_input is not None and t < self.last_input:
                self.counts['input_timestamp_reversals'] += 1
            self.last_input = t
            if r.get('recorded_at') != r['exchange_time']:
                self.counts['recorded_at_differs'] += 1
            key = str(tid)
            old = self.events.get(key)
            if old is None:
                self.events[key] = [t, p, q, 1, {r['side']}]
            else:
                self.counts['duplicate_id_extra_rows'] += 1
                if old[:3] != [t, p, q]:
                    self.counts['conflicting_trade_id_rows'] += 1
                old[3] += 1
                old[4].add(r['side'])
        except (ValueError, TypeError, KeyError, ArithmeticError):
            self.counts['invalid_eth_rows'] += 1

    def bars(self, width):
        result = {}
        # Stable source order breaks equal-timestamp ties; not inferred aggressor side.
        for t, p, q, _, _ in sorted(self.events.values(), key=lambda e:e[0]):
            b = (t//(width*1000000))*width*1000000
            if b not in result:
                result[b] = {'t': b//1000, 'o': p, 'h': p, 'l': p, 'c': p, 'v': Decimal(0), 'n': 0}
            r = result[b]
            r['h'], r['l'], r['c'] = max(r['h'], p), min(r['l'], p), p
            r['v'] += Decimal(str(q))
            r['n'] += 1
        return {t:dict(b, v=str(b['v'])) for t,b in result.items()}

    def summary(self):
        ts = sorted({e[0] for e in self.events.values()})
        minutes = {(t-self.start)//60000000 for t in ts}
        multiplicity = Counter(e[3] for e in self.events.values())
        paired = sum(e[3] == 2 and e[4] == {'buy', 'sell'} for e in self.events.values())
        gaps = [(b-a)/1e6 for a,b in zip(ts, ts[1:])]
        return {'date': self.day, **dict(self.counts), 'unique_trade_ids': len(self.events),
                'id_multiplicity': dict(multiplicity), 'paired_opposite_side_ids': paired,
                'eth_minutes': len(minutes), 'all_symbol_minutes': len(self.all_minutes),
                'missing_eth_minute_ranges': missing_runs(minutes),
                'missing_all_symbol_minute_ranges': missing_runs(self.all_minutes),
                'first_eth_utc': iso(ts[0]) if ts else None, 'last_eth_utc': iso(ts[-1]) if ts else None,
                'max_internal_gap_seconds': max(gaps) if gaps else None,
                'gaps_over_60_seconds': sum(g > 60 for g in gaps),
                'raw_mapping_sample': self.raw}


def decode_file(data, state, raw_sample=False):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(pa.BufferReader(data))
    require(all(c in pf.schema_arrow.names for c in COLS), 'Missing required trade columns')
    require(pf.metadata.num_rows <= 1000000 and sum(pf.metadata.row_group(i).total_byte_size for i in range(pf.num_row_groups)) <= 1024*MIB, 'Decoded file cap')
    seen = 0
    for batch in pf.iter_batches(batch_size=65536, columns=list(COLS), use_threads=False):
        seen += batch.num_rows
        ex = batch.column(batch.schema.get_field_index('exchange_time'))
        good = pc.fill_null(pc.match_substring_regex(ex, pattern=UTC_PATTERN), False)
        bad = batch.num_rows-int(pc.sum(pc.cast(good, pa.int64())).as_py() or 0)
        state.counts['invalid_all_symbol_timestamps'] += bad
        for v in pc.unique(pc.utf8_slice_codeunits(pc.filter(ex, good), start=0, stop=16)).to_pylist():
            if v[:10] == state.day:
                try:
                    m = int(v[11:13])*60+int(v[14:16])
                    require(0 <= m < 1440, 'Minute range')
                    state.all_minutes.add(m)
                except (ValueError, TypeError):
                    state.counts['invalid_all_symbol_timestamps'] += 1
        eth = pc.filter(batch, pc.equal(batch.column(batch.schema.get_field_index('symbol')), pa.scalar('ETH')))
        for row in eth.to_pylist():
            state.add(row)
    require(seen == pf.metadata.num_rows, 'File not fully scanned')
    if raw_sample:
        sample = []
        require('raw_json' in pf.schema_arrow.names, 'Raw mapping sample unavailable')
        for batch in pf.iter_batches(batch_size=8192, columns=list(COLS)+['raw_json'], use_threads=False):
            eth = pc.filter(batch, pc.equal(batch.column(batch.schema.get_field_index('symbol')), pa.scalar('ETH')))
            sample.extend(eth.slice(0, max(0, 64-len(sample))).to_pylist())
            if len(sample) == 64:
                break
        state.raw = inspect_raw(sample)
    return seen


def compare_hours(hours, official, day):
    start = stamp(day+'T00:00:00Z')
    o = {}
    errors = Counter()
    for b in official:
        if not isinstance(b, dict) or not isinstance(b.get('t'), int):
            errors['invalid_official_rows'] += 1
            continue
        t = b['t']*1000
        if start <= t < start+86400000000:
            if t in o:
                errors['duplicate_official_hours'] += 1
            o[t] = b
    mismatches = []
    for h in range(24):
        t = start+h*3600000000
        a, b = hours.get(t), o.get(t)
        diff = []
        if b is None:
            diff.append('missing_official')
        elif b.get('s') != 'ETH' or b.get('i') != '1h' or b.get('T') != b['t']+3599999:
            diff.append('official_market_or_interval')
        if a is None:
            diff.append('missing_derived')
        if a is not None and b is not None:
            try:
                diff += [k for k in ('o','h','l','c') if not close(a[k], b[k], relative=1e-9, absolute=1e-6)]
                if not close(a['v'], b['v'], relative=1e-6, absolute=1e-5):
                    diff.append('v')
                if a['n'] != b['n']:
                    diff.append('n')
            except (KeyError, ValueError, TypeError, ArithmeticError):
                diff.append('invalid_official_values')
        if diff:
            mismatches.append({'hour': h, 'fields': diff, 'derived': a, 'official': b})
    return {'official_hours': len(o), 'matched_hours': 24-len(mismatches), 'mismatches': mismatches, 'errors': dict(errors)}


def funding_coverage(official, day):
    start = stamp(day+'T00:00:00Z')//1000
    records = [r for r in official if isinstance(r, dict) and isinstance(r.get('time'), int) and start <= r['time'] < start+86400000]
    times = [r['time'] for r in records]
    valid = all(r.get('coin') == 'ETH' and Decimal(str(r.get('fundingRate'))).is_finite() for r in records)
    return {'rows': len(records), 'unique_times': len(set(times)), 'exact_hourly_grid': sorted(times) == [start+h*3600000 for h in range(24)], 'valid_rates': valid}


def classify(s, comparison, official_error):
    if not s.get('all_files_read'):
        return 'NOT_VERIFIED_INCOMPLETE_SCAN'
    if s.get('invalid_eth_rows', 0) or s.get('conflicting_trade_id_rows', 0):
        return 'REJECT_INCONSISTENT_TRADE_ROWS'
    if any(s.get('raw_mapping_sample', {}).get(k, 0) for k in ('identity_mismatch','numeric_mismatch','time_mismatch','side_mismatch')):
        return 'REJECT_RAW_MAPPING_MISMATCH'
    if official_error or comparison is None or comparison.get('official_hours') != 24 or comparison.get('errors'):
        return 'NOT_VERIFIED_OFFICIAL_COMPARISON'
    if comparison['matched_hours'] != 24:
        return 'REJECT_HOURLY_CROSSCHECK'
    return 'PRICE_RESEARCH_CANDIDATE' if s['eth_minutes'] == 1440 else 'PRICE_RESEARCH_CANDIDATE_WITH_EMPTY_MINUTES'


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def run(shard, out):
    import pyarrow as pa
    c = Client()
    out.mkdir(parents=True, exist_ok=True)
    raw, evidence = c.read(BASE+'_manifest.json', cap=8*MIB)
    require(evidence['sha256'] == MANIFEST_SHA, 'Pinned manifest hash mismatch')
    manifest = json.loads(raw)
    paths = manifest_paths(manifest)
    require(len(paths) == 93 and sum(map(len, paths.values())) == 3461, 'Pinned inventory differs from expected; stop for investigation')
    selected = shard_dates(paths, shard)
    require(selected, 'Empty shard')
    (out/'manifest.json').write_bytes(raw)
    start = stamp(selected[0]+'T00:00:00Z')//1000
    end = stamp(selected[-1]+'T00:00:00Z')//1000+86400000
    official, funding, official_error, funding_error = [], [], None, None
    for kind in ('candles', 'funding'):
        payload = ({'type':'candleSnapshot','req':{'coin':'ETH','interval':'1h','startTime':start,'endTime':end-1}}
                   if kind == 'candles' else {'type':'fundingHistory','coin':'ETH','startTime':start,'endTime':end-1})
        try:
            b, ev = c.read(INFO, payload=payload, cap=8*MIB)
            response = json.loads(b)
            require(isinstance(response, list), 'Official response is not a list')
            dump(out/(kind+'-request.json'), {'request':payload,'evidence':ev})
            (out/(kind+'-response.json')).write_bytes(b)
            if kind == 'candles': official = response
            else: funding = response
        except Exception as exc:
            if kind == 'candles': official_error = type(exc).__name__
            else: funding_error = type(exc).__name__
    results = []
    for d in selected:
        state = Day(d)
        files = []
        total_rows = 0
        for index, p in enumerate(paths[d]):
            item = {'path': p, 'status': 'NOT_READ'}
            try:
                raw, ev = c.read(BASE+urllib.parse.quote(p, safe='/='))
                item.update(ev)
                nr = decode_file(raw, state, raw_sample=index == 0)
                item.update(status='ALL_PROJECTED_ROWS_SCANNED', rows=nr)
                total_rows += nr
                del raw
            except Exception as exc:
                item.update(error_type=type(exc).__name__)
                if isinstance(exc, ValueError): item['detail'] = str(exc)[:140]
            files.append(item)
        s = state.summary()
        s.update(files_expected=len(paths[d]), files_read=sum(x['status']=='ALL_PROJECTED_ROWS_SCANNED' for x in files),
                 all_file_rows=total_rows, all_files_read=all(x['status']=='ALL_PROJECTED_ROWS_SCANNED' for x in files))
        s['manifest_rows'] = manifest.get('row_counts', {}).get('hyperliquid_chain/trades/'+d)
        s['manifest_row_count_matches'] = s['manifest_rows'] == total_rows
        comparison = compare_hours(state.bars(3600), official, d) if official else None
        s['hourly_comparison'] = comparison
        s['funding'] = funding_coverage(funding, d) if not funding_error else {'error':funding_error}
        s['status'] = classify(s, comparison, official_error)
        if s['all_files_read']:
            with gzip.open(out/(d+'-1m-bars.jsonl.gz'), 'wt') as f:
                for bar in state.bars(60).values():
                    f.write(json.dumps(bar, allow_nan=False)+'\n')
        dump(out/(d+'-files.json'), files)
        dump(out/(d+'-audit.json'), s)
        results.append(s)
        print('DAY_RESULT='+json.dumps({k:s.get(k) for k in ('date','status','files_read','files_expected','all_file_rows','eth_rows','unique_trade_ids','eth_minutes','all_symbol_minutes','invalid_eth_rows','conflicting_trade_id_rows','max_internal_gap_seconds')}), flush=True)
    summary = {'revision':REV, 'manifest_sha256':MANIFEST_SHA, 'shard':shard, 'shard_count':SHARDS,
               'inventory_days':list(paths), 'inventory_files':sum(map(len, paths.values())), 'date_runs':date_runs(paths),
               'selected_days':selected, 'downloaded_bytes':c.total, 'requests':c.calls, 'decoder':pa.__version__,
               'provenance':{k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')},
               'official_error':official_error, 'funding_error':funding_error, 'new_backtests':0,
               'status_counts':dict(Counter(x['status'] for x in results)),
               'all_selected_files_read':all(x['all_files_read'] for x in results)}
    dump(out/'shard-summary.json', summary)
    print('SHARD_RESULT='+json.dumps(summary), flush=True)
    return 0 if summary['all_selected_files_read'] else 1


def combine(root, out):
    out.mkdir(parents=True, exist_ok=True)
    summaries = [json.loads(p.read_text()) for p in sorted(root.glob('*/shard-summary.json'))]
    require(len(summaries) == SHARDS and sorted(s['shard'] for s in summaries) == list(range(SHARDS)), 'Missing/duplicate shards')
    anchor = summaries[0]
    for s in summaries:
        require(s['revision'] == REV and s['manifest_sha256'] == MANIFEST_SHA and s['inventory_days'] == anchor['inventory_days'], 'Inventory mismatch')
        require(s['provenance'] == anchor['provenance'], 'Mixed execution provenance')
        require(s['provenance']['GITHUB_SHA'] == os.environ.get('GITHUB_SHA') and s['provenance']['GITHUB_RUN_ID'] == os.environ.get('GITHUB_RUN_ID'), 'Stale run')
    days = [json.loads(p.read_text()) for p in sorted(root.glob('*/*-audit.json'))]
    days.sort(key=lambda d:d['date'])
    require([d['date'] for d in days] == anchor['inventory_days'], 'Missing/duplicate day reports')
    candidate = [d['date'] for d in days if d['status'].startswith('PRICE_RESEARCH_CANDIDATE')]
    summary = {'revision':REV, 'manifest_sha256':MANIFEST_SHA, 'provenance':anchor['provenance'], 'days':len(days),
               'files_expected':sum(d['files_expected'] for d in days), 'files_read':sum(d['files_read'] for d in days),
               'all_file_rows':sum(d['all_file_rows'] for d in days), 'eth_rows':sum(d.get('eth_rows',0) for d in days),
               'unique_trade_ids_within_days':sum(d['unique_trade_ids'] for d in days),
               'status_counts':dict(Counter(d['status'] for d in days)), 'inventory_date_runs':date_runs(anchor['inventory_days']),
               'candidate_date_runs':date_runs(candidate), 'candidate_days':len(candidate),
               'days_with_1440_eth_minutes':sum(d['eth_minutes']==1440 for d in days),
               'days_with_24_matching_hours':sum(d.get('hourly_comparison') is not None and d['hourly_comparison']['matched_hours']==24 for d in days),
               'funding_complete_days':sum(d['funding'].get('exact_hourly_grid',False) and d['funding'].get('valid_rates',False) for d in days),
               'downloaded_bytes':sum(s['downloaded_bytes'] for s in summaries), 'new_backtests':0,
               'limitations':['No L2/order-queue/spread or latency reconstruction', 'No actual historical fee-tier or oracle payment-price reconstruction',
                              'Raw fill mapping sampled at most 64 ETH rows per day; normalized trade columns scanned in every file',
                              'Equal timestamps use stable source order; no invented aggressor direction',
                              'Hourly volume tolerance: 1e-6 relative or 1e-5 ETH absolute; price tolerance: 1e-9 relative or 1e-6 absolute',
                              'Candidate means price-research evidence, not exact fills, profitability, or unseen holdout certification']}
    dump(out/'SUMMARY.json', summary)
    dump(out/'DAILY_DETAILS.json', days)
    fields = ['date','status','files_read','files_expected','all_file_rows','eth_rows','unique_trade_ids','eth_minutes','all_symbol_minutes','max_internal_gap_seconds','first_eth_utc','last_eth_utc','manifest_row_count_matches']
    with (out/'daily-audit.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields+['official_hours','matched_hours','funding_hour_grid'])
        w.writeheader()
        for d in days:
            r = {k:d.get(k) for k in fields}
            r.update(official_hours=(d.get('hourly_comparison') or {}).get('official_hours'), matched_hours=(d.get('hourly_comparison') or {}).get('matched_hours'), funding_hour_grid=d['funding'].get('exact_hourly_grid'))
            w.writerow(r)
    print('FULL_AUDIT_RESULT='+json.dumps(summary), flush=True)
    return 0


class Tests(unittest.TestCase):
    def row(self, tid='1', side='buy', t='2026-06-01T00:00:01Z', p=2000.0, q=0.5):
        return dict(symbol='ETH',provider='hyperliquid_chain',source_kind='hypercore_s3',exchange_time=t,recorded_at=t,price=p,size=q,notional_usd=p*q,trade_id=tid,side=side)
    def test_utc_precision(self): self.assertEqual(stamp('2026-06-01T00:00:00.000001Z')-stamp('2026-06-01T00:00:00Z'),1)
    def test_reject_timezone(self):
        with self.assertRaises(ValueError): stamp('2026-06-01T00:00:00+08:00')
    def test_url(self):
        for u in ('http://huggingface.co/x','https://evil.com/x','https://user:pass@huggingface.co/x','https://huggingface.co:8443/x'):
            with self.assertRaises(ValueError): safe_url(u)
    def test_manifest_string_dict(self):
        p='hyperliquid_chain/trades/date=2026-06-01/part-0000.parquet'
        self.assertEqual(manifest_paths({'files':[p]}),manifest_paths({'files':{p:{}}}))
    def test_manifest_duplicate(self):
        with self.assertRaises(ValueError): manifest_paths({'files':['a','a']})
    def test_shards_cover_once(self):
        days=[str(i) for i in range(93)]
        self.assertEqual([d for i in range(SHARDS) for d in shard_dates(days,i)],days)
    def test_paired_volume_once(self):
        d=Day('2026-06-01'); d.add(self.row()); d.add(self.row(side='sell'))
        b=next(iter(d.bars(60).values()))
        self.assertEqual((b['v'],b['n']),('0.5',1)); self.assertEqual(d.summary()['paired_opposite_side_ids'],1)
    def test_conflict(self):
        d=Day('2026-06-01');d.add(self.row());d.add(self.row(p=2001))
        self.assertEqual(d.counts['conflicting_trade_id_rows'],1)
    def test_invalid_values(self):
        d=Day('2026-06-01')
        for p in (0,-1,float('nan'),float('inf')): d.add(self.row(p=p))
        self.assertEqual(d.counts['invalid_eth_rows'],4)
    def test_outside_partition(self):
        d=Day('2026-06-01');d.add(self.row(t='2026-06-02T00:00:00Z'));self.assertEqual(d.counts['invalid_eth_rows'],1)
    def test_no_fill_missing_minutes(self):
        d=Day('2026-06-01');d.add(self.row());d.add(self.row(tid='2',t='2026-06-01T00:02:00Z'))
        self.assertEqual(len(d.bars(60)),2);self.assertEqual(d.summary()['missing_eth_minute_ranges'][0],[1,1])
    def test_ohlc_sorted(self):
        d=Day('2026-06-01');d.add(self.row(tid='2',t='2026-06-01T00:00:02Z',p=2001));d.add(self.row())
        b=next(iter(d.bars(60).values()));self.assertEqual((b['o'],b['h'],b['l'],b['c']),(2000,2001,2000,2001))
    def test_raw_privacy(self):
        r=self.row();r['raw_json']=json.dumps({'event':['SECRET_ADDRESS',{'coin':'ETH','px':'2000','sz':'0.5','tid':1,'side':'B','time':stamp(r['exchange_time'])//1000,'crossed':True}]})
        result=inspect_raw([r]);self.assertEqual(result['recognized_fill_rows'],1);self.assertNotIn('SECRET',json.dumps(result));self.assertEqual(result.get('time_mismatch',0),0)
    def test_raw_wrong_time(self):
        r=self.row();r['raw_json']=json.dumps({'coin':'ETH','px':'2000','sz':'0.5','tid':1,'side':'B','time':0})
        self.assertEqual(inspect_raw([r])['time_mismatch'],1)
    def test_official_missing(self): self.assertEqual(compare_hours({},[],'2026-06-01')['matched_hours'],0)
    def test_official_24_hours(self):
        st=stamp('2026-06-01T00:00:00Z');hours={};o=[]
        for h in range(24):
            t=st+h*3600000000;b=dict(t=t//1000,o=2000,h=2000,l=2000,c=2000,v='0.5',n=1);hours[t]=b;o.append(dict(b,T=t//1000+3599999,s='ETH',i='1h'))
        self.assertEqual(compare_hours(hours,o,'2026-06-01')['matched_hours'],24)
        o[3]['v']='1.0';self.assertEqual(compare_hours(hours,o,'2026-06-01')['matched_hours'],23)
    def test_funding_grid(self):
        st=stamp('2026-06-01T00:00:00Z')//1000;o=[dict(time=st+h*3600000,coin='ETH',fundingRate='0.0000125') for h in range(24)]
        self.assertTrue(funding_coverage(o,'2026-06-01')['exact_hourly_grid']);self.assertFalse(funding_coverage(o[:-1],'2026-06-01')['exact_hourly_grid'])
    def test_incomplete_not_candidate(self): self.assertEqual(classify({'all_files_read':False},None,None),'NOT_VERIFIED_INCOMPLETE_SCAN')
    def test_gaps_segment(self): self.assertEqual([r['days'] for r in date_runs(['2026-06-01','2026-06-02','2026-06-04'])],[2,1])
    def test_synthetic_parquet(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError: self.skipTest('Parquet decoder tested on isolated Actions runner')
        rows=[self.row(),self.row(side='sell')];buf=pa.BufferOutputStream();pq.write_table(pa.Table.from_pylist(rows),buf)
        d=Day('2026-06-01');self.assertEqual(decode_file(buf.getvalue().to_pybytes(),d),2);self.assertEqual(d.summary()['unique_trade_ids'],1)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');p.add_argument('--shard',type=int);p.add_argument('--combine',type=Path);p.add_argument('--out',type=Path,default=Path('audit-output'))
    a=p.parse_args()
    if a.self_test: unittest.main(argv=['audit'],verbosity=2)
    elif a.combine: raise SystemExit(combine(a.combine,a.out))
    else:
        require(a.shard is not None,'Shard required');raise SystemExit(run(a.shard,a.out))
