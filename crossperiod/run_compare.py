"""Frozen V030_R30 versus V035_R30 on saved September data; no strategy changes."""
import argparse, csv, gzip, json, os, time
from pathlib import Path
from decimal import Decimal as D
from collections import Counter
import run_threshold as old
import history
from inputs44 import need

IDS=('V030_R30','V035_R30')
PARENT_RUN='35681103912'
PARENT_SHA='f1a086139c48a81126b5a8fb6c9512cf6eca737e'


def specs(): return [s for s in old.strategy.specs() if s['id'] in IDS]
def write(path,x): path.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def net(t): return D(t['gross_pnl'])-D(t['entry_fee'])-D(t['exit_fee'])+sum((D(e['amount']) for e in t['funding_events']),D(0))
def csvfile(path,rows):
    need(rows,'Empty report')
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def load_parent(root):
    root=Path(root);s=history.read(root/'results/SUMMARY.json')
    expected=dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1')
    need(s['provenance']==expected and s['cases']==972 and s['baseline_equivalence']==108,'Wrong threshold parent')
    for name,digest in history.read(root/'results/evidence-manifest.json').items():
        need(history.sha(history.child(root/'results',name))==digest,'Threshold parent evidence hash')
    for name in ('run_threshold.py','signals_threshold.py','reference_threshold.py'):
        need((root/'evidence/threshold44-source'/name).read_bytes()==(Path(__file__).parents[1]/'threshold44'/name).read_bytes(),'Frozen threshold source drift '+name)
    blocks,checks,_=old.load_parent(root/'parent')
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:rows=[json.loads(line) for line in f]
    need(len(rows)==972,'Wrong parent case count')
    selected={(r['candidate'],r['block_id'],r['cost'],r['path']):r for r in rows if r['candidate'] in IDS}
    need(len(selected)==216,'Incomplete frozen baselines')
    return blocks,checks,selected

def protocol():
    return dict(version='frozen-cross-period-v1',specs=specs(),parent_run=PARENT_RUN,parent_commit=PARENT_SHA,
        new_cases=8,regression_cases=216,signal_minutes=15,execution_minutes=1,
        costs=old.mean.COSTS,paths=old.mean.PATHS,
        input_rule='Full union of the two pinned old official ETH1m downloads. Exact overlap required. Only incomplete15m edge trimmed; first600min warmup.',
        comparison='No strategy, thresholds, costs, position, exit, cooldown or halt changes. Two complete accounts, each replayed under two cost and two path models.',
        capital='One separate virtual10USDC per new continuous September scenario; no daily reset. Never join A44 results to this account.',
        data_status='Different dates from A44, previously used for other strategies. Not fresh unseen holdout or forward results.',
        zero_volume='Two official flat zero-volume candles are preserved and flagged; not invented fills or forward-filled data.',
        selection='Keep every result including zero trades and losses. No parameter search or automatic strategy promotion.',
        new_market_requests=0,orders=0,paid_services=0,schedule=False)

def compare_rows(row,saved):
    need(all(row[k]==saved[k] for k in row if k!='candidate'),'Frozen A44 ledger regression')

