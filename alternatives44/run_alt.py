"""Twenty-four public-method adaptations + unchanged control on exact A44 data."""
import argparse
from contextlib import contextmanager
from collections import Counter
from dataclasses import asdict, replace
from decimal import Decimal as D, ROUND_CEILING
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import time

from paperlab import engine as core
from paperlab.common import Config
from paperlab.backtest import assumed_quote, FundingPrices, COSTS, PATHS
import paired
import run44 as old
from inputs44 import load, need, MINUTE
from run_profit import previous_baseline, baseline_equal
from signals_alt import specs, features, decisions, PRIMARY, BASELINE
from reference_alt import feature_reference, decision_reference, close_feature, check_choices, audit

GROUPS=4
MODE='FINE_1M_TP_CAP'


class AlternativeEngine(old.PatternEngine):
    def open_position(self,quote,signal):
        if self.spec['filter']=='ROOM':
            d=signal.direction;top=quote.asks[0][0] if d==1 else quote.bids[0][0]
            step=D(1).scaleb(-quote.sz_decimals)
            q=(D(self.cfg.target_notional)/top/step).to_integral_value(rounding=ROUND_CEILING)*step
            price=core.market_fill(quote,d,q,self.cfg).price
            friction=2*D(self.cfg.taker_fee)+2*D(self.cfg.adverse_slippage_bps)/10000+quote.spread_bps/10000
            mean=D(self.holder['choice']['trace']['mean'])
            if d*(mean-price)/price<3*friction:
                self.decision(quote.observed_ms,'MEAN_ROOM','Known mean too near after actual model fill')
                return False
        return super().open_position(quote,signal)


@contextmanager
def installed(holder):
    original=core.strategy_signal
    def signal(bars,cfg):
        c=holder['choice'];end=bars[-1]['T']
        return core.Signal(0 if holder['engine'].position else c['direction'],end,c['atr'],c['close'],holder['spec']['id'])
    try:
        core.strategy_signal=signal;yield
    finally:core.strategy_signal=original


def replay(block,s,cost_name,path,choices,reference):
    need(s in specs() and s['family']!='BASELINE' and cost_name in COSTS and path in PATHS,'Unknown experiment')
    need(choices.keys()==reference.keys(),'Missing decisions')
    cost=COSTS[cost_name];start,end=block['start'],block['end'];bars=block['candles']
    cfg=replace(Config(),max_hold_seconds=360*60,taker_fee=cost['taker_fee'],adverse_slippage_bps=cost['adverse_slippage_bps']).validate()
    holder={'spec':s};eng=AlternativeEngine(cfg,core.new_state(cfg,start+2000,'A44_PUBLIC_METHODS_PAPER'),s,MODE,holder);holder['engine']=eng
    eng.ingest_funding(block['funding']);prices=FundingPrices({'candles':bars,'funding':block['funding']})
    times=[r['time'] for r in block['funding']];pointer=0;halt=None;quote=None
    with installed(holder):
        for j,b in enumerate(bars):
            if b['t']<start:continue
            for field,off in zip('ohlc' if path=='OHLC' else 'olhc',(2000,21000,40000,59998)):
                quote=assumed_quote(b['t']+off,b[field],cost['spread_bps'],block['metadata'])
                if pointer<len(times) and times[pointer]<=quote.observed_ms:
                    eng.reconcile_funding(prices,quote.observed_ms)
                    while pointer<len(times) and times[pointer]<=quote.observed_ms:pointer+=1
                history=None
                if field=='o':
                    holder.update(i=j,choice=choices[j]);history=bars[j-120:j]
                eng.tick(quote,history);eng.update_drawdown(quote)
                if eng.state['halt_reason'] and halt is None:halt=quote.observed_ms
            if halt and not eng.position:break
        if eng.position:
            eng.close_position(quote,'BACKTEST_SEGMENT_END_ASSUMED_FILL');eng.update_drawdown(quote)
            if eng.state['halt_reason'] and halt is None:halt=quote.observed_ms
        eng.reconcile_funding(prices,quote.observed_ms)
    row=dict(candidate=s['id'],strategy_spec=s,block_id=block['id'],days=block['days'],mode=MODE,cost=cost_name,path=path,
             start_ms=start+2000,end_ms=end-2,last_observation_ms=quote.observed_ms,initial_usdc='10',ending_usdc=str(eng.cash),
             account_config=asdict(cfg),halt_reason=eng.state['halt_reason'],halt_ms=halt,
             halted_fraction=(end-2-halt)/(end-2-start-2000) if halt else 0,
             sampled_max_drawdown_pct=str(D(eng.state['max_drawdown_fraction'])*100),open_position=eng.position is not None,
             pending_funding=eng.state['pending_funding'],integrity_warnings=eng.state['integrity_warnings'],trades=eng.state['trades'],
             metrics=paired.summarize(eng.state['trades'],cost_name,MODE),decisions=dict(eng.counts),
             potential_signals=sum(c['direction']!=0 for c in choices.values()),
             evidence='RETROSPECTIVE_A44_DISCONNECTED_EPISODES_NOT_CONTINUOUS_ACCOUNT')
    row['independent_audit']=audit(row,block,reference)
    return row


