"""One bounded four-definition experiment, saved A44 and September; no trading/network."""
import argparse
from collections import Counter
from decimal import Decimal as D
from pathlib import Path
import csv, gzip, hashlib, json, os, time
import run_compare as cross
import signals_structure as strategy
import reference_structure as independent
from inputs44 import need

PARENT_RUN = '35683448454'
PARENT_SHA = 'c65d35732046228bf250e5cedbd92577540e97c6'
PARENT_MANIFEST_SHA = '451490ca00c64f4fa4b44df3834928e399699ae3bdbd44dc855147b04eb6e9d5'
CASES = 448
mean = cross.old.mean


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance():
    return {k: os.environ.get(k) for k in ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')}


def read_rows(path):
    with gzip.open(path, 'rt') as stream:
        return [json.loads(line) for line in stream]


def load_parent(root):
    root = Path(root)
    summary = cross.history.read(root/'results/SUMMARY.json')
    need(summary['provenance'] == dict(GITHUB_SHA=PARENT_SHA, GITHUB_RUN_ID=PARENT_RUN, GITHUB_RUN_ATTEMPT='1'), 'Wrong cross-period parent')
    need(summary['new_cases'] == 8 and summary['regression_cases'] == 216, 'Incomplete parent')
    need(sha(root/'results/evidence-manifest.json') == PARENT_MANIFEST_SHA, 'Pinned parent manifest mismatch')
    for name, digest in cross.history.read(root/'results/evidence-manifest.json').items():
        need(sha(cross.history.child(root/'results', name)) == digest, 'Parent result hash '+name)
    for name in ('run_compare.py', 'history.py', 'source-pins.json'):
        need((root/'evidence/crossperiod-source'/name).read_bytes() == (Path(__file__).parents[1]/'crossperiod'/name).read_bytes(), 'Cross-period source drift')
    blocks, checks, old_a44 = cross.load_parent(root/'parent')
    september, data = cross.history.load(root/'september-original', root/'september-native')
    need(september == cross.history.read(root/'results/input-block.json'), 'September input drift')
    need(not set(september['days']).intersection(checks['days']), 'Overlapping datasets')
    saved = {('A44', *key): row for key, row in old_a44.items()}
    for row in read_rows(root/'results/SEPTEMBER_COMPARISON.jsonl.gz'):
        key = ('SEPTEMBER', row['candidate'], row['block_id'], row['cost'], row['path'])
        need(key not in saved, 'Duplicate parent baseline')
        saved[key] = row
    need(len(saved) == 224, 'Parent baseline coverage')
    return [('A44', b) for b in blocks]+[('SEPTEMBER', september)], dict(a44=checks, september=data), saved


def compare_baseline(row, previous):
    for key in row:
        if key not in ('candidate', 'evidence'):
            need(row[key] == previous[key], 'Frozen account drift: '+key)


def protocol():
    return dict(version='two-confirmed-peaks-v1', specs=strategy.specs(), primary=strategy.PRIMARY,
                parent_run=PARENT_RUN, parent_commit=PARENT_SHA, cases=CASES, regression_cases=224,
                signal_minutes=15, execution_minutes=1, costs=mean.COSTS, paths=mean.PATHS,
                pivot='Strict high > each adjacent high, one closed bar on each side; equal highs excluded. At i complete bars, k<=i-2; k in [i-12,i-2], most recent two only.',
                z='(high[k]-SMA20(closes through k))/population SD20; zero SD is undefined. Z decrease must exceed1e-12. No hindsight pair selection or signal delay.',
                data='All fixed A44 63360 minutes in27 blocks, plus saved September5490 minutes in1 block. First600 minutes warm up in every block; no gaps bridged.',
                capital='Independent10USDC per true contiguous block. A44 sum/270 is mean block return; September net/10 is continuous period return. Never concatenate across datasets.',
                controls='Original V030_R30 and V035_R30 unchanged; structure variants use V035_R30 admission and original stop,1.8R target,360min,costs,qty,cooldown,halts.',
                selection='Report both periods, every failure/no-trade case, net and fixed-trade stress. Zero trades is not evidence of profit. No automatic promotion, tuning, or deployment.',
                limitations='Already examined historical development data; no exact L2/mark/oracle/queue/latency reconstruction; modeled OHLC paths are not guaranteed bounds.',
                new_market_requests=0, orders=0, paid_services=0, schedule=False)


def aggregate(rows):
    expected = {(s['id'], dataset, block, cost, path)
                for s in strategy.specs()
                for dataset, blocks in [('A44', [f'block_{i:02d}' for i in range(27)]), ('SEPTEMBER', ['saved_september_union'])]
                for block in blocks for cost in mean.COSTS for path in mean.PATHS}
    # Derive the single September block name from rows, not a guessed hard-coded ID.
    sept_ids = {r['block_id'] for r in rows if r['dataset'] == 'SEPTEMBER'}
    need(len(sept_ids) == 1, 'Missing/duplicate September block')
    sept_id = next(iter(sept_ids))
    expected = {(s, ds, sept_id if ds=='SEPTEMBER' else b, c, p) for s, ds, b, c, p in expected}
    actual = {(r['candidate'], r['dataset'], r['block_id'], r['cost'], r['path']) for r in rows}
    need(len(rows) == CASES and actual == expected, 'Incomplete/duplicate experiment')
    result = []
    for spec in strategy.specs():
        for dataset, count in [('A44', 27), ('SEPTEMBER', 1)]:
            for cost in mean.COSTS:
                for path in mean.PATHS:
                    selected = [r for r in rows if (r['candidate'], r['dataset'], r['cost'], r['path']) == (spec['id'], dataset, cost, path)]
                    nets = [cross.net(t) for r in selected for t in r['trades']]
                    total, n = sum(nets, D(0)), len(nets)
                    gains = sum((v for v in nets if v>0), D(0))
                    losses = -sum((v for v in nets if v<0), D(0))
                    value = dict(candidate=spec['id'], dataset=dataset, cost=cost, path=path, blocks=count,
                                 trades=n, wins=sum(v>0 for v in nets), losses=sum(v<0 for v in nets),
                                 net_win_rate=sum(v>0 for v in nets)/n if n else None, net_sum_usdc=str(total),
                                 mean_net_usdc=str(total/n) if n else None,
                                 mean_block_return_pct=str(total/(10*count)*100),
                                 profit_factor=str(gains/losses) if losses else None,
                                 positive_blocks=sum(D(r['metrics']['net_usdc'])>0 for r in selected),
                                 negative_blocks=sum(D(r['metrics']['net_usdc'])<0 for r in selected),
                                 no_trade_blocks=sum(not r['trades'] for r in selected),
                                 halted_blocks=sum(bool(r['halt_reason']) for r in selected),
                                 max_single_block_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in selected)),
                                 fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in selected), D(0))),
                                 exit_counts=dict(sum((Counter(r['metrics']['exit_reasons']) for r in selected), Counter())))
                    for key in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):
                        value[key] = str(sum((D(r['metrics'][key]) for r in selected), D(0)))
                    result.append(value)
    return result


