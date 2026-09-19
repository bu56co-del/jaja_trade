"""Independent public-data/ledger arithmetic. Does not import the replay engine."""
import bisect
import hashlib
import json
from decimal import Decimal as D, ROUND_CEILING
from pathlib import Path

OLD_COMMIT = '4cc3aa9239383a5a07e4f3305a9b2b17f61c9e57'
OLD_DATA = 'c9068cfe2f1c6ac7b415df24dfc9414250fcec10ef534f7b2a4aac2159e99717'
ENGINE_HASH = 'abe98cf655254613ba9462049122579e1fb26aa3343c844278f129acaa88aba1'
COST = {'base_assumptions': (D('0.00045'), D('0.00005'), D('0.0001')),
        'cost_stress': (D('0.0009'), D('0.00015'), D('0.0003'))}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def equal(a, b, name):
    a, b = D(str(a)), D(str(b))
    require(a.is_finite() and b.is_finite() and abs(a-b) <= D('1e-18')*max(D(1), abs(a), abs(b)), name)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load(path):
    path = Path(path)
    require(not path.is_symlink() and path.stat().st_size <= 20000000, 'Invalid evidence file')
    def pairs(items):
        out = {}
        for k, v in items:
            require(k not in out, 'Duplicate JSON key')
            out[k] = v
        return out
    def bad(value):
        raise ValueError('Nonfinite JSON ' + value)
    return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=bad)


