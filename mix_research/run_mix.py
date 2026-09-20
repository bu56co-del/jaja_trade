"""One bounded hybrid experiment; all candidates frozen before new data."""
import argparse
from dataclasses import asdict
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
from statistics import NormalDist
import time

from audit_data import PublicClient, fetch_dataset, write_json, digest
from paperlab.backtest import COSTS, PATHS, LIMITATIONS, validate_dataset
from paperlab.common import Config, iso
import run as statistics_runner
import verify as v
import run_books as predecessor
from book_verify import audit as audit_book
import hybrids
from verify_mix import Reference, check_bank, audit

PREVIOUS_RUN='35481147529'
PREVIOUS_COMMIT='7b9d97ca7e4f6dc97bec2876ba3633ff837cb431'
KNOWN_HASH='f68b63107e632e64c252e8df9bb050b911c12a702b6e8e111e82b56ef9572502'
KNOWN_CUTOFF=1789867500000  # 2026-09-20 01:25 UTC, last previously seen candle end+1


def protocol():
    return dict(version='hybrids-v1',network='mainnet',coin='ETH',interval='1m',
        previous_run=PREVIOUS_RUN,previous_commit=PREVIOUS_COMMIT,known_dataset=KNOWN_HASH,
        known_cutoff_ms=KNOWN_CUTOFF,primary=hybrids.PRIMARY,
        candidates=hybrids.candidates(),controls=hybrids.controls(),
        account=asdict(Config()),costs=COSTS,paths=PATHS,
        development_cases=56,evaluation_cases=56,max_jobs=1,timeout_minutes=25,
        design='Eight fixed combinations plus six single-strategy controls. No fitting/forced winner. '
               'Each case has ONE 10 USDC account and one position, not one account per component.',
        freshness='120 closed bars per indicator, clock-aligned complete 5m groups. Votes retain the latest '
                  'nonzero event per family from current/prior two closed bars, at most 120000ms old. '
                  'Any opposing vote vetoes. Persistent vote consensus is edge-triggered.',
        evaluation='All candidates frozen before any fresh API request. Only tail beyond known_cutoff_ms is '
                   'new. All alternatives disclosed as multiple-comparison exploration, not a selected winner. '
                   'Old development/evaluation accounts are independent, never joined into a profit track record.',
        sources=['https://scholarworks.wmich.edu/math_pubs/42/',
                 'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint'],
        attribution='Original book adaptations retained as controls; combinations are our hypotheses, not book claims.',
        evidence='Net win rate, net profit, return/+5% separate. Research gate needs >=100 distinct trades '
                 'per case and 3 reporting folds with trades, plus positive net and >=55% in each gated '
                 'case and same-trade cost stress. Wilson IID/day block assumptions retained. '
                 'Bonferroni Wilson interval across 14*4 cells is supplementary, not time-dependence correction.',
        limits=LIMITATIONS+['SIGNAL_VOTES_ARE_NOT_INDEPENDENT_EVIDENCE','ALL_14_FROZEN_MODELS_DISCLOSED',
                            'NO_SECOND_MARKET_SOURCE','ONLY_NEW_TAIL_IS_UNSEEN',
                            'NO_UNLIMITED_OPTIMIZATION','NO_FUTURE_INCOME_CLAIM'])


def known_data(root,application):
    origin,checks=predecessor.known_data(root/'previous',application)
    prov=v.load(root/'books/provenance.json')
    v.require(prov['github_sha']==PREVIOUS_COMMIT and prov['github_run_id']==PREVIOUS_RUN,'Wrong prior book run')
    for name,h in prov['sources'].items():
        v.require(Path(name).name==name,'Source traversal')
        for p in (root/'book-source'/name,Path(__file__).parents[1]/'book_research'/name):
            v.require(hashlib.sha256(p.read_bytes()).hexdigest()==h,'Prior book source changed')
    data=validate_dataset(v.load(root/'books/evaluation-dataset.json'))
    v.require(digest(data)==KNOWN_HASH and data['candles'][-1]['T']+1==KNOWN_CUTOFF,'Prior data anchor mismatch')
    fresh=v.load(root/'books/fresh-dataset.json')
    checks['books_raw']=v.raw_check(fresh,root/'books/fresh-raw')
    reconstructed,_=predecessor.merge_data(origin,fresh)
    v.require(reconstructed['candles']==data['candles'] and reconstructed['funding']==data['funding'],'Prior merge mismatch')
    priorrows=v.load(root/'books/all-results.json')
    v.require(len(priorrows)==60,'Prior book scenarios incomplete')
    for r in priorrows:
        audit_book(r,origin if r['phase']=='DEVELOPMENT_SEEN' else data,r['strategy_spec'])
    checks['book_scenarios']=60
    return data,checks


