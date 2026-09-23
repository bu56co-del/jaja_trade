"""Four fixed A44 entry-quality comparisons; full accounts, offline research only."""
import argparse,csv,gzip,hashlib,json,os,time
from collections import Counter
from pathlib import Path
from decimal import Decimal as D
import run_refine as parent
import run_mean as mean
import signals_mean as original
import verify_mean as original_ref
import filters_bb as filters
import reference_bb as filter_ref
import signals_entry as strategy
import reference_entry as independent
from inputs44 import need

PARENT_RUN='35602620940'
PARENT_COMMIT='c186c5dff4b9ff4ca0be6903b1b047bafac76268'
BASE_FILTER=next(s for s in filters.specs() if s['id']==strategy.BASE_ID)
EXPECTED_CASES=432


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
def provenance():return {k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}


def load_parent(root):
    blocks,checks,_=parent.load_parent(root/'parent')
    summary=json.loads((root/'results/SUMMARY.json').read_text())
    need(summary['provenance']==dict(GITHUB_SHA=PARENT_COMMIT,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'), 'Wrong parent version')
    need(summary['cases']==1296 and summary['baseline_equivalence']==108,'Incomplete parent result')
    for name in ('run_refine.py','filters_bb.py','reference_bb.py'):
        need((root/'tests/refine44-source'/name).read_bytes()==(Path(__file__).parents[1]/'refine44'/name).read_bytes(), 'Parent source differs '+name)
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:rows=[json.loads(x) for x in f]
    grouped=[]
    for p in root.glob('groups/*/output/results.jsonl.gz'):
        s=json.loads((p.parent/'SUMMARY.json').read_text())
        need(s['provenance']==summary['provenance'] and sha(p)==s['result_sha256'],'Parent group mismatch')
        with gzip.open(p,'rt') as f:grouped.extend(json.loads(x) for x in f)
    key=lambda r:(r['candidate'],r['block_id'],r['cost'],r['path'])
    need(len(rows)==1296 and sorted(rows,key=key)==sorted(grouped,key=key),'Parent merge mismatch')
    base={(r['block_id'],r['cost'],r['path']):r for r in rows if r['candidate']==strategy.BASE_ID}
    need(len(base)==108,'Missing baseline')
    checks['entry_parent_result_sha256']=sha(root/'results/all-results.jsonl.gz')
    return blocks,checks,base


def prepare(block,cost):
    f=original.features(block,15)
    raw=original.choices(block,f,filters.BASE_SPEC)
    refraw=original_ref.reference(block,filters.BASE_SPEC);original_ref.check(raw,refraw)
    ex=filters.extra(block)
    base,trace=filters.choices(block,BASE_FILTER,mean.COSTS[cost],raw,ex)
    ref,reft=filter_ref.reference(block,BASE_FILTER,cost,refraw)
    filter_ref.check(base,ref,trace,reft)
    return base,ref,ex,trace


def protocol():
    return dict(version='entry-quality-v1',specs=strategy.specs(),primary=strategy.PRIMARY,
        baseline='BASE',parent_candidate=strategy.BASE_ID,parent_run=PARENT_RUN,parent_commit=PARENT_COMMIT,
        cases=432,days=44,episodes=27,signal_minutes=15,confirmation_minutes=1,execution_minutes=1,
        costs=mean.COSTS,paths=mean.PATHS,
        volatility='At original completed15m setup, require 1.5*ATR/close>=0.003. Equality passes. No second volatility gate after confirmation.',
        micro='Original cost-qualified signal first. Freeze ATR, high and mean. Subsequent closed1m red candle with close<previous1m low confirms; next1m open executes. Close>signal15m high cancels first. Max5 closed bars, fifth eligible. Consume on first confirmation even if room/account refuses.',
        room='Recheck new actual modeled short entry to frozen SMA20 >=3 modeled roundtrip costs. Latest1m close used for original ATR-based stop risk; original stop floor/ceiling unchanged.',
        overlap='Process active event first. Ignore another setup until it ends. Setups are15m apart and lifetime<=5min; no delayed retry after cooldown or halt.',
        capital='27 separate virtual10USDC accounts, no gap joining or resetting inside episodes. net sum/270 is mean episode return, not one10USDC income record.',
        data='All44 known A dates,63360 1m bars;600min warmup per contiguous episode;47160min execution before account halts.',
        safeguards='Same single-position/quantity/fees/funding/1.8R capped target/360min/cooldown/entry cap/drawdown/consecutive-loss halt. All failures disclosed.',
        new_market_requests=0,real_orders=0,paid_services=0,schedule=False,
        limits='Retrospective model, no L2/mark/oracle/queue/latency reconstruction; micro OHLC not exact fills; no promise of stable future profit.')


def aggregate(rows):
    expected={(s['id'],f'block_{b:02d}',c,p) for s in strategy.specs() for b in range(27) for c in mean.COSTS for p in mean.PATHS}
    need(len(rows)==EXPECTED_CASES and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Incomplete or duplicate cases')
    out=[]
    for s in strategy.specs():
        for c in mean.COSTS:
            for p in mean.PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(s['id'],c,p)]
                trades=[t for r in rr for t in r['trades']]
                nets=[D(t['gross_pnl'])-D(t['entry_fee'])-D(t['exit_fee'])+sum((D(e['amount']) for e in t['funding_events']),D(0)) for t in trades]
                n=len(nets);w=sum(x>0 for x in nets);total=sum(nets,D(0))
                positive=sum((x for x in nets if x>0),D(0));negative=-sum((x for x in nets if x<0),D(0))
                a=dict(candidate=s['id'],cost=c,path=p,trades=n,wins=w,net_win_rate=w/n if n else None,
                    net_sum_usdc=str(total),mean_net_usdc=str(total/n) if n else None,mean_episode_return_pct=str(total/270*100),
                    profit_factor=str(positive/negative) if negative else None,
                    positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),
                    negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),
                    no_trade_episodes=sum(not r['trades'] for r in rr),halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                    max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                    fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))))
                for k in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):
                    a[k]=str(sum((D(r['metrics'][k]) for r in rr),D(0)))
                a['exit_counts']=dict(sum((Counter(r['metrics']['exit_reasons']) for r in rr),Counter()))
                a['entry_event_counts']=dict(sum((Counter(r['entry_event_counts']) for r in rr),Counter()))
                out.append(a)
    return out


