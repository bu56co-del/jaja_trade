"""24 fixed economic/entry/stop ablations on the SAME audited A44 episodes."""
import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, replace
from decimal import Decimal as D
import gzip
import hashlib
import json
import os
from pathlib import Path
import csv
import time

from paperlab import engine as core
from paperlab.common import Config
from paperlab.backtest import assumed_quote, FundingPrices, COSTS, PATHS
import paired
from inputs44 import load, need, MINUTE, FIVE
from signals import features
from signals_profit import decisions, specs, PRIMARY, BASELINE, OLD_ID
from risk_engine import ProfitEngine
from verify_profit import features_ref, reference_decisions, check_features, check_decisions, audit, eq

PRIOR_RESULTS_SHA='afb8fc59e5f878eb860dfa05a3e1197148ffd757cb04e12dbe5d47fbabb9085c'
GROUPS=4
MODE='FINE_1M_TP_CAP'


@contextmanager
def installed(holder):
    original=core.strategy_signal
    def signal(bars,cfg):
        f=holder['f'];eng=holder['engine']
        need(bars[-1]['T']==f['bar_ms'],'Signal timestamp mismatch')
        return core.Signal(0 if eng.position else holder['choice']['direction'],f['bar_ms'],f['atr'],f['close'],holder['spec']['id'])
    try:
        core.strategy_signal=signal;yield
    finally:core.strategy_signal=original


def replay(block,spec,cost_name,path,fv,dv,ref):
    need(spec in specs() and cost_name in COSTS and path in PATHS,'Unknown scenario')
    cost=COSTS[cost_name];start=block['start'];end=block['end'];bars=block['candles'];signals=block['signals']
    cfg=replace(Config(),max_hold_seconds=360*60,reward_to_risk=spec['reward'],taker_fee=cost['taker_fee'],adverse_slippage_bps=cost['adverse_slippage_bps']).validate()
    holder={'spec':spec};eng=ProfitEngine(cfg,core.new_state(cfg,start+2000,'A44_ECONOMIC_ABLATION_PAPER'),spec,MODE,holder);holder['engine']=eng
    eng.ingest_funding(block['funding']);prices=FundingPrices({'candles':bars,'funding':block['funding']})
    times=[r['time'] for r in block['funding']];pointer=0;halt=None;quote=None
    with installed(holder):
        for b in bars:
            if b['t']<start:continue
            for field,off in zip('ohlc' if path=='OHLC' else 'olhc',(2000,21000,40000,59998)):
                quote=assumed_quote(b['t']+off,b[field],cost['spread_bps'],block['metadata'])
                if pointer<len(times) and times[pointer]<=quote.observed_ms:
                    eng.reconcile_funding(prices,quote.observed_ms)
                    while pointer<len(times) and times[pointer]<=quote.observed_ms:pointer+=1
                history=None
                if field=='o' and b['t']%FIVE==0:
                    i=(b['t']-bars[0]['t'])//FIVE
                    holder.update(i=i,f=fv[i],choice=dv[i]);history=signals[i-120:i]
                eng.tick(quote,history);eng.update_drawdown(quote)
                if eng.state['halt_reason'] and halt is None:halt=quote.observed_ms
            if halt and not eng.position:break
        if eng.position:
            eng.close_position(quote,'BACKTEST_SEGMENT_END_ASSUMED_FILL');eng.update_drawdown(quote)
            if eng.state['halt_reason'] and halt is None:halt=quote.observed_ms
        eng.reconcile_funding(prices,quote.observed_ms)
    row=dict(candidate=spec['id'],strategy_spec=spec,block_id=block['id'],days=block['days'],mode=MODE,cost=cost_name,path=path,
             start_ms=start+2000,end_ms=end-2,last_observation_ms=quote.observed_ms,initial_usdc='10',ending_usdc=str(eng.cash),
             account_config=asdict(cfg),halt_reason=eng.state['halt_reason'],halt_ms=halt,
             halted_fraction=(end-2-halt)/(end-2-start-2000) if halt else 0,
             sampled_max_drawdown_pct=str(D(eng.state['max_drawdown_fraction'])*100),open_position=eng.position is not None,
             pending_funding=eng.state['pending_funding'],integrity_warnings=eng.state['integrity_warnings'],trades=eng.state['trades'],
             metrics=paired.summarize(eng.state['trades'],cost_name,MODE),decisions=dict(eng.counts),
             potential_signals=sum(x['direction']!=0 for x in dv.values()),ema_rejected=sum(x['ema_rejected'] for x in dv.values()),
             evidence='RETROSPECTIVE_DISCONNECTED_EPISODES_NOT_A_CONTINUOUS_10_USDC_ACCOUNT')
    # Re-check ATR and frozen setup trace against independent features/decisions.
    for t in row['trades']:
        f=fv[t['signal_i']];eq(t['signal_atr'],f['atr'],'ATR');eq(t['signal_close'],f['close'],'Signal close')
        need(t['pattern_trace']==dv[t['signal_i']]['trace'],'Trace mismatch')
    row['independent_audit']=audit(row,block,ref)
    return row


