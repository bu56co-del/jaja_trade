"""Bounded, offline paired execution experiment on saved public ETH history."""
import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from decimal import Decimal as D
from dataclasses import asdict

from audit_data import write_json, digest
from paperlab.common import Config
from paperlab.backtest import COSTS, PATHS
from inputs import load_inputs, START, END, DATA_SHA, PRIOR_RUN, PRIOR_SHA
from market_data import need, iso
import paired
import verify_execution as verify


def protocol():
    return dict(version='paired-execution-v1',prior_run=PRIOR_RUN,prior_commit=PRIOR_SHA,
        data_sha256=DATA_SHA,network='mainnet',asset='ETH',start=START,end_exclusive=END,
        specs=paired.SPECS,execution_modes=paired.MODES,primary=paired.PRIMARY,costs=COSTS,paths=PATHS,
        expected_cases=36,account=asdict(Config()),
        signal='Closed 15m breakout+EMA9/21 trend/slope+ER20>=.2. 120 prior closed bars. '
               'Only boundary+2s receives signal; intermediate 1m samples only manage an existing position.',
        failure_exit='Freeze prior20 high/low excluding signal bar at entry; last two 15m closes must both '
                     'finish after entry and fall back inside that fixed boundary by strictly >0.25 entry ATR. '
                     'Exit next 15m boundary; risk/stop/target/deadline precede strategy exits.',
        execution='COARSE: o,h/l,l/h,c at +2s,35%,2/3,duration-2ms. FINE: same within each native 1m. '
                  'TP_CAP: replays full ledger, caps favorable target exits only; never improves stops. '
                  'Liquidation-value estimates also cap target surplus in this sensitivity case.',
        funding='Same historical rates and preceding15m close proxy for all modes; no historical oracle claim.',
        shadow='Independent forward reference-price labels at 90/360m incl. after halt, no paper account or trade '
               'win rate, no P&L, no merge into account sample. Censored at end, overlapping observations.',
        frozen_rules='No tuning. Same previously seen two-day window. NOT an unseen/forward confirmation.',
        evidence='>=55/60 sample win rate and >0 net displayed separately from 5% return. '
                 'No validated claim on seen data. Price/depth/mark proxies, 1m paths and short sample remain.',
        bounded='One job <=25m, no new market request, no schedule, no paid services, no orders or deployment.',
        sources=['https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl',
                 'https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding'])


def no_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen','os.system','os.posix_spawn'):
        raise RuntimeError('Offline experiment forbids network/process side effects')


def baseline_check(row,old):
    original='BREAK_TREND_CONFIRM_'+str(row['strategy_spec']['max_minutes'])
    matches=[r for r in old if r['candidate']==original and r['interval']=='15m'
             and r['window']=='common_2d' and r['cost']==row['cost'] and r['path']==row['path']]
    need(len(matches)==1,'Missing/duplicate legacy control')
    before=matches[0];need(len(before['trades'])==len(row['trades']),'Legacy trade count changed')
    for a,b in zip(before['trades'],row['trades']):
        for k in a:
            if k!='open_reason':need(a[k]==b[k],'Legacy control differs '+k)
    for k in ('ending_usdc','halt_reason','halt_ms','sampled_max_drawdown_pct'):
        need(before[k]==row[k],'Legacy aggregate changed '+k)
    return dict(status='PASS_LEGACY_TRADE_AND_RISK_EQUIVALENCE',note='New descriptive entry labels/trace only excluded')


def outcome(rows):
    if all(r['metrics']['trades']==0 for r in rows):return 'NO_TRADES'
    nets=[D(r['metrics']['net_usdc']) for r in rows]
    if all(x>0 for x in nets):return 'POSITIVE'
    if all(x<0 for x in nets):return 'NEGATIVE'
    return 'MIXED'


