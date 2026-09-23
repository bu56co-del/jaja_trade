"""Bounded Issue #1 research, with selection sealed before unseen data acquisition."""
import argparse
import csv
from dataclasses import asdict
from decimal import Decimal as D
import json
import os
from pathlib import Path
import sys
import time

from audit_data import PublicClient, fetch_dataset, write_json, digest
from paperlab.backtest import COSTS, PATHS, LIMITATIONS, validate_dataset
from paperlab.common import Config
import strategies
from statistics_report import metrics, fixed_trade_stress
from verify import (OLD_COMMIT, OLD_DATA, ENGINE_HASH, baseline_check, raw_check, overlap_check,
                    load, audit_row, require, equal, price_at, COST)


def protocol():
    return dict(version='issue1-research-v1', network='mainnet', coin='ETH', interval='1m',
                original_commit=OLD_COMMIT, original_dataset=OLD_DATA, original_engine=ENGINE_HASH,
                candidates=strategies.candidates(), account=asdict(Config()), costs=COSTS, paths=PATHS,
                research_rounds=2, max_new_candidates=12, job_timeout_minutes=25,
                round1='Eight entry filters: closed 5m trend, or 20-close directional efficiency.',
                round2='Four trailing-range breakouts with directional-efficiency filters. Predeclared, not holdout-tuned.',
                ranking='Among NEW candidates only: highest minimum net P&L across four development cost/path cases; '
                        'then highest minimum net win rate; then lexicographically smallest ID. Zero-trade rate=-1 for ranking.',
                selection='All old data is development. Save selection.json BEFORE any fresh API request. '
                          'Only bars after the old dataset end may be new evaluation. Never select on new results.',
                evaluation='Selected candidate plus two labelled controls, one continuous virtual account per scenario. '
                           'Partition into three non-overlapping reporting folds WITHOUT replenishment/reset. '
                           'Historical warm-up is permitted, historical entries are not.',
                evidence_gate='At least 100 distinct closed round trips per scenario, three reporting folds with trades, '
                              'positive net and >=55% net win rate under BOTH gated cost reruns and fixed-trade stress. '
                              'These are research gates, not a proof of a future probability. Show Wilson and day bootstrap.',
                phase60='An observed >=60% sample is reported, but an independent 60% research confirmation requires '
                        'new evaluation data after the 55% decision, not reusing this tail.',
                failure='Keep failed/no-trade candidates; stop after two finite research rounds. No schedule or infinite retries.',
                limitations=LIMITATIONS + ['NEW_PERIOD_MAY_BE_VERY_SHORT', 'NO_INDEPENDENT_SECOND_MARKET_DATA_SOURCE',
                    'FIXED_TRADE_COST_STRESS_IS_COUNTERFACTUAL_NOT_A_NEW_EXECUTION_REPLAY'])


def rank_candidate(spec, rows):
    cases = [r for r in rows if r['candidate']==spec['id']]
    require(len(cases)==4,'Candidate grid incomplete')
    return (min(D(r['model_net_pnl_usdc']) for r in cases),
            min(-1 if r['metrics']['net_win_rate'] is None else r['metrics']['net_win_rate'] for r in cases))


def select(rows):
    choices = sorted((s for s in strategies.candidates() if s['family']!='control'),key=lambda s:s['id'])
    return max(choices,key=lambda s:rank_candidate(s,rows))


def snapshot(row, data, at):
    cash = D(10)
    active = None
    for t in row['trades']:
        if t['opened_ms']>at:
            continue
        cash -= D(t['entry_fee'])
        cash += sum((D(f['amount']) for f in t['funding_events'] if f['time']<=at),D(0))
        if t['closed_ms']<=at:
            cash += D(t['gross_pnl'])-D(t['exit_fee'])
        else:
            active = t
    eq = cash
    if active:
        fee, half, slip = COST[row['cost']]
        d, q = active['direction'], D(active['qty'])
        px = price_at(data,at,row['path'])*(1-d*half)*(1-d*slip)
        eq += d*q*(px-D(active['entry']))-q*px*fee
    return dict(at_ms=at,cash_usdc=str(cash),liquidation_value_usdc=str(eq),
                open_trade_id=None if active is None else active['id'],
                halted=bool(row['halt_ms'] and row['halt_ms']<=at))


