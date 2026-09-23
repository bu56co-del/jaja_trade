"""Three frozen sizing/exit policies on all A44 and saved September data."""
import argparse, csv, gzip, json, os, time
from collections import Counter
from pathlib import Path
from decimal import Decimal as D
from dataclasses import asdict
import run_compare as prior
import reference_fixed
from risk_fixed import PROFILES, configuration, FixedRiskEngine
from inputs44 import need

mean=prior.old.mean
SPEC=prior.old.previous.filters.BASE_SPEC
THRESHOLD=next(s for s in prior.specs() if s['id']=='V030_R30')
PARENT_SHA='c65d35732046228bf250e5cedbd92577540e97c6'
PARENT_RUN='35683448454'
CASES=336
write=prior.write
csvfile=prior.csvfile
sha=prior.history.sha
net=prior.net


def load(root):
    root=Path(root);s=prior.history.read(root/'results/SUMMARY.json')
    need(s['provenance']==dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'),'Wrong frozen cross-period parent')
    need(s['new_cases']==8 and s['regression_cases']==216,'Incomplete parent')
    for name,digest in prior.history.read(root/'results/evidence-manifest.json').items():
        need(sha(prior.history.child(root/'results',name))==digest,'Parent evidence hash '+name)
    for name in ('run_compare.py','history.py','source-pins.json'):
        need((root/'evidence/crossperiod-source'/name).read_bytes()==(Path(__file__).parents[1]/'crossperiod'/name).read_bytes(),'Parent source drift '+name)
    blocks,checks,_=prior.load_parent(root/'parent')
    sept,september=prior.history.load(root/'september-original',root/'september-native')
    need(not(set(sept['days'])&set(checks['days'])),'Overlapping datasets')
    saved={}
    for group,filename in (('A44','A44_REGRESSION'),('SEPTEMBER','SEPTEMBER_COMPARISON')):
        with gzip.open(root/'results'/(filename+'.jsonl.gz'),'rt') as f:
            rs=[json.loads(x) for x in f]
        for row in rs:
            if row['candidate']=='V030_R30':
                key=(group,row['block_id'],row['cost'],row['path'])
                need(key not in saved,'Duplicate parent control');saved[key]=row
    need(len(saved)==112,'Missing B controls')
    return [('A44',blocks),('SEPTEMBER',[sept])],dict(a44=checks,september=september),saved


def prepare(block,cost):
    raw,rawref,extra=prior.old.prepare(block)
    cs,trace=prior.old.strategy.choices(block,THRESHOLD,mean.COSTS[cost],raw,extra)
    ref,reftrace=prior.old.independent.reference(block,THRESHOLD,cost,rawref)
    prior.old.independent.check(cs,ref,trace,reftrace)
    return cs,ref,trace


def replay(block,profile,cost_name,path,cs,ref):
    need(profile in PROFILES and cost_name in mean.COSTS and path in mean.PATHS,'Unknown scenario')
    if profile=='BASE_B':return mean.replay(block,SPEC,cost_name,path,cs,ref)
    cost=mean.COSTS[cost_name];cfg=configuration(profile,cost)
    start,end=block['start'],block['end'];bars=block['candles'];holder={'spec':SPEC}
    e=FixedRiskEngine(cfg,mean.core.new_state(cfg,start+2000,'FIXED_RISK_OFFLINE_PAPER'),SPEC,mean.MODE,holder)
    holder['engine']=e;e.ingest_funding(block['funding'])
    fund=mean.FundingPrices({'candles':bars,'funding':block['funding']})
    times=[r['time'] for r in block['funding']];pointer=0;halt=None;q=None
    with mean.installed(holder):
        for j,b in enumerate(bars):
            if b['t']<start:continue
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
    row=dict(candidate=profile,strategy_spec=SPEC,block_id=block['id'],days=block['days'],mode=mean.MODE,cost=cost_name,path=path,
        start_ms=start+2000,end_ms=end-2,last_observation_ms=q.observed_ms,initial_usdc='10',ending_usdc=str(e.cash),
        account_config=asdict(cfg),halt_reason=e.state['halt_reason'],halt_ms=halt,
        halted_fraction=(end-2-halt)/(end-2-start-2000) if halt else 0,
        sampled_max_drawdown_pct=str(D(e.state['max_drawdown_fraction'])*100),open_position=e.position is not None,
        pending_funding=e.state['pending_funding'],integrity_warnings=e.state['integrity_warnings'],trades=e.state['trades'],
        metrics=mean.paired.summarize(e.state['trades'],cost_name,mean.MODE),decisions=dict(e.counts),
        potential_signals=sum(c['direction']!=0 for c in cs.values()),sizing_attempts=e.attempts,margin_diagnostic=e.margin_summary(),
        evidence='FIXED_RISK_RETROSPECTIVE_MODEL_ONLY_NOT_LIVE')
    row['independent_audit']=reference_fixed.audit(row,block,ref)
    return row


def aggregate(rows):
    expected={(g,p,b,c,t) for g,bs in (('A44',[f'block_{i:02d}' for i in range(27)]),('SEPTEMBER',['september_union']))
              for p in PROFILES for b in bs for c in mean.COSTS for t in mean.PATHS}
    actual={(r['dataset'],r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}
    need(len(rows)==CASES and actual==expected,'Incomplete/duplicate grid')
    for g,count in (('A44',27),('SEPTEMBER',1)):
        for p in PROFILES:
            for c in mean.COSTS:
                for t in mean.PATHS:
                    rr=[r for r in rows if (r['dataset'],r['candidate'],r['cost'],r['path'])==(g,p,c,t)]
                    need(len(rr)==count,'Incomplete profile block coverage')
                    if g=='A44':need({r['block_id'] for r in rr}=={f'block_{i:02d}' for i in range(27)},'Wrong A44 blocks')
    result=[]
    for g,count in (('A44',27),('SEPTEMBER',1)):
        for p in PROFILES:
            for c in mean.COSTS:
                for path in mean.PATHS:
                    rs=[r for r in rows if (r['dataset'],r['candidate'],r['cost'],r['path'])==(g,p,c,path)]
                    ts=[t for r in rs for t in r['trades']];nets=[net(t) for t in ts]
                    total=sum(nets,D(0));n=len(ts);wins=sum(x>0 for x in nets)
                    plan=sum((D(t['planned_loss']) for t in ts),D(0))
                    volume=sum((D(t['qty'])*(D(t['entry'])+D(t['exit'])) for t in ts),D(0))
                    attempts=[a for r in rs for a in r.get('sizing_attempts',[])];accepted=[a for a in attempts if not a['rejected_by']]
                    losses=-sum((x for x in nets if x<0),D(0));gains=sum((x for x in nets if x>0),D(0))
                    a=dict(dataset=g,candidate=p,cost=c,path=path,episodes=count,trades=n,wins=wins,losses=sum(x<0 for x in nets),
                        zero_trades=sum(x==0 for x in nets),net_win_rate=wins/n if n else None,net_sum_usdc=str(total),
                        mean_net_usdc=str(total/n) if n else None,mean_episode_return_pct=str(total/(10*count)*100),
                        profit_factor=str(gains/losses) if losses else None,net_per_total_planned_risk=str(total/plan) if plan else None,
                        positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rs),negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rs),
                        no_trade_episodes=sum(not r['trades'] for r in rs),halted_episodes=sum(bool(r['halt_reason']) for r in rs),
                        max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rs)),
                        fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rs),D(0))),
                        fixed_extra_1bp_sum_usdc=str(total-volume/D(10000)),fixed_extra_2bp_sum_usdc=str(total-volume/D(5000)),
                        break_even_extra_bps_per_side=str(total/volume*10000) if volume else None,
                        min_entry_exposure=str(min(D(x['post_entry_mark_exposure']) for x in accepted)) if accepted else None,
                        max_entry_exposure=str(max(D(x['post_entry_mark_exposure']) for x in accepted)) if accepted else None,
                        max_planned_risk_fraction=str(max(D(x['loss_fraction']) for x in accepted)) if accepted else None,
                        sizing_attempts=len(attempts),rejection_counts=dict(Counter(v for a in attempts for v in a['rejected_by'])),
                        exit_counts=dict(sum((Counter(r['metrics']['exit_reasons']) for r in rs),Counter())))
                    for k in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):
                        a[k]=str(sum((D(r['metrics'][k]) for r in rs),D(0)))
                    result.append(a)
    return result


