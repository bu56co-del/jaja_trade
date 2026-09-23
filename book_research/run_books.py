"""One bounded, predeclared book-inspired experiment for Issue #1.

All known data is DEVELOPMENT_SEEN. Selection is saved before public data
requests. A new tail is never used to select a replacement winner.
"""
import argparse
from dataclasses import asdict
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
import time

from audit_data import PublicClient, fetch_dataset, write_json, digest
from paperlab.backtest import COSTS, PATHS, LIMITATIONS, validate_dataset
from paperlab.common import Config, iso
import run as existing
import verify as v
import book_strategies as books
from book_verify import audit

PREVIOUS_COMMIT = '7544e437bc78ee333c3404b0f49f25c70aa20df3'
PREVIOUS_RUN = '35455474414'
KNOWN_DATA_HASH = '40af9cec19da2e7ef7031d673e48469c06ab2b3350e016999374782542cd3717'
KNOWN_CUTOFF = 1789836000000  # 2026-09-19 16:40 UTC, all earlier results already seen
SOURCES = [
    dict(book='The New Trading for a Living', author='Alexander Elder',
         url='https://www.elder.com/product-page/the-new-trading-for-a-living', use='Risk discipline and journal; not a profitability claim.'),
    dict(book='Triple Screen public author explanation', author='Alexander Elder',
         url='https://www.spiketrade.com/shop/product/detail/R_PC44/', use='Multiple closed timeframes; exact numeric rules here are our adaptation.'),
    dict(book='Short Term Trading Strategies That Work', author='Larry Connors and Cesar Alvarez',
         url='https://store.tradingmarkets.com/products/short-term-trading-strategies-that-work-a-quantified-guide-to-trading-stocks-and-etfs-downloadable-pdf',
         use='Short-term mean reversion; NOT transfer of stock/ETF results to ETH.'),
    dict(book='The Improved R2 Strategy', author='Larry Connors',
         url='https://tradingmarkets.com/recent/the_improved_r2_strategy_84_correct_with_just_6_rules_-674361',
         use='Public research using RSI2 and long-trend context; this is not a reproduction of R2.'),
    dict(book='Locking in Gains in ETF PowerRatings Trades', author='David Penn / TradingMarkets',
         url='https://tradingmarkets.com/recent/locking_in_gains_in_etf_powerratings_trades_moving_averages_and_the_rsi-764360',
         use='Exit on strength: RSI2>70 or reclaiming an average. Adapted timeframe and risk limits.'),
    dict(book='Trading in the Zone', author='Mark Douglas',
         url='https://www.penguinrandomhouse.com/books/350665/trading-in-the-zone-by-mark-douglas/',
         use='Publisher description: probabilities/risk/consistency; protocol discipline, not a return signal.')]


def protocol():
    return dict(version='books-v1', coin='ETH', network='mainnet', interval='1m',
        previous_commit=PREVIOUS_COMMIT, previous_run=PREVIOUS_RUN,
        known_dataset=KNOWN_DATA_HASH, already_seen_cutoff_ms=KNOWN_CUTOFF,
        sources=SOURCES, source_scope='Public author/publisher pages, not complete copyrighted books.',
        candidates=books.candidates(), controls=books.controls(), account=asdict(Config()),
        costs=COSTS, paths=PATHS, new_candidate_count=8, development_scenarios=44,
        evaluation_scenarios=16, max_jobs=1, timeout_minutes=25,
        signal_history='Exactly 120 closed 1m bars. 5m groups require all five clock-aligned minutes. '
                       'Indicators are recalculated from that rolling window; RSI2 seeded by first two changes. '
                       'SMA60/100 are minutes, NOT the original stock strategy 200-day average.',
        signal_definitions='See book_research/README.md and source-linked book_strategies.py. '
                           'Books inspire hypotheses; entry thresholds/timeframes/shorts/stops are adaptations.',
        selection='Among 8 new candidates: maximize minimum net P&L over 4 gated cases and 2 base fixed-trade '
                  'stress cases; then maximum minimum gated net win rate (no trades=-1); ties lexicographic. '
                  'No-trade/negative choices remain exploratory, not qualified. Seal before any new download.',
        evaluation='Only bars >= already_seen_cutoff_ms, one new independent 10 USDC account per scenario. '
                   'No account resets within evaluation or its three reporting folds. '
                   'Only selected candidate plus 3 controls; never choose another on this tail.',
        psychology='No discretionary overrides, martingale, loss deletion, stop removal or resetting a halted account.',
        reporting='Observed 55/60 means net winning round trips / all closed round trips and positive net. '
                  'Undefined when no trades. +5% account return is a separate measure.',
        evidence='Existing >=100 nonduplicated trades per case and 3 reporting folds with trades retained. '
                 'Show Wilson95 IID and day-block sensitivity. No long-term probability guarantee. '
                 'An independent 60% stage requires subsequent unseen data after a 55% decision.',
        limitations=LIMITATIONS+['BOOK_INSPIRED_NOT_ORIGINAL_SYSTEM', 'NO_INDEPENDENT_SECOND_MARKET_SOURCE',
                    'SHORT_FRESH_TAIL', 'FIXED_COST_STRESS_DOES_NOT_RESIMULATE_ORDERS',
                    'PSYCHOLOGICAL_EFFECTS_NOT_EMPIRICALLY_TESTED'])