def add_statistics(row, data, lo, hi):
    row['metrics'] = metrics(row['trades'],row['start_ms'],row['end_ms'])
    equal(row['metrics']['net_usdc'],row['model_net_pnl_usdc'],'Independent summary')
    exposure = sum(t['closed_ms']-t['opened_ms'] for t in row['trades'])
    row['exposure_fraction'] = exposure/(row['end_ms']-row['start_ms'])
    if row['cost']=='base_assumptions':
        fixed = fixed_trade_stress(row['trades'])
        row['fixed_trade_stress_metrics']=metrics(fixed,row['start_ms'],row['end_ms'])
        row['fixed_trade_stress_trades']=fixed
    boundaries = [lo,lo+(hi-lo)//3,lo+2*(hi-lo)//3,hi]
    row['reporting_folds']=[]
    for i in range(3):
        a,b=boundaries[i:i+2]
        if a>=b:
            continue
        start=data['candles'][a]['t']
        end=data['candles'][b-1]['T']-1
        assigned=[t for t in row['trades'] if start<=t['closed_ms']<=end]
        row['reporting_folds'].append(dict(index=i,start_ms=start,end_ms=end,
            metrics=metrics(assigned,start,end),
            state_before=snapshot(row,data,start-2),state_after=snapshot(row,data,end),
            note='Trade statistics assigned by close time. A trade appears in one fold only. '
                 'Not separate accounts or standalone fold returns; compare equity snapshots including open positions.'))
    row['evidence_status']='INSUFFICIENT_SAMPLE' if (len(row['trades'])<100 or
        len(row['reporting_folds'])<3 or any(not f['metrics']['closed_trades'] for f in row['reporting_folds'])) else 'EXPLORATORY_ONLY'
    return row


def evaluate(data, spec, cost, path, lo, hi, phase):
    row=strategies.replay(data,lo,hi,spec,cost,path)
    row['phase']=phase
    row['independent_audit']=audit_row(row,data,spec)
    return add_statistics(row,data,lo,hi)


def export(output, rows):
    fields=['phase','candidate','cost','path','net_usdc','return_pct','wins','losses','flat','closed_trades',
            'net_win_rate','observed_55','observed_60','return_ge_5pct','model_max_drawdown_pct','halt_reason','evidence_status']
    with (output/'all_scenarios.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in rows:
            writer.writerow({k:({**r,**r['metrics']}).get(k) for k in fields})


def headline(row):
    m=row['metrics']
    rate='undefined' if m['net_win_rate'] is None else f"{m['net_win_rate']*100:.2f}%"
    return f"{row['candidate']} | {row['cost']} | {row['path']} | net={m['net_usdc']} | wins={m['wins']}/{m['closed_trades']} ({rate})"


def run(root, application, output):
    output.mkdir(parents=True,exist_ok=False)
    started=time.time_ns()//1000000
    plan=protocol()
    sources={p.name:__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
    write_json(output/'protocol.json',plan)
    write_json(output/'provenance.json',dict(protocol_sha256=digest(plan),research_sources=sources,
        original_engine_sha256=ENGINE_HASH,github_sha=os.environ.get('GITHUB_SHA'),
        github_run_id=os.environ.get('GITHUB_RUN_ID'),github_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),started_ms=started))
    rows=[]
    try:
        old,saved,audit=baseline_check(root,application)
        write_json(output/'baseline-audit.json',audit)
        print('BASELINE AUDIT: PASS; 5000 candles, 84 funding records, 192 old scenarios, 534 trade-scenario records.',flush=True)
        specs=sorted(strategies.candidates(),key=lambda s:(s['round'],s['id']))
        for spec in specs:
            for cost in COSTS:
                for path in PATHS:
                    row=evaluate(old,spec,cost,path,120,len(old['candles']),'DEVELOPMENT_SEEN')
                    if spec['family']=='control':
                        name=f"EMA{spec['fast']}_{spec['slow']}_both"
                        previous=[r for r in saved['results'] if r['window']=='full' and r['candidate']==name and r['cost']==cost and r['path']==path]
                        require(len(previous)==1,'Saved control unavailable')
                        require(row['trades']==previous[0]['trades'],'Control trade replay changed')
                        equal(row['model_net_pnl_usdc'],previous[0]['model_net_pnl_usdc'],'Control replay P&L')
                    rows.append(row)
                    write_json(output/'development.json',rows)
                    print(f"[{len(rows)}/56] DEVELOPMENT | "+headline(row),flush=True)
        selected=select(rows)
        selection=dict(strategy=selected,chosen_ms=time.time_ns()//1000000,development_dataset_sha256=OLD_DATA,
                       new_period_starts_ms=old['candles'][-1]['T']+1,
                       note='Development-selected exploratory candidate, not a certified profitable strategy. '
                            'This file is saved before any new data request.')
        write_json(output/'selection.json',selection)
        print('SELECTION SEALED BEFORE NEW DATA: '+selected['id'],flush=True)
        for spec in specs:
            cases=[r for r in rows if r['candidate']==spec['id']]
            observed=all(r['metrics']['observed_55'] for r in cases)
            print(f"DEVELOPMENT OBSERVED_55_ALL_CASES: {spec['id']}={observed}; NOT independent evidence",flush=True)
        client=PublicClient('mainnet',output/'fresh-raw')
        fresh=fetch_dataset(client)
        write_json(output/'fresh-dataset.json',fresh)
        write_json(output/'fresh-audit.json',raw_check(fresh,output/'fresh-raw'))
        overlap=overlap_check(old,fresh)
        write_json(output/'redownload-comparison.json',overlap)
        print('REDOWNLOAD OVERLAP: '+json.dumps(overlap),flush=True)
        bars={b['t']:b for b in old['candles']}
        bars.update({b['t']:b for b in fresh['candles']})
        rates={r['time']:r for r in old['funding']}
        rates.update({r['time']:r for r in fresh['funding']})
        merged=validate_dataset({**fresh,'candles':[bars[t] for t in sorted(bars)],
            'funding':[rates[t] for t in sorted(rates)],
            'source':'Original mainnet run plus fresh mainnet API snapshot; overlap compared',
            'provenance':'DIRECT_API_CONTIGUOUS_MERGE'})
        write_json(output/'evaluation-dataset.json',merged)
        cutoff=selection['new_period_starts_ms']
        lo=next((i for i,b in enumerate(merged['candles']) if b['t']>=cutoff),len(merged['candles']))
        hi=len(merged['candles'])
        if hi-lo<2:
            write_json(output/'status.json',dict(status='INSUFFICIENT_UNSEEN_DATA',development_scenarios=len(rows),new_bars=hi-lo))
            return 0
        print(f'NEW EVALUATION: {hi-lo} 1m bars after original cutoff. No new-period selection.',flush=True)
        frozen_evaluation=[selected]+[s for s in specs if s['family']=='control']
        newrows=[]
        for spec in frozen_evaluation:
            for cost in COSTS:
                for path in PATHS:
                    row=evaluate(merged,spec,cost,path,lo,hi,'NEW_PERIOD_EVALUATION')
                    newrows.append(row)
                    write_json(output/'evaluation-results.json',newrows)
                    print('NEW PERIOD | '+headline(row)+' | '+row['evidence_status'],flush=True)
        rows+=newrows
        primary=[r for r in newrows if r['candidate']==selected['id']]
        observed55=all(r['metrics']['observed_55'] for r in primary)
        observed60=all(r['metrics']['observed_60'] for r in primary)
        positive=all(D(r['model_net_pnl_usdc'])>0 for r in primary)
        negative=all(D(r['model_net_pnl_usdc'])<0 for r in primary)
        no_trades=all(not r['closed_trades'] for r in primary)
        enough=all(r['evidence_status']=='EXPLORATORY_ONLY' for r in primary)
        fixed_pass=all(r['fixed_trade_stress_metrics']['observed_55'] for r in primary if r['cost']=='base_assumptions')
        summary=dict(status='COMPLETED_BOUNDED_RESEARCH',development_rounds=2,development_scenarios=56,
            evaluation_scenarios=len(newrows),network='mainnet',new_bars=hi-lo,
            evaluation_start_ms=merged['candles'][lo]['t'],evaluation_end_ms=merged['candles'][-1]['T'],
            selected=selected['id'],historical_sign='NO_TRADES' if no_trades else 'POSITIVE' if positive else 'NEGATIVE' if negative else 'MIXED',
            observed_55_all_gated_cases=observed55,observed_60_all_gated_cases=observed60,
            fixed_trade_stress_55=fixed_pass,research_gate_55=observed55 and fixed_pass and enough,
            evidence='EXPLORATORY_ONLY' if enough else 'INSUFFICIENT_SAMPLE',
            stage60='REQUIRES_NEW_EVALUATION_NOT_REUSE_OF_THIS_TAIL',
            dataset_sha256=digest(merged),protocol_sha256=digest(plan),selection_sha256=digest(selection),
            original_data_audit=audit,overlap_audit=overlap,
            selected_results=[{k:v for k,v in r.items() if k not in ('trades','fixed_trade_stress_trades')} for r in primary],
            future_probability='NOT_ESTABLISHED',real_trades=0)
        write_json(output/'SUMMARY.json',summary)
        write_json(output/'all-results.json',rows)
        text=['# Issue #1 — bounded research result','',
              '**Historical sign: '+summary['historical_sign']+' | '+summary['evidence']+'**','',
              'Selected BEFORE new data: '+selected['id'],
              f'New evaluation: {hi-lo} one-minute bars. 56 development and {len(newrows)} new-period scenario runs.',
              'Observed >=55% in all gated cases: '+str(observed55),
              'Observed >=60% in all gated cases: '+str(observed60),
              'Research gate >=55% (including sample and fixed-trade stress): '+str(summary['research_gate_55']),
              'No future win-rate or income claim. No funds, accounts or orders.','',
              '## All results (net costs; development is NOT unseen evidence)','',
              '|Phase|Strategy|Cost|Path|Net USDC|Wins/trades|Win rate|','|---|---|---|---|---:|---:|---:|']
        for r in rows:
            m=r['metrics']; rate='undefined' if m['net_win_rate'] is None else f"{m['net_win_rate']*100:.2f}%"
            text.append(f"|{r['phase']}|{r['candidate']}|{r['cost']}|{r['path']}|{D(m['net_usdc']):.8f}|{m['wins']}/{m['closed_trades']}|{rate}|")
        text+=['','## Limitations']+['- '+x for x in plan['limitations']]
        (output/'REPORT.md').write_text('\n'.join(text)+'\n')
        write_json(output/'status.json',dict(status=summary['status'],historical_sign=summary['historical_sign'],evidence=summary['evidence']))
        print('FINAL: '+summary['historical_sign']+' | '+summary['evidence']+f' | research_gate_55={summary["research_gate_55"]}',flush=True)
        return 0
    except Exception as exc:
        write_json(output/'status.json',dict(status='FAILED_NO_VALIDATED_HEADLINE',error_type=type(exc).__name__,
                   error=str(exc),completed_scenarios=len(rows),historical_sign=None))
        print('FAILED_NO_VALIDATED_HEADLINE: '+str(exc),flush=True)
        raise
    finally:
        export(output,rows)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--application',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    sys.exit(run(a.baseline,a.application,a.out))