def protocol():
    return dict(version='fixed-risk-v1',profiles=PROFILES,primary='RISK125',cases=336,baseline_cases=112,
        parent_run=PARENT_RUN,parent_sha=PARENT_SHA,
        rule='B15 short entry unchanged. RISK125: per-trade planned stop+friction<=1.25%cash; post-entry mark exposure<=2;5x margin;3.50USDC reserve. TP_HALF changes only1.8d target to0.9d.',
        drawdown_budget='min(.0125*cash,max(0,cash-max(.95*known_peak,9.5)-.001*cash)); no future cash/peak/funding used.',
        model='Same four OHLC/OLHC observations at2/21/40/59.998s. No interpolation or better-fill assumptions added.',
        risks='Original stops,6h,cooldown,three-loss and5%DD rules retained. Planned loss is not guaranteed; funding/gap/latency remain limitations.',
        capital='A44=27 independent virtual10USDC blocks. September=one continuous virtual10USDC account. Never concatenate across missing dates.',
        costs=mean.COSTS,paths=mean.PATHS,extra_cost='Fixed trade ledger extra1/2bp per side is sensitivity only, not entry/exit rerun.',
        configs={p:{c:asdict(configuration(p,mean.COSTS[c])) for c in mean.COSTS} for p in PROFILES},
        new_market_requests=0,orders=0,schedule=False,paid_services=0)


