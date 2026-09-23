"""Finite H4-context / M15 decisions / M1 execution paper research."""
import argparse,csv,gzip,hashlib,json,os,time
from pathlib import Path
from decimal import Decimal as D
from dataclasses import asdict,replace
from collections import Counter
from paperlab import engine as core
from paperlab.common import Config
from paperlab.backtest import assumed_quote,FundingPrices,COSTS,PATHS
from inputs44 import need,MINUTE
import run_refine as prior
import filters_bb as filters
import reference_bb as filter_ref
import signals_mean as mean_signals
import verify_mean as mean_ref
from run_mean import MeanEngine,installed
import paired,higher
from verify_h4 import audit,reference_choices,check

MODE='FINE_1M_TP_CAP'
PARENT_RUN='35602620940'
PARENT_SHA='c186c5dff4b9ff4ca0be6903b1b047bafac76268'
GROUPS=3

def write(p,x):higher.dump(p,x)
def sha(p):return higher.digest(p)
def provenance():return {k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}

def load_parent(root):
    blocks,checks,_=prior.load_parent(root/'parent')
    summary=json.loads((root/'results/SUMMARY.json').read_text())
    need(summary['provenance']==dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1') and summary['cases']==1296,'Wrong parent')
    for name in ('run_refine.py','filters_bb.py','reference_bb.py'):
        need((root/'tests/refine44-source'/name).read_bytes()==(Path(__file__).parents[1]/'refine44'/name).read_bytes(),'Prior source changed '+name)
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:rows=[json.loads(x) for x in f]
    grouped=[]
    for p in root.glob('groups/*/output/results.jsonl.gz'):
        sm=json.loads((p.parent/'SUMMARY.json').read_text())
        need(sm['provenance']==summary['provenance'] and sha(p)==sm['result_sha256'],'Parent group integrity')
        with gzip.open(p,'rt') as f:grouped.extend(json.loads(x) for x in f)
    key=lambda r:(r['candidate'],r['block_id'],r['cost'],r['path'])
    need(len(rows)==1296 and sorted(rows,key=key)==sorted(grouped,key=key),'Parent group/combined mismatch')
    original={(r['block_id'],r['cost'],r['path']):r for r in rows if r['candidate']==higher.BASE_FILTER['id']}
    need(len(original)==108,'Missing profitable baseline')
    checks['parent_results_sha256']=sha(root/'results/all-results.jsonl.gz')
    return blocks,checks,original

def protocol():
    return dict(version='H4-M15-M1-v1',specs=higher.specs(),primary=higher.PRIMARY,baseline=higher.BASELINE,groups=3,cases=1296,days=44,blocks=27,
        parent_run=PARENT_RUN,parent_sha=PARENT_SHA,signal_minutes=15,execution_minutes=1,context_minutes=240,context_history=120,costs=COSTS,paths=PATHS,
        entry='Existing short-only BB15 re-entry + ROOM + LOWER_HALF. EMA9/21 on120 prior completed4h bars; UP_VETO or DOWN_ONLY.',
        exit='HARD original protective exits or MID15 latest CLOSED15m close<=SMA20, next1m open, hard exits first.',
        holding_minutes=[360,240],capital='27 separate virtual10USDC experiments; net_sum/270 is mean episode return, not continuous account.',
        data='Exact44A days with600min/block warmup, no gap bridging. Official4h history for context only, not extra trade dates.',
        source='https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint',
        no_orders=True,no_schedule=True,no_paid_service=True)

def replay(block,s,cost_name,path,cs,ref):
    need(s in higher.specs(),'Unknown frozen hypothesis')
    definition=s; s=dict(filters.BASE_SPEC,exit=definition['exit'])
    cost=COSTS[cost_name];start,end=block['start'],block['end'];bars=block['candles']
    cfg=replace(Config(),max_hold_seconds=definition['hold']*60,taker_fee=cost['taker_fee'],adverse_slippage_bps=cost['adverse_slippage_bps']).validate()
    h={'spec':s};e=MeanEngine(cfg,core.new_state(cfg,start+2000,'A44_RECOVERY_DYNAMIC_PAPER'),s,MODE,h);h['engine']=e
    e.ingest_funding(block['funding']);fund=FundingPrices({'candles':bars,'funding':block['funding']})
    times=[r['time'] for r in block['funding']];pointer=0;halt=None;q=None
    with installed(h):
        for j,b in enumerate(bars):
            if b['t']<start:continue
            for field,off in zip('ohlc' if path=='OHLC' else 'olhc',(2000,21000,40000,59998)):
                q=assumed_quote(b['t']+off,b[field],cost['spread_bps'],block['metadata'])
                if pointer<len(times) and times[pointer]<=q.observed_ms:
                    e.reconcile_funding(fund,q.observed_ms)
                    while pointer<len(times) and times[pointer]<=q.observed_ms:pointer+=1
                history=None
                if field=='o':h.update(i=j,choice=cs[j]);history=bars[j-120:j]
                e.tick(q,history);e.update_drawdown(q)
                if e.state['halt_reason'] and halt is None:halt=q.observed_ms
            if halt and not e.position:break
        if e.position:
            e.close_position(q,'BACKTEST_SEGMENT_END_ASSUMED_FILL');e.update_drawdown(q)
            if e.state['halt_reason'] and halt is None:halt=q.observed_ms
        e.reconcile_funding(fund,q.observed_ms)
    row=dict(candidate=s['id'],strategy_spec=s,block_id=block['id'],days=block['days'],mode=MODE,cost=cost_name,path=path,
             start_ms=start+2000,end_ms=end-2,last_observation_ms=q.observed_ms,initial_usdc='10',ending_usdc=str(e.cash),
             account_config=asdict(cfg),halt_reason=e.state['halt_reason'],halt_ms=halt,
             halted_fraction=(end-2-halt)/(end-2-start-2000) if halt else 0,
             sampled_max_drawdown_pct=str(D(e.state['max_drawdown_fraction'])*100),open_position=e.position is not None,
             pending_funding=e.state['pending_funding'],integrity_warnings=e.state['integrity_warnings'],trades=e.state['trades'],
             metrics=paired.summarize(e.state['trades'],cost_name,MODE),decisions=dict(e.counts),
             potential_signals=sum(c['direction']!=0 for c in cs.values()),evidence='RETROSPECTIVE_DISCONNECTED_A44_MODEL_ONLY')
    row['independent_audit']=audit(row,block,ref)
    return row


def prepared(root,context_dir):
    blocks,checks,original=load_parent(root)
    sm=json.loads((context_dir/'summary.json').read_text())
    need(sm['candles_sha256']==sha(context_dir/'candles.json'),'H4 hash')
    raw=[json.loads((context_dir/f'raw-{k}.json').read_text()) for k in (1,2)]
    data=higher.normalize(raw[0],sm['start_ms'],sm['end_ms'])
    need(data==higher.normalize(raw[1],sm['start_ms'],sm['end_ms'])==json.loads((context_dir/'candles.json').read_text()),'H4 raw/normalized mismatch')
    need([sha(context_dir/f'raw-{k}.json') for k in (1,2)]==sm['raw_sha256'],'H4 raw hash')
    overlap=higher.check_overlap(data,blocks)
    need(overlap==json.loads((context_dir/'overlap.json').read_text()) and not overlap['mismatches'],'H4 overlap changed')
    checks['h4']=sm
    return blocks,checks,original,data

def base_choices(block,cost):
    fv=mean_signals.features(block,15);cs=mean_signals.choices(block,fv,filters.BASE_SPEC)
    rf=mean_ref.reference(block,filters.BASE_SPEC);mean_ref.check(cs,rf)
    extras=filters.extra(block)
    a,trace=filters.choices(block,higher.BASE_FILTER,COSTS[cost],cs,extras)
    b,reftrace=filter_ref.reference(block,higher.BASE_FILTER,cost,rf)
    filter_ref.check(a,b,trace,reftrace)
    return a,b,extras

def run(group,root,context_dir,out):
    need(group in range(3),'Group');out.mkdir(parents=True,exist_ok=False);began=time.monotonic()
    write(out/'protocol.json',protocol());prov=provenance();write(out/'provenance.json',prov)
    blocks,checks,original,rows4=prepared(root,context_dir);ctx=higher.Context(rows4)
    write(out/'data-checks.json',checks);write(out/'coverage.json',prior.coverage(blocks))
    count=nt=nf=equiv=0
    with gzip.open(out/'results.jsonl.gz','wt') as f,gzip.open(out/'context-decisions.jsonl.gz','wt') as g:
        for block in blocks:
            for cost in COSTS:
                a,b,extras=base_choices(block,cost)
                for s in higher.specs()[group::3]:
                    cs,events=higher.choices(block,s,a,extras,ctx)
                    ref,refevents=reference_choices(block,s,b,rows4);check(cs,ref,events,refevents)
                    g.write(json.dumps(dict(candidate=s['id'],block_id=block['id'],cost=cost,events=events))+'\n')
                    for path in PATHS:
                        need(time.monotonic()-began<1320,'Deadline')
                        r=replay(block,s,cost,path,cs,ref)
                        if s['id']==higher.BASELINE:
                            old=original[(block['id'],cost,path)]
                            need(all(r[k]==old[k] for k in r if k!='candidate'),'Baseline account drift');equiv+=1
                        need(all(t['direction']==-1 for t in r['trades']),'Long not allowed')
                        r.update(candidate=s['id'],timeframe_spec=s,h4_setup_events=len(events),h4_rejected=sum(not e['allowed'] for e in events))
                        f.write(json.dumps(r,allow_nan=False)+'\n');f.flush();count+=1;nt+=len(r['trades']);nf+=sum(len(t['funding_events']) for t in r['trades'])
            print('BLOCK_DONE',group,block['id'],count,round(time.monotonic()-began,1),flush=True)
    need(count==432,'Case count')
    result=dict(status='COMPLETED',group=group,cases=count,trades=nt,funding_events=nf,baseline_equivalence=equiv,provenance=prov,
                hashes={n:sha(out/n) for n in ('results.jsonl.gz','context-decisions.jsonl.gz','protocol.json','data-checks.json','coverage.json')})
    write(out/'SUMMARY.json',result);print('GROUP_RESULT',json.dumps(result),flush=True)

def combine(root,out):
    out.mkdir(parents=True,exist_ok=False);prov=provenance();summaries=[];rows=[];cover=None
    for p in sorted(root.glob('*/SUMMARY.json')):
        sm=json.loads(p.read_text());need(sm['provenance']==prov and sm['status']=='COMPLETED','Stale/failed group')
        for n,h in sm['hashes'].items():need(sha(p.parent/n)==h,'Group hash')
        with gzip.open(p.parent/'results.jsonl.gz','rt') as f:rows.extend(json.loads(x) for x in f)
        cover=json.loads((p.parent/'coverage.json').read_text());summaries.append(sm)
    need(len(summaries)==3 and sorted(s['group'] for s in summaries)==[0,1,2],'Missing groups')
    for n in ('protocol.json','data-checks.json','coverage.json'):need(len({s['hashes'][n] for s in summaries})==1,'Mixed inputs')
    expected={(s['id'],f'block_{i:02d}',c,p) for s in higher.specs() for i in range(27) for c in COSTS for p in PATHS}
    need(len(rows)==1296 and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Incomplete grid')
    agg=[]
    for s in higher.specs():
        for c in COSTS:
            for p in PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(s['id'],c,p)]
                n=sum(len(r['trades']) for r in rr);w=sum(r['metrics']['wins'] for r in rr);net=sum((D(r['metrics']['net_usdc']) for r in rr),D(0))
                a=dict(candidate=s['id'],trend=s['trend'],exit=s['exit'],hold=s['hold'],cost=c,path=p,trades=n,wins=w,net_win_rate=w/n if n else None,net_sum_usdc=str(net),
                    mean_net_usdc=str(net/n) if n else None,mean_episode_return_pct=str(net/270*100),
                    fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))),
                    positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),
                    no_trade_episodes=sum(not r['trades'] for r in rr),halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                    max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                    exit_counts=dict(sum((Counter(r['metrics']['exit_reasons']) for r in rr),Counter())))
                agg.append(a)
    write(out/'aggregate.json',agg);write(out/'coverage.json',cover)
    for name,data in [('aggregate',agg),('coverage',cover)]:
        with (out/(name+'.csv')).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    with gzip.open(out/'all-results.jsonl.gz','wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    base=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC']
    summary=dict(status='COMPLETED_H4_M15_COMPARISON',provenance=prov,cases=1296,definitions=12,days=44,blocks=27,
        primary=higher.PRIMARY,baseline=higher.BASELINE,baseline_equivalence=sum(s['baseline_equivalence'] for s in summaries),
        trades=sum(s['trades'] for s in summaries),funding_events=sum(s['funding_events'] for s in summaries),
        positive_base=[a['candidate'] for a in base if D(a['net_sum_usdc'])>0],
        primary_base=next(a for a in base if a['candidate']==higher.PRIMARY),
        all_outcomes=base,no_orders=True)
    need(summary['baseline_equivalence']==108,'Baseline incomplete');write(out/'SUMMARY.json',summary);print('FINAL_RESULT',json.dumps(summary),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--group',type=int);p.add_argument('--parent',type=Path);p.add_argument('--context',type=Path);p.add_argument('--prepare',action='store_true');p.add_argument('--combine',type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    if a.prepare:
        blocks,checks,_=load_parent(a.parent);a.out.mkdir(parents=True,exist_ok=False);write(a.out/'protocol.json',protocol());write(a.out/'provenance.json',provenance());higher.prepare(blocks,a.out/'market')
    elif a.combine:combine(a.combine,a.out)
    else:run(a.group,a.parent,a.context,a.out)
