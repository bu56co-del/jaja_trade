"""Exhaustive finite public-data batch: prepare, shard, and fail-closed aggregate."""
import argparse
import base64
from collections import Counter
from dataclasses import asdict
from decimal import Decimal as D
import gzip
import hashlib
import json
import os
from pathlib import Path
import time

from audit_data import PublicClient, fetch_dataset, write_json, digest
from paperlab.backtest import COSTS, PATHS, validate_dataset
from paperlab.common import Config,iso
import hybrids
from verify_mix import Reference,check_bank,audit as audit_mix
import run_mix
import run_books
import run as stats
import verify as v
from statistics_report import wilson
from statistics import NormalDist
import grid
import replay_grid

PREVIOUS_COMMIT='5d00dab2c1edacc26614a3fb04cb7bc19e36fcd1'
PREVIOUS_RUN='35493697852'
KNOWN_HASH='cf9e8c8245384caf3705c85dac0b4911b14d35070eaa57999fddaa0bc82e7980'
CUTOFF=1789885140000  # 2026-09-20 06:19 UTC
SHARDS=8


def hfile(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def b64(b):return base64.b64encode(b).decode()
def unb64(s):return base64.b64decode(s,validate=True)

def load(p):
    if str(p).endswith('.gz'):
        with gzip.open(p,'rt',encoding='utf-8') as f:return json.load(f)
    return v.load(p)

def compressed(p,value):
    with gzip.open(p,'wt',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))

def manifest(root):
    write_json(root/'manifest.json',{str(p.relative_to(root)):hfile(p) for p in sorted(root.rglob('*'))
                                    if p.is_file() and p.name!='manifest.json'})

def check_manifest(root):
    for name,sha in load(root/'manifest.json').items():
        p=root/name
        v.require(not Path(name).is_absolute() and '..' not in Path(name).parts,'Manifest traversal')
        v.require(not p.is_symlink() and hfile(p)==sha,'Evidence hash mismatch: '+name)

def identities():
    return dict(commit=os.environ.get('GITHUB_SHA'),run=os.environ.get('GITHUB_RUN_ID'),
                attempt=os.environ.get('GITHUB_RUN_ATTEMPT'))

def candidates():
    new=[s for s in grid.all_specs() if s['previously_tested_as'] is None]
    controls=[dict(id=s['id'],kind='control',control_spec=s,control_family='mixed') for s in hybrids.candidates()]
    controls += [dict(id=s['id'],kind='control',control_spec=s,control_family='single') for s in hybrids.controls()]
    return new+controls

def protocol():
    raw,canonical=grid.rules()
    return dict(version='exhaustive-finite-v1',network='mainnet',coin='ETH',interval='1m',
        previous_commit=PREVIOUS_COMMIT,previous_run=PREVIOUS_RUN,known_data=KNOWN_HASH,cutoff=CUTOFF,
        raw_aggregate_expressions=len(raw),canonical_aggregate_rules=len(canonical),
        universe_count=2424,previously_identical=4,new_candidates=2420,controls=14,total_candidates=2434,
        expected_scenario_rows=19472,shards=SHARDS,max_jobs=SHARDS+2,job_timeout_minutes=25,
        account=asdict(Config()),costs=COSTS,paths=PATHS,
        scope='All grammar-defined 2/3/4-family subsets; finite weights and thresholds; '
              'all ordered priorities/pairs/regime assignments. Not every possible trading strategy or real-valued parameter.',
        alias_rule='Truth-table-identical rules canonicalized before data. Full phase entry and long/short exit '
                   'streams may share one exact core replay; all candidate mappings retained, never counted as '
                   'independent trades or distinct core executions.',
        sample='All old prices are development only. New tail starts at cutoff. All candidates fixed before '
               'fetch. Exploratory multiple comparisons: no winner may be called independently validated.',
        statistics='Original net-profit/55/60 and 100 unique trades/three active folds gates retained. '
                   'Wilson and day block sensitivity; familywise Wilson uses 2434*4 new cells and IID only. '
                   'No correction turns short/serially correlated data into long-term proof.',
        no_fallback=True,real_orders=0,future_income='NOT_ESTABLISHED')

