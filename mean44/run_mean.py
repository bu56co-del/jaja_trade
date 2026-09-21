"""Frozen recovered-extreme / dynamic-exit experiment on the existing A44 sample."""
import argparse
from contextlib import contextmanager
from dataclasses import asdict,replace
from decimal import Decimal as D
from collections import Counter
from pathlib import Path
import gzip,hashlib,json,csv,os,time
from paperlab import engine as core
from paperlab.common import Config
from paperlab.backtest import assumed_quote,FundingPrices,COSTS,PATHS
import paired,run44
from inputs44 import load,need,MINUTE
from signals_mean import specs,features,choices,PRIMARY,BASELINE
from verify_mean import reference,check,audit
from reference44 import eq

GROUPS=4
MODE='FINE_1M_TP_CAP'
PREVIOUS_RESULTS_SHA='c21c8383346ed5ade8361918dcee1f12522c2c48c3cd2880f166de16ac51f8ae'


class MeanEngine(run44.PatternEngine):
    def close_position(self,quote,reason):
        if reason=='OPPOSITE_EMA_CROSS':reason='DYNAMIC_'+self.spec['exit']
        return super().close_position(quote,reason)


@contextmanager
def installed(holder):
    before=core.strategy_signal
    def signal(bars,cfg):
        c=holder['choice'];p=holder['engine'].position
        direction=(-p['direction'] if c['exit_long' if p['direction']==1 else 'exit_short'] else 0) if p else c['direction']
        return core.Signal(direction,bars[-1]['T'],c['atr'],c['close'],holder['spec']['id'])
    try:
        core.strategy_signal=signal;yield
    finally:core.strategy_signal=before


def replay(block,s,cost_name,path,cs,ref):
    need(s in specs() and s['family']!='BASELINE','Unknown new hypothesis')
    cost=COSTS[cost_name];start,end=block['start'],block['end'];bars=block['candles']
    cfg=replace(Config(),max_hold_seconds=21600,taker_fee=cost['taker_fee'],adverse_slippage_bps=cost['adverse_slippage_bps']).validate()
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


def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def protocol():
    return dict(version='recovery-dynamic-v1',specs=specs(),primary=PRIMARY,baseline=BASELINE,groups=GROUPS,cases=2700,
                account=asdict(replace(Config(),max_hold_seconds=21600)),costs=COSTS,paths=PATHS,
                data='Fixed A44, 63360 1m candles, 27 actual contiguous episodes, each first600 minutes warmup.',
                aggregation='Independent10USDC per episode, sum net/270 = average episode return, not one10USDC track record.',
                math='Closed-price BB20 +/-2 population SD; Wilder RSI2; SMA5; ER20. 5m uses120 history bars,15m uses40.',
                setup='Previous close outside prior band, latest inside latest band; or RSI2 recovers above10/below90. Body must agree. Optional ER<=0.30.',
                exit='Original hard stop/1.8R capped target/360min/halting always first; optional closed-bar SMA5 or RSI2 above70/below30 then NEXT model open.',
                sources=['https://www.bollingerbands.com/bollinger-band-rules','https://tradingmarkets.com/recent/dynamic_exits_how_to_properly_exit_a_trade-641067'],
                prior_run='35560801424',prior_sha='37e821f456dd0787d57930ca84f85c312532b59d',
                no_network=True,no_orders=True,not_exact_external_strategy_reproduction=True)


def load_previous(previous):
    index=Path(__file__).parents[1]/'reversal44/dataset_index.json'
    blocks,checks=load(previous/'previous/previous/input',index)
    p=previous/'results/all-results.jsonl.gz'
    need(hashlib.sha256(p.read_bytes()).hexdigest()==PREVIOUS_RESULTS_SHA,'Previous results hash')
    with gzip.open(p,'rt') as f:old=[json.loads(x) for x in f]
    need(len(old)==2700,'Previous grid count')
    baseline={(r['block_id'],r['cost'],r['path']):r for r in old if r['candidate']==BASELINE}
    need(len(baseline)==108,'Baseline count')
    return blocks,checks,baseline


def verify_baseline(a,b):
    for k in ('trades','ending_usdc','halt_reason','halt_ms','sampled_max_drawdown_pct','metrics'):
        need(a[k]==b[k],'Baseline changed '+k)


def run(group,previous,out):
    need(0<=group<GROUPS,'Group');out.mkdir(parents=True,exist_ok=False);began=time.monotonic()
    plan=protocol();write(out/'protocol.json',plan)
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')};write(out/'provenance.json',prov)
    blocks,data,baseline=load_previous(previous);write(out/'data-checks.json',data)
    selected=specs()[group::GROUPS];count=trades=funding=equiv=0
    with gzip.open(out/'results.jsonl.gz','wt') as save:
        for block in blocks:
            banks={m:features(block,m) for m in (5,15)}
            for s in selected:
                if s['family']=='BASELINE':
                    oldspec=next(x for x in run44.specs() if x['id']==BASELINE)
                    of=run44.features(block['signals']);od=run44.decisions(of,oldspec);oref=run44.reference_decisions(run44.features_ref(block['signals']),oldspec)
                else:
                    cs=choices(block,banks[s['minutes']],s);ref=reference(block,s);check(cs,ref)
                for cost in COSTS:
                    for path in PATHS:
                        need(time.monotonic()-began<1360,'Deadline')
                        if s['family']=='BASELINE':
                            r=run44.replay(block,oldspec,cost,path,of,od,oref);verify_baseline(r,baseline[(block['id'],cost,path)]);equiv+=1
                        else:r=replay(block,s,cost,path,cs,ref)
                        save.write(json.dumps(r,allow_nan=False)+'\n');save.flush();count+=1
                        trades+=len(r['trades']);funding+=sum(len(t['funding_events']) for t in r['trades'])
            print('BLOCK_DONE',group,block['id'],count,round(time.monotonic()-began,2),flush=True)
    need(count==len(selected)*108,'Incomplete group')
    summary=dict(status='COMPLETED',group=group,cases=count,trades=trades,funding_events=funding,baseline_cases=equiv,
                 provenance=prov,candidates=[s['id'] for s in selected],
                 protocol_sha256=hashlib.sha256((out/'protocol.json').read_bytes()).hexdigest(),
                 data_checks_sha256=hashlib.sha256((out/'data-checks.json').read_bytes()).hexdigest(),
                 result_sha256=hashlib.sha256((out/'results.jsonl.gz').read_bytes()).hexdigest())
    write(out/'SUMMARY.json',summary);print('GROUP_SUMMARY',json.dumps(summary),flush=True)