def coverage(pairs):
    records = []
    for dataset, block in pairs:
        for day in block['days']:
            minutes = [b for b in block['candles'] if cross.history.iso(b['t'])[:10] == day]
            warm = sum(b['t'] < block['start'] for b in minutes)
            records.append(dict(dataset=dataset, date_UTC=day, block_id=block['id'], input_minutes=len(minutes),
                                warmup_minutes=warm, execution_window_minutes=len(minutes)-warm))
    need(len(records) == 48 and sum(r['input_minutes'] for r in records) == 68850, 'Date coverage drift')
    need(sum(r['warmup_minutes'] for r in records) == 16800, 'Warmup drift')
    return records


def run(parent, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    began, rows, equivalence = time.monotonic(), [], 0
    write(out/'protocol.json', protocol())
    write(out/'provenance.json', provenance())
    write(out/'code-hashes.json', {p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    try:
        pairs, checks, saved = load_parent(parent)
        write(out/'data-checks.json', checks)
        covered = coverage(pairs)
        cross.csvfile(out/'coverage.csv', covered)
        with gzip.open(out/'all-results.jsonl.gz', 'wt') as output, gzip.open(out/'events.jsonl.gz', 'wt') as events:
            for dataset, block in pairs:
                raw, rawref, extras = cross.old.prepare(block)
                context = strategy.contexts(block)
                for spec in strategy.specs():
                    for cost in mean.COSTS:
                        cs, traces = strategy.choices(block, spec, mean.COSTS[cost], raw, extras, context)
                        ref, rt = independent.reference(block, spec, cost, rawref)
                        independent.check(cs, ref, traces, rt)
                        events.write(json.dumps(dict(dataset=dataset, block_id=block['id'], candidate=spec['id'], cost=cost, events=traces), allow_nan=False)+'\n')
                        for path in mean.PATHS:
                            need(time.monotonic()-began < 1260, 'Research deadline')
                            row = mean.replay(block, cross.old.previous.filters.BASE_SPEC, cost, path, cs, ref)
                            if spec['pattern'] == 'NONE':
                                compare_baseline(row, saved[(dataset, spec['parent'], block['id'], cost, path)])
                                equivalence += 1
                            row.update(candidate=spec['id'], structure_spec=spec, dataset=dataset,
                                       evidence='RETROSPECTIVE_STRUCTURE_MODEL_ONLY')
                            empty = {b['t'] for b in block['candles'] if b['n']==0}
                            row['zero_volume_fill_events'] = sum(t[k]//60000*60000 in empty for t in row['trades'] for k in ('opened_ms','closed_ms'))
                            need(row['zero_volume_fill_events'] == 0, 'Unverified fill in an empty minute')
                            output.write(json.dumps(row, allow_nan=False)+'\n')
                            output.flush()
                            rows.append(row)
                print('BLOCK_DONE', dataset, block['id'], len(rows), round(time.monotonic()-began, 2), flush=True)
        need(equivalence == 224, 'Baseline count drift')
        agg = aggregate(rows)
        write(out/'aggregate.json', agg)
        cross.csvfile(out/'aggregate.csv', agg)
        summary = dict(status='COMPLETED_STRUCTURE_COMPARISON', provenance=provenance(), cases=len(rows),
                       new_structure_cases=224, baseline_equivalence=equivalence, definitions=strategy.specs(),
                       primary=strategy.PRIMARY, input_minutes=68850, warmup_minutes=16800, execution_window_minutes=52050,
                       trade_scenario_records=sum(len(r['trades']) for r in rows),
                       funding_event_scenario_records=sum(len(t['funding_events']) for r in rows for t in r['trades']),
                       base_results=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC'],
                       new_market_requests=0, orders=0, paid_services=0, exact_live_execution='NOT_VERIFIED')
        write(out/'SUMMARY.json', summary)
        write(out/'status.json', dict(status=summary['status']))
        print('STRUCTURE_RESULT', json.dumps(summary), flush=True)
        return 0
    except Exception as error:
        write(out/'status.json', dict(status='FAILED_NO_COMPLETE_RESULT', error_type=type(error).__name__,
                                     error=str(error), completed_cases=len(rows), baseline_equivalence=equivalence))
        raise
    finally:
        write(out/'evidence-manifest.json', {str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.parent, args.out))