def prior_check(root,application):
    old,checks=run_mix.known_data(root/'previous',application)
    p=load(root/'mix/provenance.json')
    v.require(p['github_sha']==PREVIOUS_COMMIT and p['github_run_id']==PREVIOUS_RUN,'Wrong prior run')
    for name,h in p['sources'].items():
        v.require(Path(name).name==name,'Bad prior source path')
        for f in (root/'mix-source'/name,Path('mix_research')/name):
            v.require(hfile(f)==h,'Prior source mismatch')
    fresh=load(root/'mix/fresh-dataset.json')
    checks['prior_raw']=v.raw_check(fresh,root/'mix/fresh-raw')
    data=validate_dataset(load(root/'mix/evaluation-dataset.json'))
    v.require(digest(data)==KNOWN_HASH and data['candles'][-1]['T']+1==CUTOFF,'Wrong data anchor')
    merged,_=run_books.merge_data(old,fresh)
    v.require(merged['candles']==data['candles'] and merged['funding']==data['funding'],'Prior normalized merge mismatch')
    previous=load(root/'mix/all-results.json')
    v.require(len(previous)==112,'Prior grid incomplete')
    # Prior bank/reference audit is already recorded; repeat ledger arithmetic for each prior case.
    refs={ 'DEVELOPMENT_SEEN':Reference(old),'NEW_PERIOD_EVALUATION':Reference(data)}
    for r in previous:
        audit_mix(r,old if r['phase']=='DEVELOPMENT_SEEN' else data,r['strategy_spec'],refs[r['phase']])
    checks['prior_mix_scenarios']=112
    return data,checks

def make_groups(data,lo,hi,specs,phase):
    bank,ref=hybrids.Bank(data),Reference(data)
    checked=check_bank(bank,ref,hybrids.KINDS)
    groups={};maps={};duplicates=0
    for s in specs:
        if s['kind']=='control':
            key='CTRL_'+s['id'];groups[key]=dict(key=key,control=s,members=[s['id']],phase=phase,lo=lo,hi=hi)
            maps[s['id']]=key;continue
        e,x=grid.streams(bank.nodes,s,len(data['candles']))
        re,rx=grid.reference_streams(ref.nodes,s,len(data['candles']))
        v.require(e==re and x==rx,'Independent grid policy disagreement '+s['id'])
        e,x=e[lo:hi],x[lo:hi]
        raw=e+x;key='STREAM_'+hashlib.sha256(raw).hexdigest()
        if key in groups:
            v.require(groups[key]['entry']==b64(e) and groups[key]['exits']==b64(x),'Hash collision')
            groups[key]['members'].append(s['id']);duplicates+=1
        else:groups[key]=dict(key=key,entry=b64(e),exits=b64(x),members=[s['id']],phase=phase,lo=lo,hi=hi)
        maps[s['id']]=key
    checked.update(candidate_policies_checked=sum(s['kind']!='control' for s in specs),
        unique_execution_streams=len(groups)-14,control_groups=14,shared_candidate_mappings=duplicates)
    return list(groups.values()),maps,checked

