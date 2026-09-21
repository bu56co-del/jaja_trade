"""Bounded one-sided ablation. Replays accounts; never filters a saved trade ledger."""
import argparse,csv,gzip,hashlib,json,os,time
from pathlib import Path
from decimal import Decimal as D
from collections import Counter
import run_mean as mean
import signals_mean as signals
import verify_mean as independent
from inputs44 import need

PARENT_RUN='35598241627'
PARENT_COMMIT='45a0c92a4adfe6f8d1c554b01164b4b995b437c8'
BASES=('BB_REENTRY_15_NONE_HARD','RSI_RECOVERY_15_NONE_HARD')
PRIMARY='RSI_RECOVERY_15_NONE_HARD_SHORT_ONLY'
SIDES=('BOTH','LONG_ONLY','SHORT_ONLY')
GROUPS=2


def specs():return [dict(id=b+'_'+s,base_id=b,side=s) for b in BASES for s in SIDES]

def base_spec(s):
    need(s in specs(),'Unknown direction definition')
    return next(x for x in signals.specs() if x['id']==s['base_id'])

def gated(cs,side):
    need(side in SIDES,'Unknown side')
    allowed={1,-1} if side=='BOTH' else {1} if side=='LONG_ONLY' else {-1}
    return {j:dict(c,direction=c['direction'] if c['direction'] in allowed else 0) for j,c in cs.items()}

def reference_gate(ref,side):
    # Separate, explicit filter implementation on independently computed events.
    answer={}
    for j,r in ref.items():
        d=r['direction']
        if side=='LONG_ONLY' and d<0:d=0
        if side=='SHORT_ONLY' and d>0:d=0
        need(side in SIDES,'Reference side')
        answer[j]={**r,'direction':d}
    return answer

def replay(block,s,cost,path,cs,ref,original=None):
    b=base_spec(s);events=gated(cs,s['side']);expected=reference_gate(ref,s['side']);independent.check(events,expected)
    row=mean.replay(block,b,cost,path,events,expected)
    if s['side']=='BOTH':
        need(original is not None and row==original,'BOTH account differs from saved parent')
    need(all(s['side']=='BOTH' or t['direction']==(1 if s['side']=='LONG_ONLY' else -1) for t in row['trades']),'Side constraint violated')
    row.update(candidate=s['id'],direction_spec=s,parent_candidate=s['base_id'])
    return row

def write(p,x):p.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n')

