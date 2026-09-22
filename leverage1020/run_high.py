"""Fixed B strategy: two new exposure levels plus an exact 5x regression."""
import argparse,csv,gzip,hashlib,json,os,time
from dataclasses import asdict
from decimal import Decimal as D
from pathlib import Path
from collections import Counter
import run_leverage as prior
from risk_high import MODES,LEVELS,configuration,ExposureEngine
import reference_high
from inputs44 import need

PARENT_RUN='35744998756'
PARENT_SHA='3b7569b56f5aaf496e41c96004ec9a2064aa4718'
CASES=324
mean=prior.mean
parent=prior.parent
sha=prior.sha
write=prior.write
csvfile=prior.csvfile
prepare=prior.prepare


def load(root):
    root=Path(root)
    blocks,checks,_=prior.load(root/'parent')
    s=json.loads((root/'results/SUMMARY.json').read_text())
    need(s['provenance']==dict(GITHUB_SHA=PARENT_SHA,GITHUB_RUN_ID=PARENT_RUN,GITHUB_RUN_ATTEMPT='1'),'Wrong 5x parent identity')
    need(s['cases']==432 and s['baseline_equivalence']==108,'Incomplete 5x parent')
    for name,digest in json.loads((root/'results/evidence-manifest.json').read_text()).items():
        need(not Path(name).is_absolute() and '..' not in Path(name).parts,'Unsafe parent manifest path')
        need(sha(root/'results'/name)==digest,'Parent result drift '+name)
    for name in ('run_leverage.py','risk5.py','reference5.py'):
        need((root/'evidence/leverage44-source'/name).read_bytes()==(Path(__file__).parents[1]/'leverage44'/name).read_bytes(),'5x source drift '+name)
    with gzip.open(root/'results/all-results.jsonl.gz','rt') as f:rows=[json.loads(x) for x in f]
    need(len(rows)==432,'Wrong parent case count')
    old={(x['block_id'],x['cost'],x['path']):x for x in rows if x['candidate']=='EXPOSURE5_WHATIF'}
    need(len(old)==108,'Missing 5x controls')
    return blocks,checks,old


def baseline_check(row,saved):
    # All ledger, sizing, margin, state and configuration fields must reproduce;
    # only experiment provenance text is allowed to differ.
    ignored={'evidence'}
    need(set(row)==set(saved),'Parent row schema changed')
    for k in row:
        if k not in ignored:
            need(row[k]==saved[k],'5x baseline drift '+k)


def replay(block,mode,cost_name,path,cs,ref):
    need(mode in MODES and cost_name in mean.COSTS and path in mean.PATHS,'Unknown scenario')
    cost=mean.COSTS[cost_name];cfg=configuration(mode,cost);start,end=block['start'],block['end'];bars=block['candles']
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
    row['independent_audit']=reference_high.audit(row,block,ref)
    return row



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
    return dict(version='a44-10-20x-v1',modes=list(MODES),cases=CASES,new_cases=216,regression_cases=108,
        parent_run=PARENT_RUN,parent_sha=PARENT_SHA,strategy='VOL / V030_R30',days=44,episodes=27,
        signal_minutes=15,execution_minutes=1,costs=mean.COSTS,paths=mean.PATHS,
        configurations={m:asdict(configuration(m,mean.COSTS['base_assumptions'])) for m in MODES},
        size='q=floor_to_lot(cash/[mark/L+max(0,mark-entryfill)+fee*(entryfill+coverfill)]); L in5,10,20.',
        changed_budgets='Isolated paper experiments only: exposure cap=L; fee/mark-loss reserve instead of fixed3.50; planned-risk budget=.0125*L (6.25%,12.5%,25%). Never modify original Config.',
        unchanged='All B entry rules,15m signal,1m execution,0.3%-1% ATR stop,1.8R capped TP,360min,900s cooldown,6entries/24h,3losses halt,5% account peak-DD/floor stop.',
        capital='27 independent10USDC episodes, no within-episode reset. Net/270 is mean episode return, not continuous44day return.',
        liquidation='Single ETH cross-collateral paper account. Trade OHLC mark proxy, saved maxLeverage25 implies2% maintenance. Breach blocks valid performance output. Exact liquidation NOT VERIFIED.',
        sources=['https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining','https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations','https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margin-tiers'],
        no_orders=True,no_new_market_downloads=True,no_paid_services=True,no_schedule=True)


