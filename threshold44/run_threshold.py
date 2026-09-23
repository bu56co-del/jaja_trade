"""Fixed 3x3 threshold experiment. Whole paper accounts, all A44 episodes, offline."""
import argparse, csv, gzip, hashlib, json, os, time
from collections import Counter
from decimal import Decimal as D
from pathlib import Path
import run_entry as previous
import signals_threshold as strategy
import reference_threshold as independent
from inputs44 import need

PARENT_RUN = '35677804240'
PARENT_SHA = '54e21b5c92185a9dd63d52f1f04619069c55cf0d'
CASES = 972
mean = previous.mean


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def provenance(): return {k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}


def load_parent(root):
    blocks,checks,_ = previous.load_parent(root/'parent')
    summary = json.loads((root/'results/SUMMARY.json').read_text())
    need(summary['provenance'] == dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'), 'Wrong entry parent version')
    need(summary['cases']==432 and summary['baseline_equivalence']==108, 'Parent incomplete')
    manifest = json.loads((root/'results/evidence-manifest.json').read_text())
    for name,digest in manifest.items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts, 'Unsafe manifest path')
        need(sha(root/'results'/name)==digest, 'Parent evidence hash '+name)
    for name in ('run_entry.py','signals_entry.py','reference_entry.py'):
        need((root/'evidence/entry44-source'/name).read_bytes()==(Path(__file__).parents[1]/'entry44'/name).read_bytes(), 'Entry parent source differs')
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:
        rows = [json.loads(x) for x in f]
    expected = {(s['id'],f'block_{i:02d}',c,p) for s in previous.strategy.specs() for i in range(27) for c in mean.COSTS for p in mean.PATHS}
    need(len(rows)==432 and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected, 'Invalid parent grid')
    baseline = {(r['block_id'],r['cost'],r['path']):r for r in rows if r['candidate']=='VOL'}
    need(len(baseline)==108, 'Missing B baseline')
    checks['threshold_parent_results_sha256'] = sha(root/'results/all-results.jsonl.gz')
    return blocks,checks,baseline


def prepare(block):
    features = previous.original.features(block,15)
    raw = previous.original.choices(block,features,previous.filters.BASE_SPEC)
    ref = previous.original_ref.reference(block,previous.filters.BASE_SPEC)
    previous.original_ref.check(raw,ref)
    return raw,ref,previous.filters.extra(block)


def protocol():
    return dict(version='threshold-grid-v1',specs=strategy.specs(),baseline=strategy.BASELINE,primary=strategy.PRIMARY,
        parent_run=PARENT_RUN,parent_commit=PARENT_SHA,cases=CASES,days=44,episodes=27,
        signal_minutes=15,execution_minutes=1,costs=mean.COSTS,paths=mean.PATHS,
        rules='Full causal BB15 short setups with lower-half red close; raw1.5ATR/close >= min_vol; (modeled short fill-SMA20)/fill >= room_multiple*2*(fee+half spread+slippage). Equality allowed.',
        important='A looser0.25% admission gate does NOT lower the unchanged0.30% stop floor. Start from unfiltered BB setups, not only previously traded events. No micro delay.',
        risk='All prior account sizing, stop/1.8R target cap/360min, costs, funding proxy, cooldown and halts unchanged.',
        data='44 fixed dates,63360 minute bars,27 real contiguous blocks;600min warmup each,47160 execution-window minutes before halt effects.',
        capital='Separate virtual10USDC per block; sum/270 is average block return, NOT one10USDC continuous return.',
        selection='Compare complete net profit and same-trade cost stress, then samples/blocks/drawdown. A no-trade result cannot win. Keep all failures. No extra fine sweep after seeing results.',
        new_market_requests=0,orders=0,paid_services=0,schedule=False,
        limitations='Retrospective A44 development only. OHLC execution assumptions; no L2/mark/oracle/latency reconstruction or guaranteed future income.')