def combine(root,out):
    out.mkdir(parents=True,exist_ok=False);ss=[json.loads(p.read_text()) for p in sorted(root.glob('*/SUMMARY.json'))]
    need(len(ss)==GROUPS and sorted(s['group'] for s in ss)==list(range(GROUPS)),'Groups missing')
    prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    need(all(s['status']=='COMPLETED' and s['provenance']==prov for s in ss),'Mixed/stale runs')
    for key in ('protocol_sha256','data_checks_sha256'):need(len({s[key] for s in ss})==1,'Mixed protocol/data')
    rows=[]
    for p in sorted(root.glob('*/results.jsonl.gz')):
        s=json.loads((p.parent/'SUMMARY.json').read_text());need(hashlib.sha256(p.read_bytes()).hexdigest()==s['result_sha256'],'Result hash')
        with gzip.open(p,'rt') as f:rows.extend(json.loads(x) for x in f)
    expected={(s['id'],f'block_{i:02d}',c,p) for s in specs() for i in range(27) for c in COSTS for p in PATHS}
    need(len(rows)==2700 and {(r['candidate'],r['block_id'],r['cost'],r['path']) for r in rows}==expected,'Incomplete grid')
    agg=[]
    for s in specs():
        for cost in COSTS:
            for path in PATHS:
                rr=[r for r in rows if (r['candidate'],r['cost'],r['path'])==(s['id'],cost,path)]
                n=sum(len(r['trades']) for r in rr);w=sum(r['metrics']['wins'] for r in rr)
                net=sum((D(r['metrics']['net_usdc']) for r in rr),D(0))
                a=dict(candidate=s['id'],family=s['family'],minutes=s['minutes'],filter=s['filter'],exit=s['exit'],cost=cost,path=path,
                       trades=n,wins=w,net_win_rate=w/n if n else None,net_sum_usdc=str(net),mean_episode_return_pct=str(net/270*100),
                       mean_net_usdc=str(net/n) if n else None,positive_episodes=sum(D(r['metrics']['net_usdc'])>0 for r in rr),
                       negative_episodes=sum(D(r['metrics']['net_usdc'])<0 for r in rr),no_trade_episodes=sum(not r['trades'] for r in rr),
                       halted_episodes=sum(bool(r['halt_reason']) for r in rr),
                       max_single_episode_drawdown_pct=str(max(D(r['sampled_max_drawdown_pct']) for r in rr)),
                       fixed_trade_stress_sum_usdc=str(sum((D(r['metrics']['fixed_trades_stress_net_usdc']) for r in rr),D(0))))
                for key in ('price_only_usdc','fees_usdc','spread_slippage_usdc','funding_usdc','target_cap_haircut_usdc'):
                    a[key]=str(sum((D(r['metrics'][key]) for r in rr),D(0)))
                a['exit_counts']=dict(sum((Counter(r['metrics']['exit_reasons']) for r in rr),Counter()));agg.append(a)
    write(out/'aggregate.json',agg)
    with (out/'aggregate.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(agg[0]));w.writeheader();w.writerows(agg)
    with gzip.open(out/'all-results.jsonl.gz','wt') as f:
        for r in rows:f.write(json.dumps(r)+'\n')
    base=[a for a in agg if a['cost']=='base_assumptions' and a['path']=='OHLC']
    summary=dict(status='COMPLETED_A44_RECOVERY_DYNAMIC_EXIT_RESEARCH',cases=len(rows),candidates=len(specs()),episodes=27,days=44,
                 primary=PRIMARY,baseline=BASELINE,provenance=prov,trades=sum(s['trades'] for s in ss),funding_events=sum(s['funding_events'] for s in ss),
                 baseline_cases=sum(s['baseline_cases'] for s in ss),base_positive=[a['candidate'] for a in base if D(a['net_sum_usdc'])>0],
                 robust_positive=[s['id'] for s in specs() if all(D(a['net_sum_usdc'])>0 and D(a['fixed_trade_stress_sum_usdc'])>0 for a in agg if a['candidate']==s['id'])],
                 best_trading_base=max((a for a in base if a['trades']),key=lambda a:D(a['net_sum_usdc'])),no_orders=True)
    need(summary['baseline_cases']==108,'Baseline missing');write(out/'SUMMARY.json',summary);print('FINAL_SUMMARY',json.dumps(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--group',type=int);p.add_argument('--previous',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--combine',type=Path)
    a=p.parse_args();combine(a.combine,a.out) if a.combine else run(a.group,a.previous,a.out)