def run(root,out):
    root,out=Path(root),Path(out);out.mkdir(parents=True,exist_ok=False)
    rows=[];began=time.monotonic();equiv=0
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    write(out/'protocol.json',protocol());write(out/'provenance.json',prov)
    write(out/'code-hashes.json',{p.name:sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    try:
        blocks,checks,saved=load(root);write(out/'data-checks.json',checks)
        need(len(blocks)==27 and sum(len(b['candles']) for b in blocks)==63360,'Incomplete A44 data')
        cover=parent.parent.coverage(blocks);need(len(cover)==44,'Incomplete day coverage');csvfile(out/'coverage.csv',cover)
        with gzip.open(out/'all-results.jsonl.gz','wt') as f:
            for block in blocks:
                for cost in mean.COSTS:
                    cs,ref=prepare(block,cost)
                    for mode in MODES:
                        for path in mean.PATHS:
                            need(time.monotonic()-began<1260,'Research deadline')
                            row=replay(block,mode,cost,path,cs,ref)
                            if mode=='EXPOSURE5_WHATIF':
                                baseline_check(row,saved[(block['id'],cost,path)]);equiv+=1
                            f.write(json.dumps(row,allow_nan=False)+'\n');f.flush();rows.append(row)
                print('BLOCK_DONE',block['id'],len(rows),flush=True)
        need(equiv==108,'Incomplete 5x equivalence')
        agg=aggregate(rows);write(out/'aggregate.json',agg);csvfile(out/'aggregate.csv',agg)
        episodes=[dict(candidate=r['candidate'],cost=r['cost'],path=r['path'],block_id=r['block_id'],days=';'.join(r['days']),
            initial_usdc=r['initial_usdc'],ending_usdc=r['ending_usdc'],net_usdc=r['metrics']['net_usdc'],trades=len(r['trades']),
            wins=r['metrics']['wins'],drawdown_pct=r['sampled_max_drawdown_pct'],halt_reason=r['halt_reason'],halt_ms=r['halt_ms'],
            margin_breaches=r['margin_diagnostic']['breaches'],minimum_margin_buffer_usdc=r['margin_diagnostic']['minimum_buffer_usdc']) for r in rows]
        csvfile(out/'episodes.csv',episodes)
        trades=[dict(candidate=r['candidate'],cost=r['cost'],path=r['path'],block_id=r['block_id'],
            id=t['id'],opened_ms=t['opened_ms'],closed_ms=t['closed_ms'],qty=t['qty'],entry=t['entry'],exit=t['exit'],
            gross_pnl=t['gross_pnl'],entry_fee=t['entry_fee'],exit_fee=t['exit_fee'],
            funding_usdc=str(sum((D(e['amount']) for e in t['funding_events']),D(0))),
            net_usdc=str(D(t['gross_pnl'])-D(t['entry_fee'])-D(t['exit_fee'])+sum((D(e['amount']) for e in t['funding_events']),D(0))),
            close_reason=t['close_reason']) for r in rows for t in r['trades']]
        if trades:csvfile(out/'trades.csv',trades)
        summary=dict(status='COMPLETED_A44_LEVERAGE_COMPARISON',provenance=prov,cases=len(rows),new_cases=216,
            regression_cases=equiv,baseline_equivalence=equiv,days=44,episodes=27,input_minutes=63360,
            warmup_minutes=16200,execution_window_minutes=47160,
            trade_scenario_records=len(trades),funding_event_scenario_records=sum(len(t['funding_events']) for r in rows for t in r['trades']),
            margin_observations=sum(r['margin_diagnostic']['observations'] for r in rows),
            base_results=[a for a in agg if a['cost']=='base_assumptions'],
            stress_results=[a for a in agg if a['cost']=='cost_stress'],
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