def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def protocol():
    return dict(version='public-methods-a44-v1',specs=specs(),primary=PRIMARY,baseline=BASELINE,
                groups=GROUPS,cases=25*27*4,costs=COSTS,paths=PATHS,mode=MODE,
                account=asdict(replace(Config(),max_hold_seconds=21600)),no_network=True,no_orders=True,
                data='Same A44: 63360 minutes, 27 adjacent episodes, first 600 min per episode warmup',
                aggregation='Independent 10 USDC per episode; sum/270 gives mean episode return, not continuous 10 USDC profit.',
                exit='Unchanged hard stop, 1.8R capped target, 360min, drawdown/three-loss halts; NOT original external strategy exits.',
                selection='24 fixed definitions and one control, all results disclosed. No automatic deployment or no-trade winner.',
                sources=['https://www.bollingerbands.com/bollinger-band-rules',
                         'https://github.com/freqtrade/freqtrade-strategies/blob/7f91ff52bb664423ae673092a6a18d76c50c2c29/user_data/strategies/berlinguyinca/BbandRsi.py',
                         'https://tradingmarkets.com/recent/the_improved_r2_strategy_84_correct_with_just_6_rules_-674361'])


def run(group,previous,out):
    need(0<=group<GROUPS,'Bad group');out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    plan=protocol();write(out/'protocol.json',plan)
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')};write(out/'provenance.json',prov)
    index=Path(__file__).parents[1]/'reversal44/dataset_index.json'
    blocks,checks=load(previous/'previous/input',index);write(out/'data-checks.json',checks)
    baseline=previous_baseline(previous/'previous')
    write(out/'code-hashes.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')})
    selected=specs()[group::GROUPS];count=trades=funding=equiv=0
    with gzip.open(out/'results.jsonl.gz','wt') as save:
        for block in blocks:
            fv=features(block['signals']);rf=feature_reference(block['signals']);close_feature(fv,rf,'All features')
            for s in selected:
                if s['family']=='BASELINE':
                    ospec=next(x for x in old.specs() if x['id']==BASELINE)
                    of=old.features(block['signals']);od=old.decisions(of,ospec);orr=old.reference_decisions(old.features_ref(block['signals']),ospec)
                else:
                    choices=decisions(block,fv,s);ref=decision_reference(block,rf,s);check_choices(choices,ref)
                for cost in COSTS:
                    for path in PATHS:
                        need(time.monotonic()-started<1360,'Research timeout')
                        if s['family']=='BASELINE':
                            row=old.replay(block,ospec,cost,path,of,od,orr)
                            row['baseline_equivalence']=baseline_equal(row,baseline[(block['id'],cost,path)]);equiv+=1
                        else:row=replay(block,s,cost,path,choices,ref)
                        save.write(json.dumps(row,allow_nan=False)+'\n');save.flush();count+=1
                        trades+=row['independent_audit']['trades'];funding+=row['independent_audit']['funding_events']
            print('BLOCK_DONE',block['id'],count,round(time.monotonic()-started,2),flush=True)
    need(count==len(selected)*108,'Incomplete coverage')
    summary=dict(status='COMPLETED',group=group,cases=count,trades=trades,funding_events=funding,baseline_cases=equiv,
                 provenance=prov,candidates=[s['id'] for s in selected],
                 protocol_sha256=hashlib.sha256((out/'protocol.json').read_bytes()).hexdigest(),
                 data_checks_sha256=hashlib.sha256((out/'data-checks.json').read_bytes()).hexdigest(),
                 result_sha256=hashlib.sha256((out/'results.jsonl.gz').read_bytes()).hexdigest())
    write(out/'SUMMARY.json',summary);print('GROUP_SUMMARY',json.dumps(summary),flush=True)


