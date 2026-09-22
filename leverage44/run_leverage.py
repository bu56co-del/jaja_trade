"""Finite 5x A44 experiment; fixed B signals and complete chronological accounts."""
import argparse, csv, gzip, hashlib, json, os, time
from dataclasses import asdict
from pathlib import Path
from decimal import Decimal as D
from collections import Counter
import run_entry as parent
from risk5 import MODES, configuration, ExposureEngine
import reference5
from inputs44 import need

PARENT_RUN='35677804240'
PARENT_SHA='54e21b5c92185a9dd63d52f1f04619069c55cf0d'
CASES=432
mean=parent.mean
B=next(s for s in parent.strategy.specs() if s['id']=='VOL')


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def csvfile(p,rows):
    need(rows,'Empty CSV')
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def load(root):
    blocks,checks,_=parent.load_parent(root/'parent')
    s=json.loads((root/'results/SUMMARY.json').read_text())
    need(s['provenance']==dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'),'Wrong parent identity')
    need(s['cases']==432 and s['baseline_equivalence']==108,'Incomplete parent')
    for name,digest in json.loads((root/'results/evidence-manifest.json').read_text()).items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts,'Unsafe evidence path')
        need(sha(root/'results'/name)==digest,'Parent result changed')
    for name in ('run_entry.py','signals_entry.py','reference_entry.py'):
        need((root/'evidence/entry44-source'/name).read_bytes()==(Path(__file__).parents[1]/'entry44'/name).read_bytes(),'Parent source drift')
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f: rr=[json.loads(line) for line in f]
    need(len(rr)==432,'Parent cases')
    baseline={(r['block_id'],r['cost'],r['path']):r for r in rr if r['candidate']=='VOL'}
    need(len(baseline)==108,'Missing B baseline')
    return blocks,checks,baseline


def prepare(block,cost):
    base,refbase,extras,_=parent.prepare(block,cost)
    cs,events=parent.strategy.choices(block,B,mean.COSTS[cost],base,extras)
    ref,reft=parent.independent.reference(block,B,cost,refbase)
    parent.independent.check(cs,ref,events,reft)
    return cs,ref


def replay(block,mode,cost_name,path,cs,ref):
    need(mode in MODES and cost_name in mean.COSTS and path in mean.PATHS,'Unknown scenario')
    cost=mean.COSTS[cost_name];cfg=configuration(mode,cost);start,end=block['start'],block['end'];bars=block['candles']
    holder={'spec':parent.filters.BASE_SPEC}
    e=ExposureEngine(cfg,mean.core.new_state(cfg,start+2000,'A44_5X_OFFLINE_WHATIF'),parent.filters.BASE_SPEC,mean.MODE,holder)
    holder['engine']=e;e.ingest_funding(block['funding']);fund=mean.FundingPrices({'candles':bars,'funding':block['funding']})
    times=[r['time'] for r in block['funding']];pointer=0;halt=None;q=None
    with mean.installed(holder):
        for j,b in enumerate(bars):
            if b['t']<start: continue
            for field,off in zip('ohlc' if path=='OHLC' else 'olhc',(2000,21000,40000,59998)):
                q=mean.assumed_quote(b['t']+off,b[field],cost['spread_bps'],block['metadata'])
                if pointer<len(times) and times[pointer]<=q.observed_ms:
                    e.reconcile_funding(fund,q.observed_ms)
                    while pointer<len(times) and times[pointer]<=q.observed_ms:pointer+=1
                history=None
                if field=='o':holder.update(i=j,choice=cs[j]);history=bars[j-120:j]
                e.tick(q,history);e.update_drawdown(q)
                if e.state['halt_reason'] and halt is None:halt=q.observed_ms
            if halt and not e.position:break
        if e.position:
            e.close_position(q,'BACKTEST_SEGMENT_END_ASSUMED_FILL');e.update_drawdown(q)
            if e.state['halt_reason'] and halt is None:halt=q.observed_ms
        e.reconcile_funding(fund,q.observed_ms)
    row=dict(candidate=mode,leverage_mode=mode,strategy_spec=parent.filters.BASE_SPEC,block_id=block['id'],days=block['days'],mode=mean.MODE,cost=cost_name,path=path,
        start_ms=start+2000,end_ms=end-2,last_observation_ms=q.observed_ms,initial_usdc='10',ending_usdc=str(e.cash),
        account_config=asdict(cfg),halt_reason=e.state['halt_reason'],halt_ms=halt,
        halted_fraction=(end-2-halt)/(end-2-start-2000) if halt else 0,
        sampled_max_drawdown_pct=str(D(e.state['max_drawdown_fraction'])*100),open_position=e.position is not None,
        pending_funding=e.state['pending_funding'],integrity_warnings=e.state['integrity_warnings'],trades=e.state['trades'],
        metrics=mean.paired.summarize(e.state['trades'],cost_name,mean.MODE),decisions=dict(e.counts),
        potential_signals=sum(r['direction']!=0 for r in cs.values()),sizing_attempts=e.attempts,margin_diagnostic=e.margin_summary(),
        evidence='RETROSPECTIVE_27_SEPARATE_ACCOUNTS_NOT_A_CONTINUOUS_44_DAY_RETURN')
    row['independent_audit']=reference5.audit(row,block,ref)
    return row