def run(root,out):
    out.mkdir(parents=True,exist_ok=False);began=time.monotonic();rows=[]
    write(out/'protocol.json',protocol());prov=provenance();write(out/'provenance.json',prov)
    write(out/'code-hashes.json',{p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    try:
        blocks,checks,old=load_parent(root);write(out/'data-checks.json',checks)
        cover=parent.coverage(blocks);write(out/'coverage.json',cover)
        with (out/'coverage.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(cover[0]));w.writeheader();w.writerows(cover)
        equiv=0
        with gzip.open(out/'all-results.jsonl.gz','wt') as save,gzip.open(out/'events.jsonl.gz','wt') as ev:
            for block in blocks:
                for cost in mean.COSTS:
                    base,refbase,ex,original_trace=prepare(block,cost)
                    for s in strategy.specs():
                        cs,events=strategy.choices(block,s,mean.COSTS[cost],base,ex)
                        ref,reports=independent.reference(block,s,cost,refbase)
                        independent.check(cs,ref,events,reports)
                        ev.write(json.dumps(dict(candidate=s['id'],block_id=block['id'],cost=cost,events=events,original_filter_trace=original_trace))+'\n')
                        for path in mean.PATHS:
                            need(time.monotonic()-began<1260,'Research time budget')
                            r=mean.replay(block,filters.BASE_SPEC,cost,path,cs,ref)
                            if s['id']=='BASE':
                                previous=old[(block['id'],cost,path)]
                                need(all(r[k]==previous[k] for k in r if k!='candidate'),'Original account drift');equiv+=1
                            need(all(t['direction']==-1 for t in r['trades']),'Unexpected long')
                            r.update(candidate=s['id'],entry_spec=s,parent_candidate=strategy.BASE_ID,
                                     entry_event_counts=dict(Counter(e['status'] for e in events)))
                            save.write(json.dumps(r,allow_nan=False)+'\n');save.flush();rows.append(r)
                print('BLOCK_DONE',block['id'],len(rows),round(time.monotonic()-began,2),flush=True)
        need(equiv==108,'Missing baseline regressions')
        agg=aggregate(rows);write(out/'aggregate.json',agg)
        with (out/'aggregate.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(agg[0]));w.writeheader();w.writerows(agg)
        base=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC']
        summary=dict(status='COMPLETED_ENTRY_QUALITY_RESEARCH',provenance=prov,cases=len(rows),candidates=4,
            days=44,episodes=27,input_minutes=63360,warmup_minutes=16200,execution_window_minutes=47160,
            primary=strategy.PRIMARY,baseline_equivalence=equiv,trades=sum(len(r['trades']) for r in rows),
            funding_events=sum(len(t['funding_events']) for r in rows for t in r['trades']),
            base_results=base,best_trading_base=max((a for a in base if a['trades']),key=lambda a:D(a['net_sum_usdc']),default=None),
            new_market_requests=0,real_orders=0)
        write(out/'SUMMARY.json',summary);write(out/'status.json',dict(status=summary['status']))
        print('FINAL_SUMMARY',json.dumps(summary),flush=True);return 0
    except Exception as exc:
        write(out/'status.json',dict(status='FAILED_NO_VALIDATED_COMPLETE_RESULT',error_type=type(exc).__name__,error=str(exc),completed_cases=len(rows)))
        raise
    finally:
        write(out/'evidence-manifest.json',{str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();raise SystemExit(run(a.parent,a.out))