def merge_data(old, new):
    comparison=v.overlap_check(old,new)
    bars={b['t']:b for b in old['candles']}
    bars.update({b['t']:b for b in new['candles']})
    rates={r['time']:r for r in old['funding']}
    rates.update({r['time']:r for r in new['funding']})
    merged=validate_dataset({**new, 'candles':[bars[t] for t in sorted(bars)],
        'funding':[rates[t] for t in sorted(rates)],
        'source':'Known source-linked mainnet evidence plus fresh mainnet /info, overlap checked',
        'provenance':'DIRECT_API_CONTIGUOUS_MERGE'})
    return merged,comparison


def known_data(root, application):
    origin, original, checks=v.baseline_check(root/'baseline', application)
    prov=v.load(root/'research/provenance.json')
    v.require(prov['github_sha']==PREVIOUS_COMMIT and prov['github_run_id']==PREVIOUS_RUN,'Wrong predecessor run')
    for name,h in prov['research_sources'].items():
        v.require(Path(name).name==name,'Source path escape')
        for p in (root/'research-source'/name, Path(__file__).parents[1]/'research'/name):
            v.require(hashlib.sha256(p.read_bytes()).hexdigest()==h,'Predecessor source changed')
    old=v.load(root/'research/evaluation-dataset.json')
    v.require(digest(old)==KNOWN_DATA_HASH and old['candles'][-1]['T']+1==KNOWN_CUTOFF,'Known data/cutoff mismatch')
    priorfresh=v.load(root/'research/fresh-dataset.json')
    v.raw_check(priorfresh, root/'research/fresh-raw')
    reconstructed,_=merge_data(origin,priorfresh)
    v.require(reconstructed['candles']==old['candles'] and reconstructed['funding']==old['funding'], 'Known merge mismatch')
    previous=v.load(root/'research/all-results.json')
    v.require(len(previous)==68,'Predecessor experiment incomplete')
    for row in previous:
        data=origin if row['phase']=='DEVELOPMENT_SEEN' else old
        v.audit_row(row,data,row['strategy_spec'])
    checks['previous_research_scenarios']=len(previous)
    checks['known_candles']=len(old['candles'])
    checks['known_cutoff_utc']=iso(KNOWN_CUTOFF)
    return old,checks


def rank(rows, spec):
    cases=[r for r in rows if r['candidate']==spec['id']]
    v.require(len(cases)==4,'Missing candidate assumptions')
    net=[D(r['metrics']['net_usdc']) for r in cases]
    net += [D(r['fixed_trade_stress_metrics']['net_usdc']) for r in cases if r['cost']=='base_assumptions']
    rates=[-1 if r['metrics']['net_win_rate'] is None else r['metrics']['net_win_rate'] for r in cases]
    return min(net),min(rates)


def select(rows):
    return max(sorted(books.candidates(),key=lambda s:s['id']), key=lambda s:rank(rows,s))