def write(p,obj):
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def protocol():
    return dict(version='a44-profit-ablation-v1',primary=PRIMARY,baseline=BASELINE,specs=specs(),
                timeframe='5m_closed_signal_1m_execution',input='Unchanged Chainticks A44 exact files',
                warmup_5m_bars=120,groups=GROUPS,costs=COSTS,paths=PATHS,
                account=asdict(replace(Config(),max_hold_seconds=360*60)),blocks=27,cases=24*27*4,
                aggregation='27 independent 10 USDC episodes. Total/270 = mean episode return, NOT a continuous 10 USDC track record.',
                model='Original two cost assumptions; 1m OHLC/OLHC observed-price stop, unchanged one-sided target cap; funding close proxy.',
                purpose='Test earlier confirmed entry, structure-based stop within original risk caps, payoff and known-price headroom.',
                sources=['https://www.cmegroup.com/education/courses/trade-and-risk-management/proper-position-size',
                         'https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl'],
                no_orders=True,no_network=True,prior_run='35525531802',
                prior_sha='f171c51fcc5fafd4dc110ef0f634fe664bb54ba1')


def previous_baseline(previous):
    summary=json.loads((previous/'results/SUMMARY.json').read_text())
    need(summary['provenance']['GITHUB_SHA']==protocol()['prior_sha'] and
         str(summary['provenance']['GITHUB_RUN_ID'])==protocol()['prior_run'],'Incorrect prior result provenance')
    p=previous/'results/all-results.jsonl.gz'
    need(hashlib.sha256(p.read_bytes()).hexdigest()==PRIOR_RESULTS_SHA,'Prior results changed')
    with gzip.open(p,'rt') as f:
        rows=[r for line in f if (r:=json.loads(line))['candidate']==OLD_ID]
    need(len(rows)==108,'Prior baseline coverage')
    return {(r['block_id'],r['cost'],r['path']):r for r in rows}


def baseline_equal(actual,expected):
    for key in ('ending_usdc','halt_reason','halt_ms','last_observation_ms','sampled_max_drawdown_pct',
                'pending_funding','integrity_warnings','potential_signals','ema_rejected','metrics','decisions'):
        need(actual[key]==expected[key],'Baseline changed: '+key)
    need(len(actual['trades'])==len(expected['trades']),'Baseline trade count')
    for a,b in zip(actual['trades'],expected['trades']):
        for key,value in b.items():
            if key=='open_reason':continue
            if key=='pattern_trace':
                need(all(a[key][k]==v for k,v in value.items() if k!='candidate'),'Baseline setup changed')
            else:need(a[key]==value,'Baseline trade changed: '+key)
    return 'PASS_PRIOR_44_DAY_ECONOMIC_AND_TRADE_EQUIVALENCE'


