"""Bounded exit-model sensitivity; frozen strategies, costs, sizing and sample risk rules."""
import argparse,copy,gzip,json,os,time
from pathlib import Path
from decimal import Decimal as D
from dataclasses import asdict
from collections import Counter
import run_fixed as old
from path_execution import MODELS,OFFSETS,interpolate,first_cross,CrossingMean,CrossingFixed
from inputs44 import need

mean=old.mean
PARENT_SHA='395614a3876c24c29c04f8a685c3ca6727579b32'
PARENT_RUN='35885160110'
CASES=1344
write,csvfile,sha=old.write,old.csvfile,old.sha


def load(root):
    root=Path(root)
    s=old.prior.history.read(root/'results/SUMMARY.json')
    need(s['provenance']==dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'),'Wrong pinned parent')
    need(s['cases']==336 and s['baseline_equivalence']==112,'Incomplete parent')
    for name,digest in old.prior.history.read(root/'results/evidence-manifest.json').items():
        need(sha(old.prior.history.child(root/'results',name))==digest,'Parent result hash '+name)
    for fn in ('risk_fixed.py','run_fixed.py','reference_fixed.py'):
        need((root/'evidence/fixedrisk-source'/fn).read_bytes()==(Path(__file__).parents[1]/'fixedrisk'/fn).read_bytes(),'Parent source drift '+fn)
    groups,checks,_=old.load(root/'parent')
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:saved=[json.loads(x) for x in f]
    need(len(saved)==336,'Parent grid count')
    expected={(g,b['id'],p,c,t) for g,bs in groups for b in bs for p in old.PROFILES for c in mean.COSTS for t in mean.PATHS}
    indexed={(r['dataset'],r['block_id'],r['candidate'],r['cost'],r['path']):r for r in saved}
    need(len(indexed)==336 and set(indexed)==expected,'Parent grid identities')
    checks['execution_parent_sha256']=sha(root/'results/all-results.jsonl.gz')
    return groups,checks,indexed


def check_control(row,saved,dataset):
    r=copy.deepcopy(row)
    if r['candidate']!=saved['candidate']:
        need(r['candidate']==old.SPEC['id'] and saved['candidate']=='BASE_B','Unknown candidate alias')
        r=old.check_baseline(r,{k:v for k,v in saved.items() if k not in ('dataset','official_zero_volume_fill_events')},dataset)
        r['candidate']='BASE_B'
    r.update(dataset=dataset,official_zero_volume_fill_events=0)
    need(r==saved,'Full original execution regression failed')
    return r


def replay(block,profile,cost_name,path,model,cs,ref):
    need(model in MODELS and profile in old.PROFILES and cost_name in mean.COSTS and path in mean.PATHS,'Unknown case')
    if model=='SAMPLED':return old.replay(block,profile,cost_name,path,cs,ref)
    cost=mean.COSTS[cost_name];cfg=old.configuration(profile,cost)
    start,end=block['start'],block['end'];bars=block['candles'];holder={'spec':old.SPEC}
    kind=CrossingMean if profile=='BASE_B' else CrossingFixed
    e=kind(cfg,mean.core.new_state(cfg,start+2000,'EXIT_MODEL_SENSITIVITY_ONLY'),old.SPEC,mean.MODE,holder,execution_model=model)
    holder['engine']=e;e.ingest_funding(block['funding'])
    fund=mean.FundingPrices({'candles':bars,'funding':block['funding']})
    times=[r['time'] for r in block['funding']];pointer=0;halt=None;q=None;marks=[];fills=0
    coverfactor=(1+D(cost['spread_bps'])/20000)*(1+D(cost['adverse_slippage_bps'])/10000)
    def quote(at,p):
        return mean.assumed_quote(at,str(p),cost['spread_bps'],block['metadata'])
    def financing(at):
        nonlocal pointer
        if pointer<len(times) and times[pointer]<=at:
            e.reconcile_funding(fund,at)
            while pointer<len(times) and times[pointer]<=at:pointer+=1
    def margin(q):
        if e.position:
            n=D(e.position['qty'])*q.mark;eq=D(e.valuation(q)['mark_equity'])
            buffer=eq-n/(2*q.asset_max_leverage)
            need(buffer>0,'Maintenance breach: no exact liquidation model')
            marks.append(buffer)
    def pending_fill(at,p):
        nonlocal fills,halt
        qq=quote(at,p);financing(at);margin(qq)
        need(bars[(at-bars[0]['t'])//60000]['n']>0,'No fill on official empty minute')
        e.update_drawdown(qq)
        if e.execute_pending(qq):fills+=1
        e.update_drawdown(qq)
        if e.state['halt_reason'] and halt is None:halt=at
    with mean.installed(holder):
        for j,b in enumerate(bars):
            if b['t']<start:continue
            previous=None
            for field,off in zip('ohlc' if path=='OHLC' else 'olhc',OFFSETS):
                at=b['t']+off;p=D(b[field])
                if previous is not None and e.position is not None:
                    a,pa=previous
                    if e.pending_exit is None:
                        hit=first_cross(a,pa,at,p,e.position,coverfactor)
                        if hit:
                            tt,reason=hit
                            e.arm(tt,reason,interpolate(tt,a,pa,at,p),'WITHIN_MINUTE_INTERPOLATION')
                    if e.pending_exit is not None and e.pending_exit['due_ms']<at:
                        due=e.pending_exit['due_ms']
                        need(due>a,'Pending exit missed prior event')
                        pending_fill(due,interpolate(due,a,pa,at,p))
                q=quote(at,p);financing(at);margin(q)
                history=None
                if field=='o':holder.update(i=j,choice=cs[j]);history=bars[j-120:j]
                before=len(e.state['trades'])
                e.tick(q,history)
                if e.position and e.pending_exit:
                    if e.pending_exit['due_ms']<=at:pending_fill(at,p)
                    elif at-e.position['opened_ms']>=cfg.max_hold_seconds*1000:
                        e.close_position(q,'MAX_HOLD_TIME')
                e.update_drawdown(q)
                if len(e.state['trades'])>before:margin(q)
                if e.state['halt_reason'] and halt is None:halt=at
                previous=(at,p)
            if halt and not e.position:break
        if e.position:
            e.close_position(q,'BACKTEST_SEGMENT_END_ASSUMED_FILL');e.update_drawdown(q)
            if e.state['halt_reason'] and halt is None:halt=q.observed_ms
        e.reconcile_funding(fund,q.observed_ms)
    need(e.pending_exit is None and not e.position and not e.state['pending_funding'] and not e.state['integrity_warnings'],'Unresolved model result')
    row=dict(candidate=profile,execution_model=model,strategy_spec=old.SPEC,block_id=block['id'],days=block['days'],mode=mean.MODE,cost=cost_name,path=path,
        start_ms=start+2000,end_ms=end-2,last_observation_ms=q.observed_ms,initial_usdc='10',ending_usdc=str(e.cash),account_config=asdict(cfg),
        halt_reason=e.state['halt_reason'],halt_ms=halt,halted_fraction=(end-2-halt)/(end-2-start-2000) if halt else 0,
        sampled_max_drawdown_pct=str(D(e.state['max_drawdown_fraction'])*100),open_position=False,pending_funding=[],integrity_warnings=[],
        trades=e.state['trades'],metrics=mean.paired.summarize(e.state['trades'],cost_name,mean.MODE),decisions=dict(e.counts),
        potential_signals=sum(c['direction']!=0 for c in cs.values()),sizing_attempts=getattr(e,'attempts',[]),trigger_log=e.trigger_log,
        supplemental_or_due_fills=fills,margin_checks=len(marks),minimum_margin_buffer=str(min(marks)) if marks else None,
        evidence='INTERPOLATED_EXECUTABLE_PRICE_SENSITIVITY_NOT_MARK_OR_L2')
    return row


def aggregate(rows):
    expected={(g,p,m,b,c,t) for g,bs in (('A44',[f'block_{i:02d}' for i in range(27)]),('SEPTEMBER',['september_union']))
              for p in old.PROFILES for m in MODELS for b in bs for c in mean.COSTS for t in mean.PATHS}
    need(len(rows)==CASES and {(r['dataset'],r['candidate'],r['execution_model'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Incomplete execution grid')
    result=[]
    for m in MODELS:
        subset=[r for r in rows if r['execution_model']==m]
        for a in old.aggregate(subset):
            a['execution_model']=m;result.append(a)
    return result


def protocol():
    return dict(version='exit-model-v1',profiles=old.PROFILES,models=MODELS,primary='CROSS_1000MS',cases=CASES,controls=336,
        parent_run=PARENT_RUN,parent_sha=PARENT_SHA,costs=mean.COSTS,paths=mean.PATHS,
        trigger='Original executable-cover threshold, NOT exchange mark price. First integer millisecond crossing on within-minute linear segments.0/1/5s delays are hypotheses, not measured latency.',
        gaps='No interpolation between close and next open. Due orders in that gap fill at next original open at its price.',
        pending='First SL/TP latch cannot untrigger. Original emergency DD/maintenance/time exit can preempt pending. At coincident original node emergency risk tick runs before scheduled fill.',
        risk='Original DD/peak/time checks on original four points; extra fill events also mark before/after. No extra entry time. Do not increase leverage, change stop/target or waive costs.',
        targets='Retain one-sided favorable TP cap; never force stop fill at trigger price.',
        samples='All44 dates/27 independent10USDC blocks, and savedSeptember/one independent10USDC account. Same600min warmup. Neither unseen data nor real fill validation.',
        no_network=True,no_orders=True,no_schedule=True,no_strategy_tuning=True,
        sources=['https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl'])


def run(root,out):
    from reference_exitmodel import audit
    root,out=Path(root),Path(out);out.mkdir(parents=True,exist_ok=False)
    begun=time.monotonic();rows=[];equiv=0
    provenance={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    write(out/'protocol.json',protocol());write(out/'provenance.json',provenance)
    try:
        groups,checks,saved=load(root);write(out/'data-checks.json',checks)
        with gzip.open(out/'all-results.jsonl.gz','wt') as f:
            for group,blocks in groups:
                for b in blocks:
                    for cost in mean.COSTS:
                        cs,ref,_=old.prepare(b,cost)
                        for p in old.PROFILES:
                            for m in MODELS:
                                for path in mean.PATHS:
                                    need(time.monotonic()-begun<1260,'Research time budget')
                                    r=replay(b,p,cost,path,m,cs,ref)
                                    if m=='SAMPLED':
                                        r=check_control(r,saved[(group,b['id'],p,cost,path)],group);equiv+=1
                                    else:r['model_reference_audit']=audit(r,b,ref)
                                    r.update(dataset=group,execution_model=m)
                                    empty={bar['t'] for bar in b['candles'] if bar['n']==0}
                                    need(not any(t[k]//60000*60000 in empty for t in r['trades'] for k in ('opened_ms','closed_ms')),'Empty minute fill')
                                    f.write(json.dumps(r,allow_nan=False)+'\n');f.flush();rows.append(r)
                    print('BLOCK_DONE',group,b['id'],len(rows),round(time.monotonic()-begun,2),flush=True)
        need(equiv==336,'Missing parent replay')
        agg=aggregate(rows);write(out/'aggregate.json',agg);csvfile(out/'comparison.csv',agg)
        (out/'coverage.csv').write_bytes((root/'results/coverage.csv').read_bytes())
        ts=[dict(dataset=r['dataset'],candidate=r['candidate'],execution_model=r['execution_model'],cost=r['cost'],path=r['path'],block_id=r['block_id'],
             **{k:t[k] for k in ('id','opened_ms','closed_ms','qty','entry','exit','stop','target','entry_fee','exit_fee','gross_pnl','planned_loss','close_reason')},
             net_usdc=str(old.net(t)),trigger_ms=t.get('exit_trigger',{}).get('trigger_ms'),due_ms=t.get('exit_trigger',{}).get('due_ms')) for r in rows for t in r['trades']]
        csvfile(out/'trades.csv',ts)
        csvfile(out/'episodes.csv',[dict(dataset=r['dataset'],candidate=r['candidate'],execution_model=r['execution_model'],cost=r['cost'],path=r['path'],block_id=r['block_id'],
             net_usdc=r['metrics']['net_usdc'],trades=len(r['trades']),wins=r['metrics']['wins'],ending_usdc=r['ending_usdc'],drawdown_pct=r['sampled_max_drawdown_pct'],halt_reason=r['halt_reason']) for r in rows])
        summary=dict(status='COMPLETED_EXIT_MODEL_SENSITIVITY',provenance=provenance,cases=len(rows),baseline_equivalence=equiv,changed_model_cases=1008,
            input_minutes=68850,warmup_minutes=16800,execution_window_minutes=52050,trade_scenario_records=len(ts),
            funding_event_scenario_records=sum(len(t['funding_events']) for r in rows for t in r['trades']),
            triggers=sum(len(r.get('trigger_log',[])) for r in rows),base_results=[a for a in agg if a['cost']=='base_assumptions'],
            exact_exchange_execution='NOT_VERIFIED_NO_HISTORICAL_MARK_L2_OR_LATENCY',new_market_requests=0,orders=0)
        write(out/'SUMMARY.json',summary);write(out/'status.json',{'status':summary['status']})
        print('FINAL_SUMMARY',json.dumps(summary),flush=True);return 0
    except Exception as e:
        write(out/'status.json',dict(status='FAILED_NO_COMPLETE_RESULT',error_type=type(e).__name__,error=str(e),completed_cases=len(rows)));raise
    finally:
        write(out/'evidence-manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();raise SystemExit(run(a.parent,a.out))