def evaluate(data,lo,hi,spec,cost,path,phase,bank,reference):
    row=hybrids.replay(data,lo,hi,spec,cost,path,bank)
    row['phase']=phase
    row['independent_audit']=audit(row,data,spec,reference)
    statistics_runner.add_statistics(row,data,lo,hi)
    from statistics_report import wilson
    m=row['metrics']
    m['wilson_familywise95_iid_56_cells']=wilson(m['wins'],m['closed_trades'],
                                                NormalDist().inv_cdf(1-.05/(2*56)))
    m['familywise_note']='56 candidate/cost/path cells; IID only, correlated trades and repeated research remain limitations.'
    return row


def aggregate(rows):
    v.require(len(rows)==4 and len({(r['cost'],r['path']) for r in rows})==4,'Incomplete scenario aggregate')
    return predecessor.aggregate(rows)


def report(output,rows,summary):
    write_json(output/'SUMMARY.json',summary)
    write_json(output/'all-results.json',rows)
    statistics_runner.export(output,rows)
    text=['# Mixed-strategy research','',
          f"**Primary {summary['primary_id']}: {summary['primary']['sign']} | {summary['primary']['evidence']}**",'',
          f"New tail: {summary['new_bars']} minutes, {summary['start_utc']} to {summary['end_utc']}",
          'All 8 hybrids and 6 controls frozen BEFORE fetch. No automatic winner or deployment.',
          'Net winning trades / all closed round trips; zero trades has no win rate. One 10 USDC account per case.',
          f"55% research gate candidates: {summary['qualified_55']}",
          'Confidence intervals and fixed-trade stress are in all-results.json. Prior results are development only.','',
          '|Phase|Strategy|Cost|Path|Net USDC|Wins/trades|Win rate|',
          '|---|---|---|---|---:|---:|---:|']
    for r in rows:
        m=r['metrics'];rate='undefined' if m['net_win_rate'] is None else f"{m['net_win_rate']*100:.2f}%"
        text.append(f"|{r['phase']}|{r['candidate']}|{r['cost']}|{r['path']}|{D(m['net_usdc']):.8f}|{m['wins']}/{m['closed_trades']}|{rate}|")
    text+=['','Mixture weights/thresholds are hypotheses, not independently validated market edges.',
           'Modelled spreads, depth, path, funding-price proxy and short sample remain. No live orders.',
           'No 60% independent confirmation without a further unseen dataset after the 55% decision.']
    (output/'REPORT.md').write_text('\n'.join(text)+'\n',encoding='utf-8')