def prepare(previous,application,out):
    out.mkdir(parents=True,exist_ok=False)
    plan=protocol();specs=candidates()
    write_json(out/'protocol.json',plan)
    write_json(out/'candidates.json',specs)
    write_json(out/'universe.json',grid.all_specs())
    raw,canonical=grid.rules();write_json(out/'truth-table-aliases.json',dict(raw=raw,canonical=canonical))
    frozen=dict(**identities(),frozen_ms=time.time_ns()//1000000,
                protocol_sha=digest(plan),candidates_sha=digest(specs),cutoff=CUTOFF,
                sources={p.name:hfile(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    write_json(out/'frozen.json',frozen)
    try:
        old,checked=prior_check(previous,application)
        write_json(out/'prior-audit.json',checked)
        write_json(out/'development-data.json',old)
        # No candidate performance is computed before this fixed download.
        fresh=fetch_dataset(PublicClient('mainnet',out/'raw'))
        write_json(out/'fresh-data.json',fresh)
        write_json(out/'fresh-audit.json',v.raw_check(fresh,out/'raw'))
        merged,overlap=run_books.merge_data(old,fresh)
        write_json(out/'evaluation-data.json',merged);write_json(out/'overlap.json',overlap)
        lo=next((i for i,b in enumerate(merged['candles']) if b['t']>=CUTOFF),len(merged['candles']))
        v.require(len(merged['candles'])-lo>=2,'INSUFFICIENT_UNSEEN_DATA')
        task=[];mapping={};audits={}
        for phase,data,a,b in [('DEVELOPMENT_SEEN',old,120,len(old['candles'])),
                               ('NEW_PERIOD_EVALUATION',merged,lo,len(merged['candles']))]:
            gs,mp,ck=make_groups(data,a,b,specs,phase)
            mapping[phase]=mp;audits[phase]=ck
            for g in gs:
                g['data_sha']=digest(data)
                for cost in COSTS:
                    for path in PATHS:
                        task.append(dict(**g,cost=cost,path=path))
            print(phase, 'policies',len(specs),'unique streams/controls',len(gs),flush=True)
        # Round-robin over longest window tasks first balances each shard deterministically.
        task.sort(key=lambda t:(-(t['hi']-t['lo']),t['key'],t['cost'],t['path']))
        for i,t in enumerate(task):
            t['task_id']=str(i);t['shard']=i%SHARDS
        compressed(out/'tasks.json.gz',task)
        write_json(out/'candidate-to-stream.json',mapping)
        write_json(out/'policy-audit.json',audits)
        write_json(out/'preparation.json',dict(**identities(),status='READY',
            core_replays_planned=len(task),scenario_rows_planned=len(specs)*8,
            new_bars=len(merged['candles'])-lo,window_start=iso(merged['candles'][lo]['t']),
            window_end=iso(merged['candles'][-1]['T']),data_hashes=dict(development=digest(old),evaluation=digest(merged))))
        print('READY:',len(task),'exact core replays for',len(specs)*8,'mapped scenario results',flush=True)
    except Exception as exc:
        write_json(out/'status.json',dict(**identities(),status='PREPARE_FAILED',error=str(exc)))
        raise
    finally:manifest(out)


def check_prepared(prepared):
    check_manifest(prepared)
    f=load(prepared/'frozen.json');p=load(prepared/'protocol.json')
    v.require(digest(p)==f['protocol_sha'] and digest(load(prepared/'candidates.json'))==f['candidates_sha'],'Protocol mismatch')
    v.require(f['sources']=={p.name:hfile(p) for p in sorted(Path(__file__).parent.glob('*.py'))},'Current source changed')
    # Local offline verification may not have GitHub env; Actions must match all three.
    for k,value in identities().items():
        if value is not None:v.require(f[k]==value,'Wrong current execution identity')


def worker(prepared,out,shard):
    v.require(0<=shard<SHARDS,'Invalid shard')
    out.mkdir(parents=True,exist_ok=False);check_prepared(prepared)
    tasks=[t for t in load(prepared/'tasks.json.gz') if t['shard']==shard]
    data={p:load(prepared/n) for p,n in [('DEVELOPMENT_SEEN','development-data.json'),('NEW_PERIOD_EVALUATION','evaluation-data.json')]}
    data_shas={p:digest(d) for p,d in data.items()}
    caches={p:replay_grid.Cache(d) for p,d in data.items()}
    banks={p:hybrids.Bank(d) for p,d in data.items()}
    refs={p:Reference(d) for p,d in data.items()}
    rows=[];start=time.monotonic()
    try:
        with gzip.open(out/'results.jsonl.gz','wt',encoding='utf-8') as f:
            for t in tasks:
                phase=t['phase'];d=data[phase];lo,hi=t['lo'],t['hi']
                v.require(data_shas[phase]==t['data_sha'],'Task data mismatch')
                if 'control' in t:
                    s=t['control']['control_spec']
                    r=hybrids.replay(d,lo,hi,s,t['cost'],t['path'],banks[phase])
                    ck=audit_mix(r,d,s,refs[phase])
                else:
                    entry,exits=unb64(t['entry']),unb64(t['exits'])
                    r=replay_grid.replay(caches[phase],lo,hi,entry,exits,t['cost'],t['path'])
                    ck=replay_grid.audit(r,d,lo,entry,exits)
                r['phase']=phase;r['independent_audit']=ck
                stats.add_statistics(r,d,lo,hi)
                m=r['metrics'];m['wilson_familywise95_iid']=wilson(m['wins'],m['closed_trades'],
                    NormalDist().inv_cdf(1-.05/(2*2434*4)))
                r.update(task_id=t['task_id'],stream_key=t['key'],members=t['members'],data_sha=t['data_sha'],
                         research_scope='EXPLORATORY_MULTIPLE_COMPARISONS_NOT_VALIDATED',**identities())
                f.write(json.dumps(r,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n');f.flush()
                rows.append(t['task_id'])
                if len(rows)%20==0:print('SHARD',shard,len(rows),'/',len(tasks),'elapsed',round(time.monotonic()-start),flush=True)
        write_json(out/'status.json',dict(**identities(),status='COMPLETE',shard=shard,completed=len(rows),
                                         task_ids=rows,expected=len(tasks),source='REAL_MAINNET_HISTORICAL_DATA'))
    except Exception as exc:
        write_json(out/'status.json',dict(**identities(),status='FAILED',shard=shard,completed=len(rows),error=str(exc)))
        raise
    finally:manifest(out)


def read_rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:yield json.loads(line)


def aggregate(prepared,results,out):
    import csv
    out.mkdir(parents=True,exist_ok=False)
    try:
        check_prepared(prepared)
        tasks={t['task_id']:t for t in load(prepared/'tasks.json.gz')}
        rows={}
        for i in range(SHARDS):
            root=results/f'shard-{i}';check_manifest(root)
            status=load(root/'status.json')
            v.require(status['status']=='COMPLETE' and status['shard']==i,'Incomplete shard')
            expected={k for k,t in tasks.items() if t['shard']==i}
            v.require(set(status['task_ids'])==expected and len(status['task_ids'])==len(expected),'Wrong shard tasks')
            found=set()
            for r in read_rows(root/'results.jsonl.gz'):
                key=r['task_id'];v.require(key in expected and key not in rows,'Duplicate/unknown result')
                t=tasks[key]
                for k in ('key','cost','path','phase','data_sha','members'):
                    actual=r['stream_key'] if k=='key' else r[k]
                    v.require(actual==t[k],'Result identity mismatch '+k)
                for k,value in identities().items():
                    if value is not None:v.require(r[k]==value,'Stale run result')
                v.require(r['independent_audit']['status']=='PASS_INDEPENDENT_MODEL_ARITHMETIC','Unaudited result')
                v.require(not r['pending_funding'] and not r['open_position_remaining'] and not r['integrity_warnings'],'Invalid model')
                rows[key]=r;found.add(key)
            v.require(found==expected,'Missing row in shard')
        v.require(rows.keys()==tasks.keys(),'Not all core replays completed')
        specs=load(prepared/'candidates.json');byid={s['id']:s for s in specs}
        groups={}
        for r in rows.values():
            for name in r['members']:
                v.require(name in byid,'Unknown candidate')
                groups.setdefault((r['phase'],name),[]).append(r)
        expected={(p,s['id']) for p in ('DEVELOPMENT_SEEN','NEW_PERIOD_EVALUATION') for s in specs}
        v.require(groups.keys()==expected,'Missing candidate coverage')
        scenario_count=0;summary_groups=[]
        with (out/'all-candidate-scenarios.csv').open('w',newline='',encoding='utf-8') as f:
            field=['phase','candidate','kind','cost','path','wins','trades','win_rate','net_usdc','return_pct',
                   'max_drawdown_pct','halt_reason','evidence','core_task_id','same_stream_candidates']
            w=csv.DictWriter(f,fieldnames=field);w.writeheader()
            for (phase,name),rs in sorted(groups.items()):
                v.require(len(rs)==4 and len({(r['cost'],r['path']) for r in rs})==4,'Incomplete candidate assumptions')
                a=run_books.aggregate(rs)
                summary_groups.append(dict(phase=phase,candidate=name,spec=byid[name],**a))
                for r in rs:
                    m=r['metrics'];w.writerow(dict(phase=phase,candidate=name,kind=byid[name]['kind'],cost=r['cost'],path=r['path'],
                        wins=m['wins'],trades=m['closed_trades'],win_rate=m['net_win_rate'],net_usdc=m['net_usdc'],return_pct=m['return_pct'],
                        max_drawdown_pct=r['model_max_drawdown_pct'],halt_reason=r['halt_reason'],evidence=r['evidence_status'],
                        core_task_id=r['task_id'],same_stream_candidates=len(r['members'])))
                    scenario_count+=1
        v.require(scenario_count==19472,'Scenario mapping incomplete')
        compressed(out/'candidate-summaries.json.gz',summary_groups)
        # Full ledgers stored once per verified stream; CSV maps EVERY candidate to it.
        with gzip.open(out/'core-ledgers.jsonl.gz','wt',encoding='utf-8') as f:
            for k in sorted(rows,key=int):f.write(json.dumps(rows[k],ensure_ascii=False,allow_nan=False)+'\n')
        prep=load(prepared/'preparation.json')
        counts={}
        for phase in ('DEVELOPMENT_SEEN','NEW_PERIOD_EVALUATION'):
            gs=[g for g in summary_groups if g['phase']==phase and g['spec']['kind']!='control']
            counts[phase]=dict(by_sign=dict(Counter(g['sign'] for g in gs)),
                observed55_all_cost_path=sum(g['observed_55_all_gated'] for g in gs),
                observed60_all_cost_path=sum(g['observed_60_all_gated'] for g in gs),
                research_gate55=sum(g['research_gate_55'] for g in gs),
                observed55_any_case=sum(any(c['metrics']['observed_55'] for c in g['case_metrics']) for g in gs),
                observed60_any_case=sum(any(c['metrics']['observed_60'] for c in g['case_metrics']) for g in gs),
                return5_any_case=sum(any(c['metrics']['return_ge_5pct'] for c in g['case_metrics']) for g in gs))
        new=[g for g in summary_groups if g['phase']=='NEW_PERIOD_EVALUATION' and g['spec']['kind']!='control']
        # Top table explicitly exploratory, not an untouched primary or deployment recommendation.
        def rank(g):
            base=[c for c in g['case_metrics'] if c['cost']=='base_assumptions']
            return min(D(c['metrics']['net_usdc']) for c in base)
        top=sorted(new,key=lambda g:(-rank(g),g['candidate']))[:10]
        summary=dict(**identities(),status='COMPLETE_FINITE_GRID',universe=2424,new_candidates=2420,controls=14,
            candidate_scenario_results=scenario_count,actual_core_replays=len(rows),
            shared_result_references=scenario_count-len(rows),all_shards=SHARDS,
            new_bars=prep['new_bars'],start=prep['window_start'],end=prep['window_end'],counts=counts,
            qualified55_ids=[g['candidate'] for g in new if g['research_gate_55']],
            next60='REQUIRES_NEW_UNSEEN_DATA_AFTER_55_DECISION',sample_warning='INSUFFICIENT_SAMPLE',
            statistically_validated_strategy=False,real_orders=0,
            top_exploratory_candidates=top,data_hashes=prep['data_hashes'],frozen_sha=hfile(prepared/'frozen.json'))
        write_json(out/'SUMMARY.json',summary)
        text=['# Finite exhaustive mixed-strategy results','',
              f"2420 new mixtures +14 controls; {scenario_count} mapped scenario results; {len(rows)} actual exact core replays.",
              'Shared identical decision streams are NOT independent experiments or extra trading samples.',
              f"New window: {prep['window_start']} — {prep['window_end']} ({prep['new_bars']} minutes).",'',
              '## New candidates, NEW data only',json.dumps(counts['NEW_PERIOD_EVALUATION'],indent=2),
              '', 'No statistically established future win rate or subscription income. All candidates are exploratory multiple comparisons.',
              '## Ten highest basic-cost nets (post-selection illustration, NOT independent validation)',
              '|Candidate|Base net range USDC|Base wins/trades|Across all four assumptions|',
              '|---|---|---|---|']
        for g in top:
            b=[c for c in g['case_metrics'] if c['cost']=='base_assumptions']
            text.append('|'+g['candidate']+'|'+' / '.join(c['metrics']['net_usdc'] for c in b)+'|'+
                ' / '.join(str(c['metrics']['wins'])+'/'+str(c['metrics']['closed_trades']) for c in b)+'|'+g['sign']+'|')
        text+=['','Full universe, aliases, raw market data and causal checks are in prepared artifact.',
               'All candidate scenarios map to core-ledgers; exact original engine, risk halts and costs retained.',
               'Missing L2, exact funding oracle, historical metadata, true intraminute paths and independent market source remain limitations.']
        (out/'REPORT.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
        print('\n'.join(text),flush=True)
        if os.environ.get('GITHUB_STEP_SUMMARY'):Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('\n'.join(text))
    except Exception as exc:
        write_json(out/'status.json',dict(**identities(),status='INCOMPLETE_NO_SUCCESS_CLAIM',error=str(exc)))
        raise
    finally:manifest(out)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['prepare','worker','aggregate'])
    p.add_argument('--previous',type=Path);p.add_argument('--application',type=Path)
    p.add_argument('--prepared',type=Path);p.add_argument('--results',type=Path)
    p.add_argument('--out',required=True,type=Path);p.add_argument('--shard',type=int)
    a=p.parse_args()
    if a.mode=='prepare':prepare(a.previous,a.application,a.out)
    elif a.mode=='worker':worker(a.prepared,a.out,a.shard)
    else:aggregate(a.prepared,a.results,a.out)