def run(root,out):
    root,out=Path(root),Path(out);out.mkdir(parents=True,exist_ok=False)
    rows=[];started=time.monotonic();equiv=0
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    write(out/'protocol.json',protocol());write(out/'provenance.json',prov)
    try:
        groups,checks,saved=load(root);write(out/'data-checks.json',checks)
        coverage=[];daily=[]
        with gzip.open(out/'all-results.jsonl.gz','wt') as f,gzip.open(out/'events.jsonl.gz','wt') as events:
            for group,blocks in groups:
                for b in blocks:
                    blockrows=[]
                    for cost in mean.COSTS:
                        cs,ref,trace=prepare(b,cost)
                        events.write(json.dumps(dict(dataset=group,block_id=b['id'],cost=cost,events=trace),allow_nan=False)+'\n')
                        for profile in PROFILES:
                            for path in mean.PATHS:
                                need(time.monotonic()-started<1260,'Research deadline')
                                row=replay(b,profile,cost,path,cs,ref)
                                if profile=='BASE_B':
                                    prior.compare_rows(row,saved[(group,b['id'],cost,path)]);equiv+=1
                                row.update(candidate=profile,dataset=group)
                                empty={bar['t'] for bar in b['candles'] if bar['n']==0}
                                row['official_zero_volume_fill_events']=sum(t[k]//60000*60000 in empty for t in row['trades'] for k in ('opened_ms','closed_ms'))
                                need(row['official_zero_volume_fill_events']==0,'Unverifiable fill on empty minute')
                                f.write(json.dumps(row,allow_nan=False)+'\n');f.flush();rows.append(row);blockrows.append(row)
                    day=prior.daily(b,blockrows)
                    daily.extend(dict(dataset=group,**d) for d in day)
                    for d in day:
                        if d['candidate']=='BASE_B' and d['cost']=='base_assumptions' and d['path']=='OHLC':
                            coverage.append(dict(dataset=group,block_id=b['id'],**{k:d[k] for k in ('date_UTC','input_minutes','warmup_minutes','execution_window_minutes')}))
                    print('BLOCK_DONE',group,b['id'],len(rows),round(time.monotonic()-started,2),flush=True)
        need(equiv==112 and len(coverage)==48,'Missing baseline or dates')
        need(sum(x['input_minutes'] for x in coverage)==68850,'Input coverage')
        agg=aggregate(rows);write(out/'aggregate.json',agg);csvfile(out/'comparison.csv',agg)
        csvfile(out/'daily.csv',daily);csvfile(out/'coverage.csv',coverage)
        trades=[dict(dataset=r['dataset'],candidate=r['candidate'],cost=r['cost'],path=r['path'],block_id=r['block_id'],
            **{k:t[k] for k in ('id','opened_ms','closed_ms','qty','entry','exit','stop','target','entry_fee','exit_fee','gross_pnl','planned_loss','close_reason')},
            funding_usdc=str(sum((D(e['amount']) for e in t['funding_events']),D(0))),net_usdc=str(net(t))) for r in rows for t in r['trades']]
        csvfile(out/'trades.csv',trades)
        csvfile(out/'episodes.csv',[dict(dataset=r['dataset'],candidate=r['candidate'],cost=r['cost'],path=r['path'],block_id=r['block_id'],
            net_usdc=r['metrics']['net_usdc'],ending_usdc=r['ending_usdc'],trades=len(r['trades']),wins=r['metrics']['wins'],
            drawdown_pct=r['sampled_max_drawdown_pct'],halt_reason=r['halt_reason']) for r in rows])
        summary=dict(status='COMPLETED_FIXED_RISK_COMPARE',provenance=prov,cases=len(rows),baseline_equivalence=equiv,
            new_candidate_cases=224,input_minutes=68850,warmup_minutes=16800,execution_window_minutes=52050,
            trade_scenario_records=len(trades),funding_event_scenario_records=sum(len(t['funding_events']) for r in rows for t in r['trades']),
            margin_observations=sum(r.get('margin_diagnostic',{}).get('observations',0) for r in rows),
            sizing_attempts=sum(len(r.get('sizing_attempts',[])) for r in rows),
            base_results=[a for a in agg if a['cost']=='base_assumptions'],new_market_requests=0,orders=0,
            exact_live_execution='NOT_VERIFIED',unseen_holdout=False)
        write(out/'SUMMARY.json',summary);write(out/'status.json',dict(status=summary['status']))
        print('FINAL_SUMMARY',json.dumps(summary),flush=True);return 0
    except Exception as exc:
        write(out/'status.json',dict(status='FAILED_NO_COMPLETE_RESULT',error_type=type(exc).__name__,error=str(exc),completed_cases=len(rows)))
        raise
    finally:
        write(out/'evidence-manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();raise SystemExit(run(a.parent,a.out))
