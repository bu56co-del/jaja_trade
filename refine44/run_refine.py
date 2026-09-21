"""Bounded same-A44 improvement experiment with an explicit 44-day coverage ledger."""
import argparse,csv,gzip,hashlib,json,os,time
from pathlib import Path
from decimal import Decimal as D
from collections import Counter
from datetime import datetime,timezone
import run_direction as direction
import run_mean as mean
import signals_mean as parent_signals
import verify_mean as parent_reference
import filters_bb as filters
import reference_bb as independent
from inputs44 import need,MINUTE

PARENT_RUN='35599128691';PARENT_SHA='bfb9f5591b879c8d0531c9316e42f26192b3b789';GROUPS=3
BASELINE='BB15_NONE_NONE_NONE'

def write(p,x):p.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def provenance():return {k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}

def load_parent(root):
    blocks,checks,_=direction.load_parent(root/'previous')
    summary=json.loads((root/'results/SUMMARY.json').read_text())
    need(summary['provenance']==dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1') and summary['cases']==648,'Wrong direction parent')
    for mod,files in {'direction44':['run_direction.py'],'mean44':['run_mean.py','signals_mean.py','verify_mean.py']}.items():
        for name in files:
            need((root/'tests'/(mod+'-source')/name).read_bytes()==(Path(__file__).parents[1]/mod/name).read_bytes(),'Parent source changed '+name)
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:rows=[json.loads(x) for x in f]
    grouped=[]
    for p in root.glob('groups/*/results/results.jsonl.gz'):
        s=json.loads((p.parent/'SUMMARY.json').read_text());need(s['provenance']==summary['provenance'] and sha(p)==s['result_sha256'],'Parent group hash/provenance')
        with gzip.open(p,'rt') as f:grouped.extend(json.loads(x) for x in f)
    key=lambda r:(r['candidate'],r['block_id'],r['cost'],r['path'])
    need(len(rows)==648 and sorted(grouped,key=key)==sorted(rows,key=key),'Parent content mismatch')
    original={(r['block_id'],r['cost'],r['path']):r for r in rows if r['candidate']==filters.BASE_ID}
    need(len(original)==108,'Missing base scenarios');checks['parent_results_sha256']=sha(root/'results/all-results.jsonl.gz')
    return blocks,checks,original

def coverage(blocks):
    rows=[]
    for b in blocks:
        for day in b['days']:
            xs=[x for x in b['candles'] if datetime.fromtimestamp(x['t']/1000,timezone.utc).strftime('%Y-%m-%d')==day]
            warm=sum(x['t']<b['start'] for x in xs)
            rows.append(dict(date_UTC=day,block_id=b['id'],input_minutes=len(xs),warmup_minutes=warm,execution_window_minutes=len(xs)-warm,
                             signal_chart_minutes=15,execution_chart_minutes=1))
    need(len(rows)==44 and len({r['date_UTC'] for r in rows})==44 and all(r['input_minutes']==1440 for r in rows),'Incomplete calendar')
    need(sum(r['warmup_minutes'] for r in rows)==16200 and sum(r['execution_window_minutes'] for r in rows)==47160,'Warmup changed')
    return rows

def protocol():
    return dict(version='bb-short-filter-v1',specs=filters.specs(),primary=filters.PRIMARY,baseline=BASELINE,
        parent_run=PARENT_RUN,parent_commit=PARENT_SHA,groups=GROUPS,cases=1296,days=44,episodes=27,
        cost=mean.COSTS,path=mean.PATHS,signal_minutes=15,execution_minutes=1,
        input_minutes=63360,warmup_minutes=16200,execution_window_minutes=47160,
        rules='3 trend choices x with/without actual-entry-to-frozen-middle room >=3 roundtrip cost x with/without close in lower half. All short only. Original exits/risk unchanged.',
        ema='Same preceding40 closed15m history, first-close seed; EMA9/21. Strong up: fast>slow and fast rise over4 intervals>0.5ATR. Down: fast<=slow and nonrising fast.',
        capital='Each true contiguous episode independently starts virtual10USDC; sums/270 are mean episode returns, not a continuous10USDC equity curve.',
        labels='Same seen A44 research, no calendar exclusions, no guarantee or established55/60 rate.',no_orders=True,no_new_market_download=True)

def run(group,root,out):
    need(group in range(GROUPS),'Group out of range');out.mkdir(parents=True,exist_ok=False);began=time.monotonic()
    plan=protocol();write(out/'protocol.json',plan);prov=provenance();write(out/'provenance.json',prov)
    blocks,checks,old=load_parent(root);write(out/'data-checks.json',checks);cover=coverage(blocks);write(out/'coverage.json',cover)
    selected=filters.specs()[group::GROUPS];count=trades=funds=equiv=0
    with gzip.open(out/'results.jsonl.gz','wt') as save,gzip.open(out/'filter-decisions.jsonl.gz','wt') as tr:
        for block in blocks:
            fv=parent_signals.features(block,15);base=parent_signals.choices(block,fv,filters.BASE_SPEC)
            refbase=parent_reference.reference(block,filters.BASE_SPEC);parent_reference.check(base,refbase)
            extra=filters.extra(block)
            for s in selected:
                for cost in mean.COSTS:
                    cs,trace=filters.choices(block,s,mean.COSTS[cost],base,extra)
                    ref,reftrace=independent.reference(block,s,cost,refbase);independent.check(cs,ref,trace,reftrace)
                    rejects=Counter(reason for t in trace.values() for reason in t['rejected_by'])
                    tr.write(json.dumps(dict(candidate=s['id'],block_id=block['id'],cost=cost,events=trace))+'\n')
                    for path in mean.PATHS:
                        need(time.monotonic()-began<1350,'Run deadline')
                        r=mean.replay(block,filters.BASE_SPEC,cost,path,cs,ref)
                        if s['id']==BASELINE:
                            parent=old[(block['id'],cost,path)]
                            # Every field produced by the core must be identical except its label.
                            need(all(r[k]==parent[k] for k in r if k!='candidate'),'Baseline account drift');equiv+=1
                        need(all(t['direction']==-1 for t in r['trades']),'Unexpected long')
                        r.update(candidate=s['id'],filter_spec=s,parent_candidate=filters.BASE_ID,
                                 filter_rejections=dict(rejects),raw_short_setups=len(trace))
                        save.write(json.dumps(r,allow_nan=False)+'\n');save.flush();count+=1;trades+=len(r['trades']);funds+=sum(len(t['funding_events']) for t in r['trades'])
            print('BLOCK_DONE',group,block['id'],count,round(time.monotonic()-began,1),flush=True)
    need(count==432,'Group incomplete')
    summary=dict(status='COMPLETED',group=group,cases=count,trades=trades,funding_events=funds,baseline_equivalence=equiv,provenance=prov,
        protocol_sha256=sha(out/'protocol.json'),data_sha256=sha(out/'data-checks.json'),coverage_sha256=sha(out/'coverage.json'),
        result_sha256=sha(out/'results.jsonl.gz'),trace_sha256=sha(out/'filter-decisions.jsonl.gz'))
    write(out/'SUMMARY.json',summary);print('GROUP_RESULT',json.dumps(summary),flush=True)

def combine(root,out):
    out.mkdir(parents=True,exist_ok=False);prov=provenance();ss=[json.loads(p.read_text()) for p in root.glob('*/SUMMARY.json')]
    need(len(ss)==GROUPS and sorted(s['group'] for s in ss)==list(range(GROUPS)),'Missing groups')
    need(all(s['provenance']==prov and s['status']=='COMPLETED' for s in ss),'Stale/mixed execution')
    for k in ('protocol_sha256','data_sha256','coverage_sha256'):need(len({s[k] for s in ss})==1,'Input/protocol difference')
    rows=[];cover=None
    for p in root.glob('*/results.jsonl.gz'):
        s=json.loads((p.parent/'SUMMARY.json').read_text())
        for name,key in [('results.jsonl.gz','result_sha256'),('filter-decisions.jsonl.gz','trace_sha256'),('protocol.json','protocol_sha256'),('data-checks.json','data_sha256'),('coverage.json','coverage_sha256')]:
            need(sha(p.parent/name)==s[key],'Evidence digest '+name)
        with gzip.open(p,'rt') as f:rows.extend(json.loads(x) for x in f)
        cover=json.loads((p.parent/'coverage.json').read_text())
    expected={(s['id'],f'block_{b:02d}',c,p) for s in filters.specs() for b in range(27) for c in mean.COSTS for p in mean.PATHS}
    need(len(rows)==1296 and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Case coverage')
    agg=[];daily=[]
    for s in filters.specs():
        for c in mean.COSTS:
            for p in mean.PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(s['id'],c,p)];nets=[D(r['metrics']['net_usdc']) for r in rr]
                n=sum(len(r['trades']) for r in rr);w=sum(r['metrics']['wins'] for r in rr);total=sum(nets,D(0))
                a=dict(candidate=s['id'],trend=s['trend'],room=s['room'],body=s['body'],cost=c,path=p,trades=n,wins=w,net_win_rate=w/n if n else None,
                    net_sum_usdc=str(total),mean_net_usdc=str(total/n) if n else None,mean_episode_return_pct=str(total/270*100),
                    positive_episodes=sum(x>0 for x in nets),negative_episodes=sum(x<0 for x in nets),no_trade_episodes=sum(not r['trades'] for r in rr),
                    halted_episodes=sum(bool(r['halt_reason']) for r in rr),max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                    fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))))
                for k in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):a[k]=str(sum((D(r['metrics'][k]) for r in rr),D(0)))
                agg.append(a)
                for d in cover:
                    r=next(x for x in rr if x['block_id']==d['block_id']);date=d['date_UTC']
                    dt=lambda t:datetime.fromtimestamp(t/1000,timezone.utc).strftime('%Y-%m-%d')
                    opened=[t for t in r['trades'] if dt(t['opened_ms'])==date];closed=[t for t in r['trades'] if dt(t['closed_ms'])==date]
                    net=sum((D(t['gross_pnl'])-D(t['entry_fee'])-D(t['exit_fee'])+sum((D(e['amount']) for e in t['funding_events']),D(0)) for t in closed),D(0))
                    start=int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()*1000);end=start+86400000
                    inactive=max(0,(end-max(start,r['halt_ms']))/60000) if r['halt_ms'] is not None else 0
                    daily.append(dict(candidate=s['id'],cost=c,path=p,**d,entries=len(opened),closed_trades=len(closed),closed_trade_net_usdc=str(net),
                                      inactive_after_halt_minutes=inactive,note='Closed-trade attribution, not daily mark-to-market return.'))
    for name,data in [('aggregate',agg),('daily',daily),('coverage',cover)]:
        write(out/(name+'.json'),data)
        with (out/(name+'.csv')).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    with gzip.open(out/'all-results.jsonl.gz','wt') as f:
        for r in rows:f.write(json.dumps(r,allow_nan=False)+'\n')
    base=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC']
    summary=dict(status='COMPLETED_A44_BB_SHORT_FILTER_RESEARCH',provenance=prov,cases=1296,candidates=12,days=44,episodes=27,
        input_minutes=63360,warmup_minutes=16200,execution_window_minutes=47160,signal_minutes=15,execution_minutes=1,
        primary=filters.PRIMARY,baseline=BASELINE,baseline_equivalence=sum(s['baseline_equivalence'] for s in ss),
        trades=sum(s['trades'] for s in ss),funding_events=sum(s['funding_events'] for s in ss),
        positive_base=[a['candidate'] for a in base if D(a['net_sum_usdc'])>0],
        robust_positive=[s['id'] for s in filters.specs() if all(D(a['net_sum_usdc'])>0 and D(a['fixed_trade_stress_sum_usdc'])>0 for a in agg if a['candidate']==s['id'])],
        best_trading_base=max((a for a in base if a['trades']),key=lambda a:D(a['net_sum_usdc'])),new_orders=0)
    need(summary['baseline_equivalence']==108,'Base comparisons incomplete');write(out/'SUMMARY.json',summary)
    print('FINAL_SUMMARY',json.dumps(summary),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--group',type=int);p.add_argument('--parent',type=Path);p.add_argument('--combine',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();combine(a.combine,a.out) if a.combine else run(a.group,a.parent,a.out)