def run(group,previous,out):
    need(0<=group<GROUPS,'Group');out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    plan=protocol();write(out/'protocol.json',plan)
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    write(out/'provenance.json',prov)
    root=Path(__file__).parents[1]/'reversal44';blocks,checks=load(previous/'input',root/'dataset_index.json');write(out/'data-checks.json',checks)
    baseline=previous_baseline(previous)
    write(out/'code-hashes.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')})
    selected=specs()[group::GROUPS];rows=[]
    with gzip.open(out/'results.jsonl.gz','wt',encoding='utf-8') as saved:
        for b in blocks:
            fv=features(b['signals']);rf=features_ref(b['signals']);check_features(fv,rf)
            for s in selected:
                dv=decisions(fv,s);ref=reference_decisions(rf,s);check_decisions(dv,ref)
                for cost in COSTS:
                    for path in PATHS:
                        need(time.monotonic()-started<1360,'Bounded job deadline')
                        r=replay(b,s,cost,path,fv,dv,ref)
                        if s['id']==BASELINE:r['baseline_equivalence']=baseline_equal(r,baseline[(b['id'],cost,path)])
                        saved.write(json.dumps(r,allow_nan=False)+'\n');saved.flush()
                        rows.append({k:r[k] for k in ('candidate','block_id','cost','path','metrics','independent_audit')})
            print('BLOCK_DONE',b['id'],len(rows),round(time.monotonic()-started,1),flush=True)
    need(len(rows)==len(selected)*27*4,'Incomplete scenario coverage')
    summary=dict(status='COMPLETED',group=group,candidates=[s['id'] for s in selected],cases=len(rows),
                 trades=sum(r['independent_audit']['trades'] for r in rows),funding_events=sum(r['independent_audit']['funding_events'] for r in rows),
                 provenance=prov,protocol_sha256=hashlib.sha256((out/'protocol.json').read_bytes()).hexdigest(),
                 data_checks_sha256=hashlib.sha256((out/'data-checks.json').read_bytes()).hexdigest(),
                 result_sha256=hashlib.sha256((out/'results.jsonl.gz').read_bytes()).hexdigest(),seconds=time.monotonic()-started)
    write(out/'SUMMARY.json',summary);print('GROUP_SUMMARY',json.dumps(summary),flush=True)