def evaluate(data,lo,hi,spec,cost,path,phase):
    row=books.replay(data,lo,hi,spec,cost,path)
    row['phase']=phase
    row['independent_audit']=audit(row,data,spec)
    existing.add_statistics(row,data,lo,hi)
    return row


def sign(rows):
    if all(not r['closed_trades'] for r in rows):return 'NO_TRADES'
    vals=[D(r['model_net_pnl_usdc']) for r in rows]
    if all(x>0 for x in vals):return 'POSITIVE'
    if all(x<0 for x in vals):return 'NEGATIVE'
    if all(x==0 for x in vals):return 'FLAT'
    return 'MIXED'


def aggregate(rows):
    primary=rows
    fixed=[r['fixed_trade_stress_metrics'] for r in primary if r['cost']=='base_assumptions']
    enough=all(r['evidence_status']=='EXPLORATORY_ONLY' for r in primary)
    return dict(sign=sign(rows), net_range=[str(min(D(r['model_net_pnl_usdc']) for r in rows)),
                                         str(max(D(r['model_net_pnl_usdc']) for r in rows))],
                observed_55_all_gated=all(r['metrics']['observed_55'] for r in primary),
                observed_60_all_gated=all(r['metrics']['observed_60'] for r in primary),
                fixed_trade_stress_55=all(m['observed_55'] for m in fixed),
                research_gate_55=enough and all(r['metrics']['observed_55'] for r in primary) and all(m['observed_55'] for m in fixed),
                evidence='EXPLORATORY_ONLY' if enough else 'INSUFFICIENT_SAMPLE',
                case_metrics=[dict(cost=r['cost'],path=r['path'],metrics=r['metrics'],
                    fixed_trade_stress=r.get('fixed_trade_stress_metrics'),drawdown_pct=r['model_max_drawdown_pct'],
                    halt_reason=r['halt_reason'],decision_counts=r.get('decision_counts')) for r in rows])


def finish(output, summary, rows):
    write_json(output/'SUMMARY.json',summary)
    write_json(output/'all-results.json',rows)
    existing.export(output,rows)
    text=['# Book-inspired research result','',f"**{summary['historical_sign']} | {summary['evidence']}**",'',
          'Selected BEFORE new data: '+summary['selected'],
          'New evaluation: '+str(summary['new_bars'])+' one-minute bars; '+summary['start_utc']+' to '+summary['end_utc'],
          '55% research gate: '+str(summary['primary']['research_gate_55']),
          'No real orders; no established future win rate. Daily-stock methods are adapted, NOT reproduced.','',
          '|Phase|Candidate|Cost|Path|Net USDC|Wins/closed|Net win rate|','|---|---|---|---|---:|---:|---:|']
    for row in rows:
        m=row['metrics']; rate='undefined' if m['net_win_rate'] is None else f"{100*m['net_win_rate']:.2f}%"
        text.append(f"|{row['phase']}|{row['candidate']}|{row['cost']}|{row['path']}|{D(m['net_usdc']):.8f}|{m['wins']}/{m['closed_trades']}|{rate}|")
    text+=['','All development results were seen; no post-hoc claim of out-of-sample validation.',
           'New tail is not an account carried forward from the old experiment; no resets occur within this tail.',
           'Modelled L2/spread/slippage/intrabar paths and funding-price proxy remain. No independent second source.']
    (output/'REPORT.md').write_text('\n'.join(text)+'\n',encoding='utf-8')