def aggregate(rows):
    expected={(s['id'],f'block_{i:02d}',c,p) for s in strategy.specs() for i in range(27) for c in mean.COSTS for p in mean.PATHS}
    need(len(rows)==CASES and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Incomplete/duplicate grid')
    out=[]
    for s in strategy.specs():
        for c in mean.COSTS:
            for p in mean.PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(s['id'],c,p)]
                ts=[t for r in rr for t in r['trades']]
                nets=[D(t['gross_pnl'])-D(t['entry_fee'])-D(t['exit_fee'])+sum((D(e['amount']) for e in t['funding_events']),D(0)) for t in ts]
                n=len(nets);total=sum(nets,D(0));win=sum(x>0 for x in nets)
                gains=sum((x for x in nets if x>0),D(0));losses=-sum((x for x in nets if x<0),D(0))
                a=dict(candidate=s['id'],min_vol=s['min_vol'],room_multiple=s['room_multiple'],cost=c,path=p,
                    trades=n,wins=win,net_win_rate=win/n if n else None,net_sum_usdc=str(total),
                    mean_net_usdc=str(total/n) if n else None,mean_episode_return_pct=str(total/270*100),
                    profit_factor=str(gains/losses) if losses else None,
                    positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),
                    negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),
                    no_trade_episodes=sum(not r['trades'] for r in rr),halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                    max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                    fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))))
                for k in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):
                    a[k]=str(sum((D(r['metrics'][k]) for r in rr),D(0)))
                a['exit_counts']=dict(sum((Counter(r['metrics']['exit_reasons']) for r in rr),Counter()))
                out.append(a)
    return out


def run(root,out):
    out.mkdir(parents=True,exist_ok=False);rows=[];started=time.monotonic();equiv=0
    write(out/'protocol.json',protocol());prov=provenance();write(out/'provenance.json',prov)
    write(out/'code-hashes.json',{p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    try:
        blocks,checks,old=load_parent(root);write(out/'data-checks.json',checks)
        coverage=previous.parent.coverage(blocks);write(out/'coverage.json',coverage)
        with (out/'coverage.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(coverage[0]));w.writeheader();w.writerows(coverage)
        with gzip.open(out/'all-results.jsonl.gz','wt') as saved,gzip.open(out/'events.jsonl.gz','wt') as events:
            for block in blocks:
                raw,refraw,extras=prepare(block)
                for cost in mean.COSTS:
                    for spec in strategy.specs():
                        choices,trace=strategy.choices(block,spec,mean.COSTS[cost],raw,extras)
                        ref,reft=independent.reference(block,spec,cost,refraw)
                        independent.check(choices,ref,trace,reft)
                        events.write(json.dumps(dict(candidate=spec['id'],block_id=block['id'],cost=cost,events=trace),allow_nan=False)+'\n')
                        for path in mean.PATHS:
                            need(time.monotonic()-started<1260,'Time budget')
                            row=mean.replay(block,previous.filters.BASE_SPEC,cost,path,choices,ref)
                            if spec['id']==strategy.BASELINE:
                                prior=old[(block['id'],cost,path)]
                                need(all(row[k]==prior[k] for k in row if k!='candidate'),'B baseline account drift')
                                equiv+=1
                            need(all(t['direction']==-1 for t in row['trades']),'Unexpected long')
                            row.update(candidate=spec['id'],threshold_spec=spec,parent_candidate='VOL')
                            saved.write(json.dumps(row,allow_nan=False)+'\n');saved.flush();rows.append(row)
                print('BLOCK_DONE',block['id'],len(rows),round(time.monotonic()-started,2),flush=True)
        need(equiv==108,'Missing baseline replay')
        agg=aggregate(rows);write(out/'aggregate.json',agg)
        with (out/'aggregate.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(agg[0]));w.writeheader();w.writerows(agg)
        base=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC']
        summary=dict(status='COMPLETED_THRESHOLD_GRID',provenance=prov,cases=len(rows),candidates=9,days=44,episodes=27,
            input_minutes=63360,warmup_minutes=16200,execution_window_minutes=47160,baseline_equivalence=equiv,
            primary=strategy.PRIMARY,baseline=strategy.BASELINE,trades=sum(len(r['trades']) for r in rows),
            funding_events=sum(len(t['funding_events']) for r in rows for t in r['trades']),base_results=base,
            best_trading_base=max((a for a in base if a['trades']),key=lambda a:D(a['net_sum_usdc']),default=None),
            all_model_costs_positive=[s['id'] for s in strategy.specs() if all(D(a['net_sum_usdc'])>0 and D(a['fixed_trade_stress_sum_usdc'])>0 for a in agg if a['candidate']==s['id'])],
            new_market_requests=0,real_orders=0)
        write(out/'SUMMARY.json',summary);write(out/'status.json',{'status':summary['status']})
        print('FINAL_SUMMARY',json.dumps(summary),flush=True);return 0
    except Exception as e:
        write(out/'status.json',dict(status='FAILED_NO_COMPLETE_RESULT',error_type=type(e).__name__,error=str(e),completed_cases=len(rows)))
        raise
    finally:
        write(out/'evidence-manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();raise SystemExit(run(a.parent,a.out))