def baseline_check(row,old):
    for k in ('trades','ending_usdc','halt_reason','halt_ms','sampled_max_drawdown_pct','metrics','decisions','potential_signals'):
        need(row[k]==old[k],'Baseline drift '+k)


def aggregate(rows):
    expected={(m,f'block_{i:02d}',c,p) for m in MODES for i in range(27) for c in mean.COSTS for p in mean.PATHS}
    need(len(rows)==CASES and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Incomplete or duplicate grid')
    out=[]
    for mode in MODES:
        for c in mean.COSTS:
            for p in mean.PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(mode,c,p)]
                n=sum(len(r['trades']) for r in rr);w=sum(r['metrics']['wins'] for r in rr)
                total=sum((D(r['metrics']['net_usdc']) for r in rr),D(0))
                attempts=[a for r in rr for a in r['sizing_attempts']];filled=[a for a in attempts if not a['rejected_by']]
                margins=[D(r['margin_diagnostic']['minimum_buffer_usdc']) for r in rr if r['margin_diagnostic']['minimum_buffer_usdc'] is not None]
                a=dict(candidate=mode,cost=c,path=p,trades=n,wins=w,net_win_rate=w/n if n else None,
                    net_sum_usdc=str(total),mean_episode_return_pct=str(total/270*100),mean_net_usdc=str(total/n) if n else None,
                    positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),
                    no_trade_episodes=sum(not r['trades'] for r in rr),halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                    max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                    min_ending_balance=str(min(D(r['ending_usdc']) for r in rr)),
                    fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))),
                    maintenance_proxy_breaches=sum(r['margin_diagnostic']['breaches'] for r in rr),
                    minimum_margin_buffer_usdc=str(min(margins)) if margins else None,
                    min_entry_exposure=str(min(D(x['post_entry_mark_exposure']) for x in filled)) if filled else None,
                    max_entry_exposure=str(max(D(x['post_entry_mark_exposure']) for x in filled)) if filled else None,
                    rejection_counts=dict(Counter(reason for x in attempts for reason in x['rejected_by'])),
                    halt_counts=dict(Counter(r['halt_reason'] for r in rr if r['halt_reason'])))
                for k in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):
                    a[k]=str(sum((D(r['metrics'][k]) for r in rr),D(0)))
                a['exit_counts']=dict(sum((Counter(r['metrics']['exit_reasons']) for r in rr),Counter()))
                out.append(a)
    return out