def run(previous, application, output):
    output.mkdir(parents=True,exist_ok=False)
    plan=protocol()
    write_json(output/'protocol.json',plan)
    src={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
    provenance=dict(github_sha=os.environ.get('GITHUB_SHA'),github_run_id=os.environ.get('GITHUB_RUN_ID'),
                    github_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),
                    protocol_sha256=digest(plan),sources=src,started_ms=time.time_ns()//1000000)
    write_json(output/'provenance.json',provenance)
    rows=[]
    try:
        old,checks=known_data(previous,application)
        write_json(output/'prior-audit.json',checks)
        print('KNOWN DATA / ORIGINAL AND PRIOR LEDGERS: PASS',flush=True)
        specs=books.candidates()+books.controls()
        for spec in specs:
            for cost in COSTS:
                for path in PATHS:
                    row=evaluate(old,120,len(old['candles']),spec,cost,path,'DEVELOPMENT_SEEN')
                    rows.append(row)
                    write_json(output/'development.json',rows)
                    print(f'[{len(rows)}/44] '+existing.headline(row),flush=True)
        selected=select(rows)
        choice=dict(strategy=selected,chosen_ms=time.time_ns()//1000000,known_cutoff_ms=KNOWN_CUTOFF,
                    development_dataset_sha256=digest(old),protocol_sha256=digest(plan),
                    development_qualified=aggregate([r for r in rows if r['candidate']==selected['id']])['observed_55_all_gated'])
        write_json(output/'selection.json',choice)
        print('SELECTION SEALED BEFORE FETCH: '+selected['id'],flush=True)
        fresh=fetch_dataset(PublicClient('mainnet',output/'fresh-raw'))
        write_json(output/'fresh-dataset.json',fresh)
        write_json(output/'fresh-audit.json',v.raw_check(fresh,output/'fresh-raw'))
        merged,overlap=merge_data(old,fresh)
        write_json(output/'redownload-comparison.json',overlap)
        write_json(output/'evaluation-dataset.json',merged)
        lo=next((i for i,b in enumerate(merged['candles']) if b['t']>=KNOWN_CUTOFF),len(merged['candles']))
        hi=len(merged['candles'])
        if hi-lo<2:
            write_json(output/'status.json',dict(status='INSUFFICIENT_UNSEEN_DATA',development_scenarios=len(rows),new_bars=hi-lo))
            return 0
        print('NEW TAIL: '+str(hi-lo)+' bars; starts '+iso(merged['candles'][lo]['t']),flush=True)
        new=[]
        for spec in [selected]+books.controls():
            for cost in COSTS:
                for path in PATHS:
                    row=evaluate(merged,lo,hi,spec,cost,path,'NEW_PERIOD_EVALUATION')
                    new.append(row)
                    write_json(output/'evaluation-results.json',new)
                    print('NEW | '+existing.headline(row)+' | '+row['evidence_status'],flush=True)
        rows+=new
        primary=aggregate([r for r in new if r['candidate']==selected['id']])
        summary=dict(status='COMPLETED_BOOK_INSPIRED_RESEARCH',selected=selected['id'],network='mainnet',
                     historical_sign=primary['sign'],evidence=primary['evidence'],primary=primary,
                     development_scenarios=44,evaluation_scenarios=16,new_bars=hi-lo,
                     start_utc=iso(merged['candles'][lo]['t']),end_utc=iso(merged['candles'][hi-1]['T']),
                     known_cutoff_ms=KNOWN_CUTOFF,selection_sha256=digest(choice),dataset_sha256=digest(merged),
                     protocol_sha256=digest(plan),provenance=provenance,overlap=overlap,
                     development_candidates={s['id']:aggregate([r for r in rows if r['phase']=='DEVELOPMENT_SEEN' and r['candidate']==s['id']]) for s in specs},
                     stage60='NEEDS_NEW_DATA_AFTER_55_DECISION',future_probability='NOT_ESTABLISHED',real_orders=0)
        finish(output,summary,rows)
        write_json(output/'status.json',dict(status=summary['status'],historical_sign=summary['historical_sign'],evidence=summary['evidence']))
        print('FINAL: '+summary['historical_sign']+' | '+summary['evidence']+' | research_gate_55='+str(primary['research_gate_55']),flush=True)
        print('SUMMARY_JSON '+json.dumps(summary,ensure_ascii=False),flush=True)
        return 0
    except Exception as exc:
        write_json(output/'status.json',dict(status='FAILED_NO_VALIDATED_RESULT',error=str(exc),completed_scenarios=len(rows)))
        print('FAILED_NO_VALIDATED_RESULT: '+str(exc),flush=True)
        return 2
    finally:
        manifest={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(output.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'}
        write_json(output/'evidence-manifest.json',manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous',type=Path,required=True)
    parser.add_argument('--application',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(run(args.previous,args.application,args.out))