def combine(root,out):
    out.mkdir(parents=True,exist_ok=False);ss=[json.loads(p.read_text()) for p in sorted(root.glob('*/SUMMARY.json'))]
    need(len(ss)==GROUPS and sorted(s['group'] for s in ss)==list(range(GROUPS)),'Missing/duplicate groups')
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    need(all(s['status']=='COMPLETED' and s['provenance']==prov for s in ss),'Wrong provenance/status')
    for k in ('data_checks_sha256','protocol_sha256'):need(len({s[k] for s in ss})==1,'Mixed data/protocol')
    rows=[]
    for p in sorted(root.glob('*/results.jsonl.gz')):
        s=json.loads((p.parent/'SUMMARY.json').read_text());need(hashlib.sha256(p.read_bytes()).hexdigest()==s['result_sha256'],'Result hash')
        with gzip.open(p,'rt') as f:rows.extend(json.loads(x) for x in f)
    expected={(s['id'],f'block_{i:02d}',c,p) for s in specs() for i in range(27) for c in COSTS for p in PATHS}
    need(len(rows)==2700 and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Full grid')
    aggregates=[]
    for s in specs():
        for cost in COSTS:
            for path in PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(s['id'],cost,path)]
                n=sum(r['metrics']['trades'] for r in rr);wins=sum(r['metrics']['wins'] for r in rr)
                total=sum((D(r['metrics']['net_usdc']) for r in rr),D(0))
                a=dict(candidate=s['id'],family=s['family'],param=s['param'],trigger=s['trigger'],filter=s['filter'],cost=cost,path=path,
                       trades=n,wins=w,net_win_rate=wins/n if n else None,net_sum_usdc=str(total),
                       mean_episode_return_pct=str(total/270*100),mean_net_usdc=str(total/n) if n else None,
                       positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),
                       no_trade_episodes=sum(not r['trades'] for r in rr),halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                       max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                       fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))))
                for k in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):
                    a[k]=str(sum((D(r['metrics'][k]) for r in rr),D(0)))
                a['gate_counts']=dict(sum((Counter(r['decisions']) for r in rr),Counter()));aggregates.append(a)
    write(out/'aggregate.json',aggregates)
    with (out/'aggregate.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(aggregates[0]));w.writeheader();w.writerows(aggregates)
    with gzip.open(out/'all-results.jsonl.gz','wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    base=[r for r in aggregates if r['cost']=='base_assumptions' and r['path']=='OHLC']
    robust=[s['id'] for s in specs() if all(D(x['net_sum_usdc'])>0 and D(x['fixed_trade_stress_sum_usdc'])>0 for x in aggregates if x['candidate']==s['id'])]
    summary=dict(status='COMPLETED_RETROSPECTIVE_PUBLIC_METHOD_RESEARCH',cases=2700,candidates=25,episodes=27,days=44,
                 primary=PRIMARY,baseline=BASELINE,provenance=prov,baseline_cases=sum(s['baseline_cases'] for s in ss),
                 trades=sum(s['trades'] for s in ss),funding_events=sum(s['funding_events'] for s in ss),
                 base_positive=[r['candidate'] for r in base if D(r['net_sum_usdc'])>0],robust_positive=robust,
                 best_trading_base=max((r for r in base if r['trades']),key=lambda r:D(r['net_sum_usdc'])),
                 no_orders=True,guaranteed_income=False)
    need(summary['baseline_cases']==108,'Baseline incomplete');write(out/'SUMMARY.json',summary)
    print('FINAL_SUMMARY',json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--group',type=int);p.add_argument('--previous',type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--combine',type=Path)
    a=p.parse_args();combine(a.combine,a.out) if a.combine else run(a.group,a.previous,a.out)