def protocol():
    return dict(version='a44-5x-risk-experiment-v1',modes=list(MODES),cases=432,strategy='VOL / V030_R30',
        parent_run=PARENT_RUN,parent_sha=PARENT_SHA,days=44,episodes=27,signal_minutes=15,execution_minutes=1,
        costs=mean.COSTS,paths=mean.PATHS,configurations={m:asdict(configuration(m,mean.COSTS['base_assumptions'])) for m in MODES},
        size='5x short affordable qty=floor(cash/[mark/5 + max(0,mark-entryfill) + fee*(entryfill+coverfill)] / lot)*lot; costs/rounding make actual exposure slightly below5.',
        changed_budgets='EXPOSURE5_WHATIF only: no fixed3.50 cash reserve; notional cap5; per-trade planned risk6.25%. Explicit paper risk experiment, NOT existing safe policy or strategy improvement.',
        unchanged='Stop0.3%-1%, target1.8R conservative cap,360min,6entries/24h,900s cooldown,3losses halt,5% drawdown/equity halt; no within-episode reset.',
        capital='27 independent virtual10USDC episodes; sum/270 is mean episode return, never sum/10 or compounded44day income.',
        liquidation='Saved maxLeverage metadata determines maintenance; one-minute TRADE price is mark proxy only. A breach blocks valid performance headline; actual liquidation NOT VERIFIED.',
        no_orders=True,no_new_market_downloads=True,no_paid_services=True,no_schedule=True)


def run(root,out):
    out.mkdir(parents=True,exist_ok=False);rows=[];began=time.monotonic();equiv=0
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    write(out/'protocol.json',protocol());write(out/'provenance.json',prov)
    try:
        blocks,checks,saved=load(root);write(out/'data-checks.json',checks)
        cover=parent.parent.coverage(blocks);csvfile(out/'coverage.csv',cover)
        with gzip.open(out/'all-results.jsonl.gz','wt') as f:
            for block in blocks:
                for cost in mean.COSTS:
                    cs,ref=prepare(block,cost)
                    for mode in MODES:
                        for path in mean.PATHS:
                            need(time.monotonic()-began<1260,'Research time limit')
                            r=replay(block,mode,cost,path,cs,ref)
                            if mode=='BASE':baseline_check(r,saved[(block['id'],cost,path)]);equiv+=1
                            rows.append(r);f.write(json.dumps(r,allow_nan=False)+'\n');f.flush()
                print('BLOCK_DONE',block['id'],len(rows),round(time.monotonic()-began,2),flush=True)
        need(equiv==108,'Missing baseline cases')
        agg=aggregate(rows);write(out/'aggregate.json',agg);csvfile(out/'aggregate.csv',agg)
        same=[]
        for row in rows:
            if row['candidate']!='LEVERAGE5_SAME_SIZE':continue
            old=saved[(row['block_id'],row['cost'],row['path'])]
            ts=[{k:v for k,v in t.items() if k!='initial_margin_model'} for t in row['trades']]
            prior=[{k:v for k,v in t.items() if k!='initial_margin_model'} for t in old['trades']]
            same.append(ts==prior and row['metrics']==old['metrics'])
        summary=dict(status='COMPLETED_5X_RISK_MODEL_EXPERIMENT',provenance=prov,cases=len(rows),days=44,episodes=27,
            baseline_equivalence=equiv,same_size_economically_unchanged_cases=sum(same),
            input_minutes=63360,warmup_minutes=16200,execution_window_minutes=47160,
            trade_scenario_records=sum(len(r['trades']) for r in rows),funding_event_records=sum(len(t['funding_events']) for r in rows for t in r['trades']),
            base_results=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC'],
            real_liquidation='NOT_VERIFIED',new_market_requests=0,orders=0)
        write(out/'SUMMARY.json',summary);print('FINAL_SUMMARY',json.dumps(summary),flush=True);return 0
    except Exception as e:
        write(out/'status.json',dict(status='FAILED_NO_COMPLETE_VALIDATED_RESULT',error_type=type(e).__name__,error=str(e),completed_cases=len(rows)));raise
    finally:
        write(out/'evidence-manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();raise SystemExit(run(a.parent,a.out))