def combine(root,out):
    out.mkdir(parents=True,exist_ok=False)
    summaries=[json.loads(p.read_text()) for p in sorted(root.glob('*/SUMMARY.json'))]
    need(len(summaries)==GROUPS and sorted(s['group'] for s in summaries)==list(range(GROUPS)),'Missing/duplicate groups')
    need(len({s['protocol_sha256'] for s in summaries})==1 and len({s['data_checks_sha256'] for s in summaries})==1,'Mixed inputs/protocol')
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    need(all(s['provenance']==prov and s['status']=='COMPLETED' for s in summaries),'Stale/incomplete execution')
    rows=[]
    for p in sorted(root.glob('*/results.jsonl.gz')):
        s=json.loads((p.parent/'SUMMARY.json').read_text());need(hashlib.sha256(p.read_bytes()).hexdigest()==s['result_sha256'],'Result hash mismatch')
        with gzip.open(p,'rt') as f:rows.extend(json.loads(line) for line in f)
    identities={(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}
    expected={(s['id'],f'block_{i:02d}',c,p) for s in specs() for i in range(27) for c in COSTS for p in PATHS}
    need(len(rows)==2592 and identities==expected,'Full grid mismatch')
    aggregate=[]
    for s in specs():
        for cost in COSTS:
            for path in PATHS:
                rr=[r for r in rows if r['candidate']==s['id'] and r['cost']==cost and r['path']==path]
                n=sum(r['metrics']['trades'] for r in rr);w=sum(r['metrics']['wins'] for r in rr)
                total=sum((D(r['metrics']['net_usdc']) for r in rr),D(0));stress=sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))
                gains=sum((D(t['gross_pnl'])-D(t['entry_fee'])-D(t['exit_fee'])+sum((D(e['amount']) for e in t['funding_events']),D(0))
                           for r in rr for t in r['trades'] if D(t['gross_pnl'])-D(t['entry_fee'])-D(t['exit_fee'])+sum((D(e['amount']) for e in t['funding_events']),D(0))>0),D(0))
                loss=gains-total
                entry=dict(candidate=s['id'],entry=s['entry'],stop=s['stop'],reward=s['reward'],room=s['room'],cost=cost,path=path,
                    episodes=27,total_initial_usdc='270',net_sum_usdc=str(total),mean_episode_return_pct=str(total/D(270)*100),
                    trades=n,wins=w,net_win_rate=w/n if n else None,mean_net_usdc=str(total/n) if n else None,
                    profit_factor=str(gains/loss) if loss else None,fixed_trade_stress_sum_usdc=str(stress),
                    positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),
                    no_trade_episodes=sum(r['metrics']['trades']==0 for r in rr),halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                    max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                    forced_end_trades=sum(t['close_reason']=='BACKTEST_SEGMENT_END_ASSUMED_FILL' for r in rr for t in r['trades']))
                for k in ('price_only_usdc','spread_slippage_usdc','fees_usdc','funding_usdc','target_cap_haircut_usdc'):
                    entry[k]=str(sum((D(r['metrics'][k]) for r in rr),D(0)))
                entry['gate_counts']=dict(sum((Counter(r['decisions']) for r in rr),Counter()))
                aggregate.append(entry)
    write(out/'aggregate.json',aggregate)
    with (out/'aggregate.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(aggregate[0]));w.writeheader();w.writerows(aggregate)
    fields=['candidate','block_id','days','cost','path','ending_usdc','halt_reason','sampled_max_drawdown_pct','trades','wins','net_win_rate','net_usdc','fixed_trades_stress_net_usdc']
    with (out/'episodes.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:
            v={k:r.get(k,r['metrics'].get(k)) for k in fields};v['days']=';'.join(r['days']);v['trades']=r['metrics']['trades'];w.writerow(v)
    with gzip.open(out/'all-results.jsonl.gz','wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    robust=[]
    for s in specs():
        group=[r for r in aggregate if r['candidate']==s['id']]
        if all(D(x['net_sum_usdc'])>0 and D(x['fixed_trade_stress_sum_usdc'])>0 for x in group):robust.append(s['id'])
    result=dict(status='COMPLETED_RETROSPECTIVE_A44_PROFIT_ABLATION',cases=len(rows),candidates=24,episodes=27,days=44,
                trades=sum(s['trades'] for s in summaries),funding_events=sum(s['funding_events'] for s in summaries),
                primary=PRIMARY,baseline=BASELINE,baseline_equivalence_cases=sum(r.get('baseline_equivalence') is not None for r in rows),robust_positive_candidates=robust,provenance=prov,guaranteed_income=False,real_orders=0,
                best_by_base_net=max((r for r in aggregate if r['cost']=='base_assumptions' and r['path']=='OHLC'),key=lambda r:D(r['net_sum_usdc'])))
    need(result['baseline_equivalence_cases']==108,'Unverified baseline')
    result['base_positive_candidates']=[a['candidate'] for a in aggregate if a['cost']=='base_assumptions' and a['path']=='OHLC' and D(a['net_sum_usdc'])>0]
    write(out/'SUMMARY.json',result);print('FINAL_SUMMARY',json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--group',type=int);p.add_argument('--previous',type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--combine',type=Path)
    a=p.parse_args()
    if a.combine:combine(a.combine,a.out)
    else:run(a.group,a.previous,a.out)