def raw_check(data, rawdir):
    require(data['network'] == 'mainnet' and data['coin'] == 'ETH' and data['interval'] == '1m', 'Wrong dataset identity')
    responses = {}
    for line in (Path(rawdir)/'requests.jsonl').read_text().splitlines():
        item = json.loads(line)
        if item.get('status') != 'OK':
            continue
        require(item['endpoint'] == 'https://api.hyperliquid.xyz/info' and item['network'] == 'mainnet', 'Wrong raw endpoint')
        name = item['response_file']
        require(Path(name).name == name, 'Raw path escape')
        path = Path(rawdir)/name
        require(hashlib.sha256(path.read_bytes()).hexdigest() == item['response_sha256'], 'Raw hash mismatch')
        responses.setdefault(item['request']['type'], []).append(load(path))
    require(len(responses.get('candleSnapshot', [])) == 1 and len(responses.get('metaAndAssetCtxs', [])) == 1, 'Raw responses incomplete')
    candles = sorted(responses['candleSnapshot'][0], key=lambda b:b['t'])
    require(len(candles) == len(data['candles']), 'Raw candle count mismatch')
    for i, (raw, clean) in enumerate(zip(candles, data['candles'])):
        require(raw['s'] == 'ETH' and raw['i'] == '1m', 'Raw market mismatch')
        require(raw['t'] == clean['t'] and raw['T'] == clean['T'], 'Raw time mismatch')
        t = clean['t']
        require(type(t) is int and t % 60000 == 0 and clean['T'] == t+59999, 'Invalid candle interval')
        require(clean['T'] < data['downloaded_ms']-2000, 'Unclosed candle')
        require(not i or t-data['candles'][i-1]['t'] == 60000, 'Gap or duplicate')
        values = [D(clean[k]) for k in 'ohlc']
        require(all(v.is_finite() and v > 0 for v in values), 'Invalid candle price')
        require(D(clean['h']) == max(values) and D(clean['l']) == min(values), 'Bad OHLC')
        for k in 'ohlc':
            equal(raw[k], clean[k], 'Raw price mismatch')
    rawfund = [r for page in responses.get('fundingHistory', []) for r in page]
    require(len(rawfund) == len(data['funding']), 'Funding count mismatch')
    times = set()
    for r, c in zip(sorted(rawfund, key=lambda x:x['time']), data['funding']):
        require(r['coin'] == 'ETH' and r['time'] == c['time'], 'Funding identity mismatch')
        require(c['time']//3600000 not in times and abs(D(c['rate'])) <= D('.04'), 'Funding duplicate or cap')
        times.add(c['time']//3600000)
        equal(r['fundingRate'], c['rate'], 'Raw funding mismatch')
    lo, hi = (data['candles'][0]['t']+3599999)//3600000, data['candles'][-1]['T']//3600000
    require(set(range(lo, hi+1)) <= times, 'Missing funding hour')
    meta = [m for m in responses['metaAndAssetCtxs'][0][0]['universe'] if m['name']=='ETH']
    require(len(meta)==1 and meta[0]['szDecimals']==data['metadata']['sz_decimals'] and
            meta[0]['maxLeverage']==data['metadata']['max_leverage'], 'Metadata mismatch')
    return dict(status='PASS_RAW_NORMALIZED_INTERNAL_CONSISTENCY', candles=len(candles), funding=len(rawfund),
                market_facts_independent_source='NOT_VERIFIED', exact_historical_L2_oracle='NOT_VERIFIED')


def mean_line(values, period):
    out, a = [values[0]], D(2)/D(period+1)
    for value in values[1:]:
        out.append(value*a+out[-1]*(1-a))
    return out


def signal_check(history, trade, spec):
    closes = [D(b['c']) for b in history]
    a, b = mean_line(closes, spec['fast']), mean_line(closes, spec['slow'])
    direction = (1 if a[-2]<=b[-2] and a[-1]>b[-1] else
                 -1 if a[-2]>=b[-2] and a[-1]<b[-1] else 0)
    kind = spec.get('family', 'control')
    if kind == 'breakout':
        n = spec['length']
        above = closes[-1] > max(D(x['h']) for x in history[-n-1:-1])
        below = closes[-1] < min(D(x['l']) for x in history[-n-1:-1])
        direction = (1 if above and closes[-2]<=max(D(x['h']) for x in history[-n-2:-2]) else
                     -1 if below and closes[-2]>=min(D(x['l']) for x in history[-n-2:-2]) else 0)
    require(direction==trade['direction'] and direction!=0, 'Independent entry signal mismatch')
    if kind in ('breakout', 'efficiency'):
        n = spec.get('efficiency_length', spec['length'])
        series = closes[-n-1:]
        travel = sum((abs(series[i]-series[i-1]) for i in range(1,len(series))), D(0))
        er = abs(series[-1]-series[0])/travel if travel else D(0)
        require(er>=D(spec['threshold']), 'Efficiency filter mismatch')
    if kind == 'trend':
        groups = {}
        for bar in history:
            groups.setdefault(bar['t']//300000, []).append(bar)
        vals = []
        for key in sorted(groups):
            g = groups[key]
            if len(g)==5 and all(g[i]['t']==key*300000+i*60000 for i in range(5)):
                vals.append(D(g[-1]['c']))
        require(len(vals)>=spec['higher_slow']+2, 'Higher interval warmup missing')
        f, s = mean_line(vals,spec['higher_fast']), mean_line(vals,spec['higher_slow'])
        require(direction*(f[-1]-s[-1])>0 and direction*(f[-1]-f[-2])>0, 'Trend filter mismatch')
    tr = [max(D(history[i]['h'])-D(history[i]['l']), abs(D(history[i]['h'])-closes[i-1]),
              abs(D(history[i]['l'])-closes[i-1])) for i in range(1,len(history))]
    atr = sum(tr[-14:], D(0))/14
    equal(trade['signal_atr'], atr, 'ATR mismatch')
    equal(trade['signal_close'], closes[-1], 'Signal close mismatch')
    return max(D('.003'), atr/closes[-1]*D('1.5'))


def price_at(data, at, path):
    index = (at//60000*60000-data['candles'][0]['t'])//60000
    require(0<=index<len(data['candles']), 'Execution outside dataset')
    fields = {2000:'o',21000:'h',40000:'l',59998:'c'} if path=='OHLC' else {2000:'o',21000:'l',40000:'h',59998:'c'}
    require(at%60000 in fields, 'Execution is not a model sample')
    return D(data['candles'][index][fields[at%60000]])


def audit_row(row, data, spec):
    require(not row['open_position_remaining'] and not row['pending_funding'] and not row['integrity_warnings'], 'Incomplete/model-invalid result')
    fee, half, slip = COST[row['cost']]
    rows = row['trades']
    require(row['closed_trades']==len(rows), 'Wrong number of trades')
    rates = {r['time']:D(r['rate']) for r in data['funding']}
    ends = [b['T'] for b in data['candles']]
    total, fees, funding, values, last_close = D(0), D(0), D(0), [], -1
    for number, t in enumerate(rows,1):
        start, end, d, q = t['opened_ms'], t['closed_ms'], t['direction'], D(t['qty'])
        require(t['id']==number and row['start_ms']<=start<=end<=row['end_ms'] and start>=last_close, 'Trade sequence mismatch')
        require(d in (-1,1) and q>0 and start%60000==2000, 'Bad entry')
        last_close = end
        index = (start//60000*60000-data['candles'][0]['t'])//60000
        require(index>=120 and t['signal_bar_ms']==data['candles'][index-1]['T'], 'Signal sees future/missing warmup')
        distance = signal_check(data['candles'][index-120:index], t, spec)
        ref = price_at(data,start,row['path'])*(1+d*half)
        step = D(1).scaleb(-data['metadata']['sz_decimals'])
        expectedq = (D('10.10')/ref/step).to_integral_value(rounding=ROUND_CEILING)*step
        equal(q,expectedq,'Quantity mismatch')
        entry, exit_ = ref*(1+d*slip), price_at(data,end,row['path'])*(1-d*half)*(1-d*slip)
        equal(t['entry'],entry,'Entry price mismatch'); equal(t['exit'],exit_,'Exit price mismatch')
        equal(t['entry_raw_vwap'],ref,'Entry raw price')
        equal(t['exit_raw_vwap'],price_at(data,end,row['path'])*(1-d*half),'Exit raw price')
        equal(t['stop'],entry*(1-d*distance),'Stop mismatch')
        equal(t['target'],entry*(1+d*distance*D('1.8')),'Target mismatch')
        gross, ef, xf = d*q*(exit_-entry), q*entry*fee, q*exit_*fee
        equal(t['gross_pnl'],gross,'Gross mismatch'); equal(t['entry_fee'],ef,'Entry fee'); equal(t['exit_fee'],xf,'Exit fee')
        expected = {when for when in rates if start<when<=end}
        times = [f['time'] for f in t['funding_events']]
        require(len(times)==len(set(times)) and set(times)==expected,'Funding event mismatch')
        ft = D(0)
        for event in t['funding_events']:
            idx = bisect.bisect_right(ends,event['time'])-1
            require(idx>=0 and event['time']-ends[idx]<=90000,'Funding proxy unavailable')
            oracle = D(data['candles'][idx]['c'])
            equal(event['oracle'],oracle,'Funding proxy mismatch'); equal(event['rate'],rates[event['time']],'Funding rate mismatch')
            amount = -d*q*oracle*rates[event['time']]
            equal(event['amount'],amount,'Funding amount mismatch'); ft += amount
        value = gross-ef-xf+ft
        values.append(value); total += value; fees += ef+xf; funding += ft
    for actual, expected, name in [(row['model_net_pnl_usdc'],total,'Total net'),(row['model_end_usdc'],10+total,'Ending balance'),
                                  (row['fees_usdc'],fees,'Total fees'),(row['estimated_funding_usdc'],funding,'Total funding'),
                                  (row['model_return_pct'],total*10,'Return')]:
        equal(actual,expected,name)
    if rows:
        equal(row['win_rate_pct'],D(sum(v>0 for v in values))/len(rows)*100,'Win rate')
    else:
        require(row['win_rate_pct'] is None,'No trades has no win rate')
    # Independently sample liquidation-value equity, including opening/closing fees and funding.
    peak, dd = D(10), D(0)
    limit = max((t['closed_ms'] for t in rows),default=row['start_ms']-1)
    for bar in data['candles']:
        if bar['T']<row['start_ms'] or bar['t']>limit:
            continue
        for offset in (2000,21000,40000,59998):
            at = bar['t']+offset
            if not row['start_ms']<=at<=limit:
                continue
            value = D(10)
            for t in rows:
                if t['opened_ms']>at:
                    continue
                value -= D(t['entry_fee'])
                value += sum((D(f['amount']) for f in t['funding_events'] if f['time']<=at),D(0))
                if t['closed_ms']<=at:
                    value += D(t['gross_pnl'])-D(t['exit_fee'])
                else:
                    d,q = t['direction'],D(t['qty'])
                    markexit = price_at(data,at,row['path'])*(1-d*half)*(1-d*slip)
                    value += d*q*(markexit-D(t['entry']))-q*markexit*fee
            peak = max(peak,value); dd = max(dd,(peak-value)/peak)
    equal(row['model_max_drawdown_pct'],dd*100,'Drawdown mismatch')
    return dict(status='PASS_INDEPENDENT_MODEL_ARITHMETIC',trades=len(rows))


def baseline_check(root, application):
    root, application = Path(root), Path(application)
    provenance = load(root/'github-provenance.json')
    require(provenance['GITHUB_SHA']==OLD_COMMIT and provenance['GITHUB_RUN_ID']=='35452535157','Wrong baseline run')
    manifest = load(root/'source-manifest.json')['files']
    require(len(manifest)==20 and manifest['paperlab/engine.py']==ENGINE_HASH,'Wrong source manifest')
    for name, sha in manifest.items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts,'Source path escape')
        for base in (application, root/'reviewed-source'):
            require(hashlib.sha256((base/name).read_bytes()).hexdigest()==sha,'Source hash mismatch: '+name)
    data = load(root/'experiment/dataset.json')
    require(digest(data)==OLD_DATA,'Wrong baseline dataset')
    rawdir = root/'experiment/attempts/71e11c05be254544ab7cdc1da3928418/raw'
    checks = raw_check(data,rawdir)
    old = load(root/'experiment/results.json')
    expected = {(w,f'EMA{f}_{s}_{direction}',cost,path)
                for w in ('full','development_60pct','validation_20pct','audit_holdout_20pct')
                for f,s in ((5,13),(9,21),(12,26),(16,36))
                for direction in ('both','long_only','short_only') for cost in COST for path in ('OHLC','OLHC')}
    identities = [(r['window'],r['candidate'],r['cost'],r['path']) for r in old['results']]
    require(len(identities)==192 and set(identities)==expected,'Baseline grid incomplete')
    n=0
    for row in old['results']:
        pair=row['candidate'].split('_')
        audit_row(row,data,dict(fast=int(pair[0][3:]),slow=int(pair[1]),family='control'))
        n+=len(row['trades'])
    checks.update(source_files=20,baseline_scenarios=192,trade_scenario_records=n,
                  source_status='PASS',ledger_status='PASS',ledger_samples_are_independent=False)
    return data, old, checks


def overlap_check(old,new):
    require(old['network']==new['network']=='mainnet','Network mismatch')
    oldbars={b['t']:b for b in old['candles']}
    overlap=[b for b in new['candles'] if b['t'] in oldbars]
    require(bool(overlap),'No overlapping market observations to compare')
    for b in overlap:
        prior=oldbars[b['t']]
        require(b['T']==prior['T'],'Re-download time mismatch')
        for k in 'ohlc': equal(b[k],prior[k],'Re-download OHLC mismatch')
    prior={r['time']:r for r in old['funding']}
    common=[r for r in new['funding'] if r['time'] in prior]
    require(bool(common),'No overlapping funding records')
    for r in common: equal(r['rate'],prior[r['time']]['rate'],'Re-download funding mismatch')
    require(new['metadata']==old['metadata'],'Market metadata changed; explicit model migration required')
    return dict(status='PASS_SAME_API_REDOWNLOAD_OVERLAP',candles=len(overlap),funding=len(common),
                independent_market_source='NOT_VERIFIED',entire_old_window_refetched=len(overlap)==len(oldbars))