def export(out,rows,provenance,checks,shadow):
    need(len(rows)==36 and len({(r['candidate'],r['mode'],r['cost'],r['path']) for r in rows})==36,'Scenario coverage')
    combos={(s['id'],m,c,p) for s in paired.SPECS for m in paired.MODES for c in COSTS for p in PATHS}
    need({(r['candidate'],r['mode'],r['cost'],r['path']) for r in rows}==combos,'Wrong candidate space')
    selected=[r for r in rows if (r['candidate'],r['mode'])==paired.PRIMARY]
    summary=dict(status='COMPLETED_PAIRED_EXECUTION_RESEARCH',primary=paired.PRIMARY,primary_sign=outcome(selected),
        cases=len(rows),start=iso(START),end_exclusive=iso(END),prior_run=PRIOR_RUN,
        dataset_sha256=DATA_SHA,market_requests_in_this_run=0,provenance=provenance,
        counts={sg:sum(outcome([r])==sg for r in rows) for sg in ('POSITIVE','NEGATIVE','NO_TRADES','MIXED')},
        max_closed_trades=max(r['metrics']['trades'] for r in rows),
        observed55_positive_cases=sum(r['metrics']['observed55_and_positive'] for r in rows),
        observed60_positive_cases=sum(r['metrics']['observed60_and_positive'] for r in rows),
        return5_cases=sum(r['metrics']['return5'] for r in rows),
        qualified_reliable55=0,independent60='NOT_VERIFIED',future_profitability='NOT_ESTABLISHED',
        evidence='INSUFFICIENT_SAMPLE_RETROSPECTIVE_NOT_UNSEEN',real_orders=0)
    write_json(out/'SUMMARY.json',summary)
    with gzip.open(out/'all-results.json.gz','wt',encoding='utf-8') as f:json.dump(rows,f,ensure_ascii=False,allow_nan=False)
    columns=['candidate','mode','cost','path','trades','wins','net_win_rate','net_usdc','return_pct',
        'mean_net_usdc','price_only_usdc','spread_slippage_usdc','target_cap_haircut_usdc','fees_usdc','funding_usdc',
        'fixed_trades_stress_net_usdc','sampled_max_drawdown_pct','halt_reason','halted_fraction']
    with (out/'all-scenarios.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        for r in rows:
            data={**r,**r['metrics']};w.writerow({k:data.get(k) for k in columns})
    pairs=[]
    for spec in paired.SPECS:
        for cost in COSTS:
            for path in PATHS:
                cases={r['mode']:r for r in rows if r['candidate']==spec['id'] and r['cost']==cost and r['path']==path}
                n={k:D(v['metrics']['net_usdc']) for k,v in cases.items()}
                pairs.append(dict(candidate=spec['id'],cost=cost,path=path,
                    finer_minus_coarse_net=str(n['FINE_1M']-n['COARSE_15M']),
                    cap_minus_fine_net=str(n['FINE_1M_TP_CAP']-n['FINE_1M']),
                    note='Full paired reruns, later trades/halts may differ; not additive independent profits.'))
    write_json(out/'paired-differences.json',pairs)
    write_json(out/'shadow-signals.json',shadow)
    text=['# 15m訊號／1m執行配對研究','',f"**事前主觀察 {paired.PRIMARY}: {summary['primary_sign']}**",'',
        '2026-09-18至09-20 00:00 UTC，已保存及已看過主網ETH資料。不是新行情或樣本外盈利證據。',
        '36個完整情境；每案獨立虛擬10 USDC，不補錢，不串接收益。',
        '1m仍是OHLC四點假設，不是歷史tick/L2，也不是交易所mark-trigger訂單重建。','',
        '|策略|執行|成本|路徑|贏/平倉|淨利USDC|固定交易壓力淨利|',
        '|---|---|---|---|---:|---:|---:|']
    for r in rows:
        m=r['metrics']
        text.append(f"|{r['candidate']}|{r['mode']}|{r['cost']}|{r['path']}|{m['wins']}/{m['trades']}|{D(m['net_usdc']):+.8f}|{D(m['fixed_trades_stress_net_usdc']):+.8f}|")
    text+=['','原帳戶永久停機不重設；shadow係獨立價格診斷，沒有帳戶回報或交易勝率。',
        '止賺封頂是單向保守敏感度，並非真實限價單／市場單成交模型。',
        '全部成本、期間、路徑、規則固定；負數與無交易均保留。55/60觀察值不等於未來勝率。',
        f"達55%且淨正案例：{summary['observed55_positive_cases']}；可靠門檻通過：0。",
        '精確歷史mark/oracle、L2、latency與未見長樣本仍NOT VERIFIED；沒有訂單、合併、部署。']
    (out/'REPORT.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return summary


def run(prior,prior_final,code,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    plan=protocol();write_json(out/'protocol.json',plan)
    files={str(p.relative_to(code)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted((Path(code)/'execution').rglob('*')) if p.is_file() and p.suffix in ('.py','.md')}
    provenance={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
    provenance.update(sources=files,protocol_sha256=digest(plan),frozen_ms=time.time_ns()//1000000)
    write_json(out/'frozen.json',provenance)
    rows=[]
    try:
        data,old,checks=load_inputs(prior,prior_final,code)
        write_json(out/'input-audit.json',checks)
        # Saved data is part of the result artifact, not fetched through an untrusted URL.
        write_json(out/'inputs.json',{'1m':data['1m'],'15m':data['15m']})
        fv=paired.features(data['15m']['candles']);ref=verify.reference_features(data['15m']['candles'],fv)
        write_json(out/'features-audit.json',dict(status='PASS_INDEPENDENT_FEATURES',count=len(ref)))
        for spec in paired.SPECS:
            for mode in paired.MODES:
                for cost in COSTS:
                    for path in PATHS:
                        row=paired.replay(data,START,END,spec,mode,cost,path,fv)
                        row['independent_audit']=verify.audit(row,data,ref)
                        if mode=='COARSE_15M' and spec['exit']=='confirm':row['legacy_control']=baseline_check(row,old)
                        rows.append(row);write_json(out/'checkpoint.json',rows)
                        m=row['metrics']
                        print(f"[{len(rows)}/36] {spec['id']} {mode} {cost} {path} net={m['net_usdc']} wins={m['wins']}/{m['trades']}",flush=True)
        shadow=paired.shadow_signals(data,START,END,fv)
        shadow['after_halt_by_case']=[dict(candidate=r['candidate'],mode=r['mode'],cost=r['cost'],path=r['path'],halt_ms=r['halt_ms'],
            signal_open_ms=[s['signal_open_ms'] for s in shadow['signals'] if r['halt_ms'] is not None and s['signal_open_ms']>r['halt_ms']]) for r in rows]
        write_json(out/'shadow-audit.json',verify.audit_shadow(shadow,data,START,END,ref,rows))
        summary=export(out,rows,provenance,checks,shadow)
        write_json(out/'status.json',dict(status='COMPLETE',cases=len(rows),primary_sign=summary['primary_sign']))
        print('FINAL '+json.dumps(summary,ensure_ascii=False),flush=True)
        return 0
    except Exception as exc:
        write_json(out/'status.json',dict(status='FAILED_NO_VALIDATED_RESULT',completed_cases=len(rows),error=str(exc)))
        print('FAILED_NO_VALIDATED_RESULT '+str(exc),flush=True)
        return 2
    finally:
        write_json(out/'manifest.json',{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out.rglob('*')) if p.is_file() and p.name!='manifest.json'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior',type=Path,required=True);parser.add_argument('--prior-final',type=Path,required=True)
    parser.add_argument('--code',type=Path,default=Path.cwd());parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();sys.addaudithook(no_network)
    raise SystemExit(run(args.prior,args.prior_final,args.code,args.out))