def load_parent(parent):
    blocks,checks,_=mean.load_previous(parent/'previous')
    summary=json.loads((parent/'results/SUMMARY.json').read_text())
    need(summary['provenance']==dict(GITHUB_SHA=PARENT_COMMIT,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'),'Wrong parent execution')
    need(summary['cases']==2700 and summary['baseline_cases']==108,'Parent incomplete')
    need((parent/'tests/mean44-source/SEMANTICS.md').is_file(),'Strict re-entry fix absent')
    for n in ('signals_mean.py','verify_mean.py','run_mean.py'):
        need((parent/'tests/mean44-source'/n).read_bytes()==(Path(__file__).parents[1]/'mean44'/n).read_bytes(),'Parent core code changed '+n)
    with gzip.open(parent/'results/all-results.jsonl.gz','rt') as f:rows=[json.loads(x) for x in f]
    need(len(rows)==2700,'Parent row count')
    selected={(r['candidate'],r['block_id'],r['cost'],r['path']):r for r in rows if r['candidate'] in BASES}
    need(len(selected)==216,'Parent BOTH cases missing')
    # Verify each group hash and exact case content before using the parent.
    group_rows=[]
    for p in parent.glob('groups/*/results/results.jsonl.gz'):
        sm=json.loads((p.parent/'SUMMARY.json').read_text());need(sm['provenance']==summary['provenance'],'Mixed parent group')
        need(hashlib.sha256(p.read_bytes()).hexdigest()==sm['result_sha256'],'Parent group digest')
        with gzip.open(p,'rt') as f:group_rows.extend(json.loads(x) for x in f)
    key=lambda r:(r['candidate'],r['block_id'],r['cost'],r['path'])
    need(sorted(group_rows,key=key)==sorted(rows,key=key),'Parent group/combined mismatch')
    checks['parent_results_sha256']=hashlib.sha256((parent/'results/all-results.jsonl.gz').read_bytes()).hexdigest()
    return blocks,checks,selected

def protocol():
    return dict(version='side-ablation-v1',specs=specs(),primary=PRIMARY,parent_run=PARENT_RUN,parent_commit=PARENT_COMMIT,
        cases=648,episodes=27,days=44,groups=2,costs=mean.COSTS,paths=mean.PATHS,
        rule='Filter causal entry direction only. Replay complete accounts from each episode start. Do not remove trades from an existing ledger.',
        risk='Every original risk/cost/position/target/stop/halt/holding-time rule remains unchanged.',
        capital='Separate virtual10USDC per episode; net sum/270 is mean episode return, not one10USDC compounded track record.',
        status='Retrospective development hypothesis from observed long/short asymmetry. Not future-proof or income guarantee.',
        no_new_network=True,no_orders=True)

def run(group,parent,out):
    need(group in (0,1),'Group');out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    plan=protocol();write(out/'protocol.json',plan)
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')};write(out/'provenance.json',prov)
    blocks,checks,original=load_parent(parent);write(out/'data-checks.json',checks)
    selected=specs()[group::GROUPS];count=nt=nf=both=0
    with gzip.open(out/'results.jsonl.gz','wt') as f:
        for block in blocks:
            fv=signals.features(block,15);prepared={}
            for s in selected:
                b=base_spec(s)
                if s['base_id'] not in prepared:
                    cs=signals.choices(block,fv,b);ref=independent.reference(block,b);independent.check(cs,ref);prepared[s['base_id']]=(cs,ref)
                cs,ref=prepared[s['base_id']]
                for cost in mean.COSTS:
                    for path in mean.PATHS:
                        need(time.monotonic()-started<1300,'Deadline')
                        r=replay(block,s,cost,path,cs,ref,original.get((s['base_id'],block['id'],cost,path)))
                        f.write(json.dumps(r,allow_nan=False)+'\n');f.flush();count+=1;nt+=len(r['trades'])
                        nf+=sum(len(t['funding_events']) for t in r['trades']);both+=s['side']=='BOTH'
            print('BLOCK_DONE',group,block['id'],count,round(time.monotonic()-started,1),flush=True)
    need(count==324,'Incomplete group')
    result=dict(status='COMPLETED',group=group,cases=count,trades=nt,funding_events=nf,both_equivalence=both,provenance=prov,
        protocol_sha256=hashlib.sha256((out/'protocol.json').read_bytes()).hexdigest(),
        data_checks_sha256=hashlib.sha256((out/'data-checks.json').read_bytes()).hexdigest(),
        result_sha256=hashlib.sha256((out/'results.jsonl.gz').read_bytes()).hexdigest())
    write(out/'SUMMARY.json',result);print('GROUP_SUMMARY',json.dumps(result),flush=True)

def combine(root,out):
    out.mkdir(parents=True,exist_ok=False);prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    summaries=[json.loads(p.read_text()) for p in root.glob('*/SUMMARY.json')]
    need(len(summaries)==2 and sorted(s['group'] for s in summaries)==[0,1],'Incomplete groups')
    need(all(s['status']=='COMPLETED' and s['provenance']==prov for s in summaries),'Stale groups')
    for k in ('protocol_sha256','data_checks_sha256'):need(len({s[k] for s in summaries})==1,'Mixed inputs')
    rows=[]
    for p in root.glob('*/results.jsonl.gz'):
        sm=json.loads((p.parent/'SUMMARY.json').read_text());need(hashlib.sha256(p.read_bytes()).hexdigest()==sm['result_sha256'],'Result hash')
        with gzip.open(p,'rt') as f:rows.extend(json.loads(x) for x in f)
    expected={(s['id'],f'block_{i:02d}',c,p) for s in specs() for i in range(27) for c in mean.COSTS for p in mean.PATHS}
    need(len(rows)==648 and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Case coverage')
    agg=[]
    for s in specs():
        for c in mean.COSTS:
            for p in mean.PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(s['id'],c,p)]
                n=sum(r['metrics']['trades'] for r in rr);w=sum(r['metrics']['wins'] for r in rr)
                total=sum((D(r['metrics']['net_usdc']) for r in rr),D(0))
                a=dict(candidate=s['id'],base_id=s['base_id'],side=s['side'],cost=c,path=p,trades=n,wins=w,net_win_rate=w/n if n else None,
                    net_sum_usdc=str(total),mean_net_usdc=str(total/n) if n else None,mean_episode_return_pct=str(total/270*100),
                    positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),
                    no_trade_episodes=sum(not r['trades'] for r in rr),halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                    max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                    fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))))
                for k in ('price_only_usdc','spread_slippage_usdc','fees_usdc','funding_usdc','target_cap_haircut_usdc'):
                    a[k]=str(sum((D(r['metrics'][k]) for r in rr),D(0)))
                a['exit_counts']=dict(sum((Counter(r['metrics']['exit_reasons']) for r in rr),Counter()));agg.append(a)
    write(out/'aggregate.json',agg)
    with (out/'aggregate.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(agg[0]));w.writeheader();w.writerows(agg)
    with gzip.open(out/'all-results.jsonl.gz','wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    base=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC']
    summary=dict(status='COMPLETED_DIRECTION_ABLATION',cases=648,candidates=6,new_one_sided_candidates=4,episodes=27,days=44,primary=PRIMARY,provenance=prov,
        trades=sum(s['trades'] for s in summaries),funding_events=sum(s['funding_events'] for s in summaries),both_equivalence=sum(s['both_equivalence'] for s in summaries),
        base_positive=[a['candidate'] for a in base if D(a['net_sum_usdc'])>0],
        robust_positive=[s['id'] for s in specs() if all(D(a['net_sum_usdc'])>0 and D(a['fixed_trade_stress_sum_usdc'])>0 for a in agg if a['candidate']==s['id'])],
        best_trading_base=max((a for a in base if a['trades']),key=lambda a:D(a['net_sum_usdc'])),no_orders=True)
    need(summary['both_equivalence']==216,'Missing BOTH regressions');write(out/'SUMMARY.json',summary);print('FINAL_SUMMARY',json.dumps(summary),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--group',type=int);p.add_argument('--previous',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--combine',type=Path)
    a=p.parse_args();combine(a.combine,a.out) if a.combine else run(a.group,a.previous,a.out)