def summarize(row):
    m=row['metrics'];n=m['trades'];nets=[net(t) for t in row['trades']]
    gains=sum((x for x in nets if x>0),D(0));losses=-sum((x for x in nets if x<0),D(0))
    return dict(candidate=row['candidate'],cost=row['cost'],path=row['path'],trades=n,wins=m['wins'],losses=m['losses'],
        net_win_rate=m['net_win_rate'],net_usdc=m['net_usdc'],initial_usdc=row['initial_usdc'],ending_usdc=row['ending_usdc'],
        return_pct=str(D(m['net_usdc'])/D(row['initial_usdc'])*100),
        mean_net_usdc=str(sum(nets)/n) if n else None,profit_factor=str(gains/losses) if losses else None,
        fixed_trade_stress_net_usdc=m['fixed_trades_stress_net_usdc'],fixed_trade_stress_win_rate=m['fixed_trades_stress_win_rate'],
        drawdown_pct=row['sampled_max_drawdown_pct'],halt_reason=row['halt_reason'],halt_ms=row['halt_ms'],
        halted_fraction=row['halted_fraction'],potential_signals=row['potential_signals'],
        exposure_minutes=sum((t['closed_ms']-t['opened_ms'])/60000 for t in row['trades']),
        exit_reasons=m['exit_reasons'],official_zero_volume_fill_events=row['official_zero_volume_fill_events'],
        **{k:m[k] for k in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc')})

def daily(block,rows):
    answer=[]
    for row in rows:
        for day in block['days']:
            bs=[b for b in block['candles'] if history.iso(b['t'])[:10]==day]
            start=max(bs[0]['t'],block['start']);end=bs[-1]['T']+1
            opened=[t for t in row['trades'] if history.iso(t['opened_ms'])[:10]==day]
            closed=[t for t in row['trades'] if history.iso(t['closed_ms'])[:10]==day]
            answer.append(dict(candidate=row['candidate'],cost=row['cost'],path=row['path'],date_UTC=day,
                input_minutes=len(bs),warmup_minutes=sum(b['t']<block['start'] for b in bs),execution_window_minutes=max(0,end-start)//60000,
                entries=len(opened),closed_trades=len(closed),closed_trade_net_usdc=str(sum((net(t) for t in closed),D(0))),
                halted_minutes=max(0,end-max(start,row['halt_ms']))/60000 if row['halt_ms'] else 0,
                note='Daily attribution of complete closed-trade net; not daily mark-to-market return.'))
    return answer

def run(parent,original,native,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);began=time.monotonic();results=[];equiv=0
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    write(out/'protocol.json',protocol());write(out/'provenance.json',prov)
    try:
        a44,checks,saved=load_parent(parent);block,data=history.load(original,native)
        need(not(set(block['days'])&set(checks['days'])),'September overlaps A44')
        write(out/'input-block.json',block);write(out/'data-checks.json',dict(september=data,a44=checks))
        for subset,blocks in [('A44_REGRESSION',a44),('SEPTEMBER_COMPARISON',[block])]:
            with gzip.open(out/(subset+'.jsonl.gz'),'wt') as f,gzip.open(out/(subset+'-events.jsonl.gz'),'wt') as event:
                for b in blocks:
                    raw,rawref,extras=old.prepare(b)
                    for spec in specs():
                        for cost in old.mean.COSTS:
                            choices,trace=old.strategy.choices(b,spec,old.mean.COSTS[cost],raw,extras)
                            ref,reft=old.independent.reference(b,spec,cost,rawref)
                            old.independent.check(choices,ref,trace,reft)
                            event.write(json.dumps(dict(candidate=spec['id'],block_id=b['id'],cost=cost,events=trace))+'\n')
                            for path in old.mean.PATHS:
                                need(time.monotonic()-began<1260,'Research deadline')
                                r=old.mean.replay(b,old.previous.filters.BASE_SPEC,cost,path,choices,ref)
                                if subset=='A44_REGRESSION':
                                    compare_rows(r,saved[(spec['id'],b['id'],cost,path)]);equiv+=1
                                r.update(candidate=spec['id'],threshold_spec=spec,dataset=subset)
                                if subset=='SEPTEMBER_COMPARISON':
                                    r['evidence']='SAVED_SEPTEMBER_CROSS_PERIOD_MODEL_ONLY'
                                    empty={bar['t'] for bar in b['candles'] if bar['n']==0}
                                    r['official_zero_volume_fill_events']=sum(t[k]//60000*60000 in empty for t in r['trades'] for k in ('opened_ms','closed_ms'))
                                    r['held_during_official_zero_volume_minutes']=sum(any(t['opened_ms']<ms+60000 and t['closed_ms']>=ms for t in r['trades']) for ms in empty)
                                    results.append(r)
                                f.write(json.dumps(r,allow_nan=False)+'\n');f.flush()
                    print('BLOCK_DONE',subset,b['id'],'regressions',equiv,'new',len(results),flush=True)
        need(equiv==216 and len(results)==8,'Incomplete comparison')
        expected={(s,c,p) for s in IDS for c in old.mean.COSTS for p in old.mean.PATHS}
        need({(r['candidate'],r['cost'],r['path']) for r in results}==expected,'Missing new scenario')
        agg=[summarize(r) for r in results];write(out/'comparison.json',agg);csvfile(out/'comparison.csv',agg)
        day=daily(block,results);write(out/'daily.json',day);csvfile(out/'daily.csv',day)
        summary=dict(status='COMPLETED_FROZEN_CROSS_PERIOD',provenance=prov,new_cases=8,regression_cases=equiv,definitions=list(IDS),
            input_minutes=data['input_minutes'],warmup_minutes=600,execution_window_minutes=data['execution_window_minutes'],
            input_start=data['input_start'],execution_start=data['execution_start'],end_exclusive=data['end_exclusive'],
            new_trade_scenario_records=sum(len(r['trades']) for r in results),
            new_funding_event_records=sum(len(t['funding_events']) for r in results for t in r['trades']),
            baseline_results=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC'],
            zero_volume_fill_events=sum(r['official_zero_volume_fill_events'] for r in results),new_market_requests=0,orders=0,
            exact_live_execution='NOT_VERIFIED',unseen_holdout=False)
        write(out/'SUMMARY.json',summary);write(out/'status.json',{'status':summary['status']})
        print('CROSS_PERIOD_RESULT',json.dumps(summary),flush=True);return 0
    except Exception as e:
        write(out/'status.json',dict(status='FAILED_NO_COMPLETE_RESULT',error_type=type(e).__name__,error=str(e),new_cases=len(results),regression_cases=equiv))
        raise
    finally:
        write(out/'evidence-manifest.json',{str(p.relative_to(out)):history.sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True);p.add_argument('--original',type=Path,required=True);p.add_argument('--native',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();raise SystemExit(run(a.parent,a.original,a.native,a.out))
