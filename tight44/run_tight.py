"""Bounded five-profile tighter SL/TP comparison; all 44 dates, same risk policy."""
import argparse, gzip, json, os, time
from pathlib import Path
from dataclasses import asdict
from decimal import Decimal as D
import run_high as prior
from risk_high import MODES, LEVELS, ExposureEngine
from exit_config import PROFILES, PRIMARY, configuration
import reference_tight
from inputs44 import need

PARENT_RUN='35798881696'
PARENT_SHA='9d7a8de3993c8c8c7af48d4da83d4604ff867b37'
CASES=1620
mean=prior.mean
parent=prior.parent
sha=prior.sha
write=prior.write
csvfile=prior.csvfile
prepare=prior.prepare


def load(root):
    root=Path(root)
    blocks,checks,_=prior.load(root/'parent')
    summary=json.loads((root/'results/SUMMARY.json').read_text())
    need(summary['provenance']==dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'),'Wrong high-leverage parent')
    need(summary['cases']==324 and summary['baseline_equivalence']==108,'Incomplete high-leverage parent')
    for name,digest in json.loads((root/'results/evidence-manifest.json').read_text()).items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts,'Unsafe parent path')
        need(sha(root/'results'/name)==digest,'Parent result hash '+name)
    for name in ('run_high.py','risk_high.py','reference_high.py'):
        need((root/'evidence/leverage1020-source'/name).read_bytes()==(Path(__file__).parents[1]/'leverage1020'/name).read_bytes(),'Parent source drift '+name)
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:rows=[json.loads(x) for x in f]
    expected={(m,f'block_{i:02d}',c,p) for m in MODES for i in range(27) for c in mean.COSTS for p in mean.PATHS}
    need(len(rows)==324 and {(x['leverage_mode'],x['block_id'],x['cost'],x['path']) for x in rows}==expected,'Missing parent controls')
    saved={(x['leverage_mode'],x['block_id'],x['cost'],x['path']):x for x in rows}
    checks['tight_parent_results_sha256']=sha(root/'results/all-results.jsonl.gz')
    return blocks,checks,saved


def replay(block,mode,profile,cost_name,path,cs,ref):
    need(mode in MODES and cost_name in mean.COSTS and path in mean.PATHS,'Unknown scenario')
    cost=mean.COSTS[cost_name];cfg=configuration(mode,profile,cost);start,end=block['start'],block['end'];bars=block['candles']
    holder={'spec':parent.filters.BASE_SPEC}
    e=ExposureEngine(cfg,mean.core.new_state(cfg,start+2000,'A44_LEVERAGE_OFFLINE_WHATIF'),parent.filters.BASE_SPEC,mean.MODE,holder)
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
    if profile == 'BASE':
        row['independent_audit']=prior.reference_high.audit(row,block,ref)
    else:
        row['exit_profile']=profile
        row['independent_audit']=reference_tight.audit(row,block,ref)
    return row


def aggregate(rows):
    expected={(m,x,f'block_{i:02d}',c,p) for m in MODES for x in PROFILES for i in range(27) for c in mean.COSTS for p in mean.PATHS}
    actual={(r['leverage_mode'],r['exit_profile'],r['block_id'],r['cost'],r['path']) for r in rows}
    need(len(rows)==CASES and actual==expected,'Incomplete or duplicate exit grid')
    result=[]
    for profile in PROFILES:
        subset=[dict(r,candidate=r['leverage_mode']) for r in rows if r['exit_profile']==profile]
        for a in prior.aggregate(subset):
            a.update(leverage=LEVELS[a['candidate']],exit_profile=profile)
            a['candidate']+='__'+profile
            result.append(a)
    return result


def protocol():
    return dict(version='a44-tight-sl-tp-v1',profiles=PROFILES,primary=PRIMARY,modes=MODES,
        cases=CASES,regression_cases=324,new_exit_cases=1296,
        parent_run=PARENT_RUN,parent_sha=PARENT_SHA,strategy='VOL / V030_R30',
        days=44,episodes=27,signal_minutes=15,execution_minutes=1,costs=mean.COSTS,paths=mean.PATHS,
        definitions='d=max(.003,1.5*ATR15/close15); original d>.01 refused. Stop=s*d, target=t*1.8*d; (s,t) fixed in profiles. Factors are price distances, not equity return.',
        unchanged='All B signal rules, same leverage sizing and .0125*L planned-loss budgets, friction, funding proxy, target cap,360min,900s cooldown,6entries/24h,3losses halt and5% peak-DD/floor retained. No position increase from tightening stops.',
        cost_gate='Recompute new target>=3*roundtrip friction before opening; never relax to force trades. No-trades is not profit.',
        configs={m:{x:asdict(configuration(m,x,mean.COSTS['base_assumptions'])) for x in PROFILES} for m in MODES},
        capital='27 independent virtual10USDC accounts; net/270 is mean episode return, not one continuous44-day return.',
        liquidation='Trade OHLC mark proxy only; maintenance breach prevents verified result. Exact exchange liquidation remains NOT VERIFIED.',
        orders=0,new_market_requests=0,paid_services=0,schedule=False)