def run(previous,application,output):
    output.mkdir(parents=True,exist_ok=False)
    plan=protocol();write_json(output/'protocol.json',plan)
    sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))}
    prov=dict(github_sha=os.environ.get('GITHUB_SHA'),github_run_id=os.environ.get('GITHUB_RUN_ID'),
              github_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),sources=sources,
              protocol_sha256=digest(plan),started_ms=time.time_ns()//1000000)
    write_json(output/'provenance.json',prov)
    frozen=dict(primary=hybrids.PRIMARY,all_specs=plan['candidates']+plan['controls'],
                cutoff_ms=KNOWN_CUTOFF,frozen_ms=time.time_ns()//1000000,protocol_sha256=digest(plan))
    write_json(output/'frozen.json',frozen)
    rows=[]
    try:
        old,checks=known_data(previous,application)
        write_json(output/'prior-audit.json',checks)
        bank,ref=hybrids.Bank(old),Reference(old)
        write_json(output/'development-features-audit.json',check_bank(bank,ref,hybrids.KINDS))
        print('PRIOR DATA, LEDGERS AND INDEPENDENT HYBRID DECISIONS: PASS',flush=True)
        for spec in frozen['all_specs']:
            for cost in COSTS:
                for path in PATHS:
                    r=evaluate(old,120,len(old['candles']),spec,cost,path,'DEVELOPMENT_SEEN',bank,ref)
                    rows.append(r);write_json(output/'development.json',rows)
                    print(f'[{len(rows)}/56] DEV | '+statistics_runner.headline(r),flush=True)
        v.require(frozen['all_specs']==hybrids.candidates()+hybrids.controls(),'Frozen candidates changed')
        fresh=fetch_dataset(PublicClient('mainnet',output/'fresh-raw'))
        write_json(output/'fresh-dataset.json',fresh)
        write_json(output/'fresh-audit.json',v.raw_check(fresh,output/'fresh-raw'))
        merged,overlap=predecessor.merge_data(old,fresh)
        write_json(output/'evaluation-dataset.json',merged)
        write_json(output/'redownload-comparison.json',overlap)
        lo=next((i for i,b in enumerate(merged['candles']) if b['t']>=KNOWN_CUTOFF),len(merged['candles']))
        hi=len(merged['candles'])
        if hi-lo<2:
            write_json(output/'status.json',dict(status='INSUFFICIENT_UNSEEN_DATA',completed_cases=len(rows),new_bars=hi-lo))
            return 0
        bank,ref=hybrids.Bank(merged),Reference(merged)
        write_json(output/'evaluation-features-audit.json',check_bank(bank,ref,hybrids.KINDS))
        new=[]
        for spec in frozen['all_specs']:
            for cost in COSTS:
                for path in PATHS:
                    r=evaluate(merged,lo,hi,spec,cost,path,'NEW_PERIOD_EVALUATION',bank,ref)
                    new.append(r);write_json(output/'evaluation-results.json',new)
                    print(f'[{len(new)}/56] NEW | '+statistics_runner.headline(r),flush=True)
        rows+=new
        allgroups={s['id']:aggregate([r for r in new if r['candidate']==s['id']]) for s in frozen['all_specs']}
        qualified=[s['id'] for s in hybrids.candidates() if allgroups[s['id']]['research_gate_55']]
        summary=dict(status='COMPLETED_MIXED_RESEARCH',network='mainnet',primary_id=hybrids.PRIMARY,
            primary=allgroups[hybrids.PRIMARY],evaluation_candidates=allgroups,
            development_candidates={s['id']:aggregate([r for r in rows[:56] if r['candidate']==s['id']]) for s in frozen['all_specs']},
            qualified_55=qualified,new_bars=hi-lo,start_utc=iso(merged['candles'][lo]['t']),end_utc=iso(merged['candles'][-1]['T']),
            development_cases=56,evaluation_cases=len(new),dataset_sha256=digest(merged),frozen_sha256=digest(frozen),
            protocol_sha256=digest(plan),overlap=overlap,provenance=prov,
            outcome='OBSERVED_THRESHOLD_REQUIRES_FURTHER_VALIDATION' if qualified else 'NO_QUALIFYING_STRATEGY',
            next60='REQUIRES_SUBSEQUENT_UNSEEN_DATA',future_win_rate='NOT_ESTABLISHED',real_orders=0)
        report(output,rows,summary)
        write_json(output/'status.json',dict(status=summary['status'],outcome=summary['outcome'],primary_sign=summary['primary']['sign']))
        print('FINAL: '+summary['outcome']+' | PRIMARY '+summary['primary']['sign']+' | '+summary['primary']['evidence'],flush=True)
        print('EVALUATION_COMPARISON '+json.dumps({k:dict(sign=x['sign'],net_range=x['net_range'],observed55=x['observed_55_all_gated'],gate55=x['research_gate_55']) for k,x in allgroups.items()}),flush=True)
        return 0
    except Exception as exc:
        write_json(output/'status.json',dict(status='FAILED_NO_VALIDATED_RESULT',error=str(exc),completed_cases=len(rows)))
        print('FAILED_NO_VALIDATED_RESULT '+str(exc),flush=True)
        return 2
    finally:
        write_json(output/'evidence-manifest.json',{str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob('*')) if p.is_file() and p.name!='evidence-manifest.json'})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--previous',required=True,type=Path)
    p.add_argument('--application',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    raise SystemExit(run(a.previous,a.application,a.out))