def run(root,out):
    root,out=Path(root),Path(out);out.mkdir(parents=True,exist_ok=False)
    rows=[];began=time.monotonic();equiv=0
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    write(out/'protocol.json',protocol());write(out/'provenance.json',prov)
    write(out/'code-hashes.json',{p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    try:
        blocks,checks,saved=load(root);write(out/'data-checks.json',checks)
        need(len(blocks)==27 and sum(len(b['candles']) for b in blocks)==63360,'Incomplete A44')
        cover=parent.parent.coverage(blocks);need(len(cover)==44,'Incomplete coverage');csvfile(out/'coverage.csv',cover)
        with gzip.open(out/'all-results.jsonl.gz','wt') as f:
            for block in blocks:
                for cost in mean.COSTS:
                    cs,ref=prepare(block,cost)
                    for mode in MODES:
                        for profile in PROFILES:
                            for path in mean.PATHS:
                                need(time.monotonic()-began<1260,'Research deadline')
                                row=replay(block,mode,profile,cost,path,cs,ref)
                                if profile=='BASE':
                                    prior.baseline_check(row,saved[(mode,block['id'],cost,path)]);equiv+=1
                                row.update(exit_profile=profile,candidate=mode+'__'+profile)
                                f.write(json.dumps(row,allow_nan=False)+'\n');f.flush();rows.append(row)
                print('BLOCK_DONE',block['id'],len(rows),round(time.monotonic()-began,2),flush=True)
        need(equiv==324,'Missing unchanged exits controls')
        agg=aggregate(rows);write(out/'aggregate.json',agg);csvfile(out/'aggregate.csv',agg)
        episodes=[dict(candidate=r['candidate'],leverage=LEVELS[r['leverage_mode']],exit_profile=r['exit_profile'],cost=r['cost'],path=r['path'],block_id=r['block_id'],days=';'.join(r['days']),
            initial_usdc=r['initial_usdc'],ending_usdc=r['ending_usdc'],net_usdc=r['metrics']['net_usdc'],trades=len(r['trades']),wins=r['metrics']['wins'],
            drawdown_pct=r['sampled_max_drawdown_pct'],halt_reason=r['halt_reason'],halt_ms=r['halt_ms'],margin_breaches=r['margin_diagnostic']['breaches']) for r in rows]
        csvfile(out/'episodes.csv',episodes)
        trades=[dict(candidate=r['candidate'],leverage=LEVELS[r['leverage_mode']],exit_profile=r['exit_profile'],cost=r['cost'],path=r['path'],block_id=r['block_id'],
            **{k:t[k] for k in ('id','opened_ms','closed_ms','qty','entry','exit','stop','target','gross_pnl','entry_fee','exit_fee','planned_loss','close_reason')},
            funding_usdc=str(sum((prior.D(e['amount']) for e in t['funding_events']),prior.D(0))),
            net_usdc=str(prior.D(t['gross_pnl'])-prior.D(t['entry_fee'])-prior.D(t['exit_fee'])+sum((prior.D(e['amount']) for e in t['funding_events']),prior.D(0)))) for r in rows for t in r['trades']]
        if trades:csvfile(out/'trades.csv',trades)
        summary=dict(status='COMPLETED_A44_TIGHT_EXITS',provenance=prov,cases=len(rows),new_exit_cases=1296,baseline_equivalence=equiv,
            days=44,episodes=27,input_minutes=63360,warmup_minutes=16200,execution_window_minutes=47160,primary=PRIMARY,
            trade_scenario_records=len(trades),funding_event_scenario_records=sum(len(t['funding_events']) for r in rows for t in r['trades']),
            margin_observations=sum(r['margin_diagnostic']['observations'] for r in rows),
            base_results=[a for a in agg if a['cost']=='base_assumptions'],stress_results=[a for a in agg if a['cost']=='cost_stress'],
            exact_exchange_liquidation='NOT_VERIFIED_NO_HISTORICAL_MARK_L2',new_market_requests=0,orders=0)
        write(out/'SUMMARY.json',summary);write(out/'status.json',{'status':summary['status']})
        print('FINAL_SUMMARY',json.dumps(summary),flush=True);return 0
    except Exception as e:
        write(out/'status.json',dict(status='FAILED_NO_COMPLETE_VALID_RESULT',error_type=type(e).__name__,error=str(e),completed_cases=len(rows)))
        raise
    finally:
        write(out/'evidence-manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();raise SystemExit(run(a.parent,a.out))
