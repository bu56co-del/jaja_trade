"""Freeze, download once, compare native timeframes, retain every failed experiment."""
import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import time

from audit_data import write_json, strict_json, digest
from paperlab.backtest import COSTS, PATHS
from paperlab.common import Config
from dataclasses import asdict
from decimal import Decimal as D
from market_data import collect, ms, iso, END, STARTS, INTERVALS, need
from model import SPECS, PRIMARY, windows, features, replay
from verify_long import check_features, audit

ENGINE_HASH='abe98cf655254613ba9462049122579e1fb26aa3343c844278f129acaa88aba1'
KNOWN_START=ms('2026-09-16T04:21:00+00:00')
KNOWN_END=ms('2026-09-20T07:29:00+00:00')

def load(path):
    path=Path(path);need(not path.is_symlink() and path.stat().st_size<50_000_000,'Unsafe/oversized file')
    return strict_json(path.read_bytes())

def provenance():
    return {k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}

def source_manifest():
    root=Path(__file__).resolve().parents[1];files={}
    for folder in ('application','longer'):
        for p in sorted((root/folder).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.json','.md'):
                files[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
    need(files['application/paperlab/engine.py']==ENGINE_HASH,'Original engine changed')
    return files

def protocol():
    return {'version':'calendar-timeframe-ablation-v1','network':'mainnet','coin':'ETH',
        'calendar_end_exclusive':END,'starts':STARTS,'warmup_bars':120,'specs':SPECS,
        'primary':PRIMARY,'costs':COSTS,'paths':PATHS,'account':asdict(Config()),
        'windows':{tf:windows(tf) for tf in INTERVALS},'expected_cases':264,
        'selection':'No tuning or winner selection. Primary is fixed before downloading any data.',
        'epistemic_status':'RETROSPECTIVE_EXPLORATORY_PARTLY_PREVIOUSLY_SEEN; not forward or unseen final confirmation.',
        'seen_period_ms':[KNOWN_START,KNOWN_END],
        'period_accounts':'Full account is never reset. Calendar cohorts each start independently with 10 USDC; do not concatenate returns.',
        'timing':'Native bar observations: open at +2s, high/low at 35% and 2/3 duration, close at duration-2ms. Both OHLC/OLHC. Not historical L2.',
        'stop_and_cost':'All original entry risk, stop, target and cost gates retained. Confirmation exit never blocks stop/target/account halt.',
        'cadence':'Only simulation sampling gap tolerance scales for 5m/15m. Missing bars fail at admission. Original live runner is unchanged.',
        'limits':['CURRENT_METADATA_NOT_HISTORICAL','FUNDING_PRECEDING_CLOSE_PROXY_NOT_ORACLE','SPREAD_DEPTH_SLIPPAGE_ASSUMPTIONS',
                  'COARSE_INTRABAR_PATH_NOT_EXCHANGE_EXECUTION','RELATED_TESTS_NOT_INDEPENDENT','NO_FUTURE_WIN_RATE_CLAIM'],
        'sources':['https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint',
                   'https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals',
                   'https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding',
                   'https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees']}

def manifest(root):
    write_json(root/'manifest.json',{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob('*')) if p.is_file() and p.name!='manifest.json'})

def prepare(out):
    out.mkdir(parents=True,exist_ok=False)
    plan=protocol();sources=source_manifest()
    frozen={'protocol':plan,'protocol_sha256':digest(plan),'sources':sources,'sources_sha256':digest(sources),
            'provenance':provenance(),'frozen_ms':time.time_ns()//1000000}
    write_json(out/'frozen.json',frozen)
    try:
        data,checks=collect(out/'market')
        raw={tf:{'bars':len(d['candles']),'funding':len(d['funding']),'first':iso(d['candles'][0]['t']),
                 'last':iso(d['candles'][-1]['T']),'sha256':digest(d)} for tf,d in data.items()}
        write_json(out/'data-inventory.json',raw)
        ready={'status':'READY','frozen_sha256':digest(frozen),'data_sha256':digest(data),'inventory':raw,'checks':checks,'provenance':provenance()}
        write_json(out/'ready.json',ready)
        print('PUBLIC_DATA_READY '+json.dumps(raw),flush=True)
        return 0
    except Exception as exc:
        write_json(out/'status.json',{'status':'DATA_UNAVAILABLE_OR_INVALID','error':str(exc),'provenance':provenance()})
        print('NO_VALIDATED_RESULT '+str(exc),flush=True);return 2
    finally:manifest(out)

def prepared(root):
    frozen,ready=load(root/'frozen.json'),load(root/'ready.json')
    need(ready['status']=='READY' and ready['frozen_sha256']==digest(frozen),'Preparation incomplete')
    need(frozen['sources']==source_manifest(),'Code changed after freeze')
    need(frozen['provenance']==provenance()==ready['provenance'],'Wrong run/commit/attempt')
    need(frozen['protocol_sha256']==digest(protocol()),'Protocol changed after freeze')
    data=load(root/'market/data.json');need(digest(data)==ready['data_sha256'],'Changed dataset')
    return frozen,data

def compute(root,out,tf):
    out.mkdir(parents=True,exist_ok=False);rows=[]
    try:
        frozen,all_data=prepared(root);data=all_data[tf];fv=features(data['candles']);reference=check_features(data,fv)
        write_json(out/'causal-features-check.json',{'status':'PASS_INDEPENDENT_REFERENCE','timeframe':tf,'bars':len(reference)})
        for name,start,end in windows(tf):
            lo=(start-data['candles'][0]['t'])//INTERVALS[tf];hi=(end-data['candles'][0]['t'])//INTERVALS[tf]
            need(data['candles'][lo]['t']==start and data['candles'][hi-1]['T']==end-1,'Calendar boundaries mismatch')
            for spec in SPECS:
                for cost in COSTS:
                    for path in PATHS:
                        r=replay(data,lo,hi,spec,cost,path,fv);r['window']=name
                        r['independent_audit']=audit(r,data,reference)
                        r['previously_seen_overlap_ms']=max(0,min(end,KNOWN_END)-max(start,KNOWN_START))
                        r['dataset_sha256']=digest(data);rows.append(r)
                        # Checkpoint after each case, never use an old success file on failure.
                        write_json(out/'results.json',rows)
                        m=r['metrics'];print(f"{tf} {name} {spec['id']} {cost} {path}: net={m['net_usdc']} wins={m['wins']}/{m['trades']} halt={r['halt_reason']}",flush=True)
        status={'status':'COMPLETED','interval':tf,'cases':len(rows),'data_sha256':digest(data),'results_sha256':digest(rows),
                'frozen_sha256':digest(frozen),'provenance':provenance()}
        write_json(out/'status.json',status);return 0
    except Exception as exc:
        write_json(out/'status.json',{'status':'FAILED_NO_VALIDATED_RESULT','error':str(exc),'completed_cases':len(rows),'provenance':provenance()})
        print('FAILED_NO_VALIDATED_RESULT '+str(exc),flush=True);return 2
    finally:manifest(out)

def classify(rows):
    need(len(rows)==4,'Four cost/path cases required')
    m=[r['metrics'] for r in rows];p=[D(x['net_usdc']) for x in m]
    if all(x['trades']==0 for x in m):return 'NO_TRADES'
    if all(x>0 for x in p):return 'POSITIVE'
    if all(x<0 for x in p):return 'NEGATIVE'
    if all(x<=0 for x in p) and any(x<0 for x in p):return 'LOSS_AND_NO_TRADES_OR_FLAT'
    if all(x>=0 for x in p) and any(x>0 for x in p):return 'PROFIT_AND_NO_TRADES_OR_FLAT'
    return 'MIXED'

def aggregate(root,results,out):
    out.mkdir(parents=True,exist_ok=False)
    try:
        frozen,data=prepared(root);rows=[]
        for tf in INTERVALS:
            folder=results/tf;st=load(folder/'status.json');part=load(folder/'results.json')
            need(st['status']=='COMPLETED' and st['interval']==tf and st['provenance']==provenance(),'Missing/failed/mismatched shard')
            need(st['frozen_sha256']==digest(frozen) and st['results_sha256']==digest(part) and st['data_sha256']==digest(data[tf]),'Shard hash mismatch')
            rows+=part
        expected={(tf,w,s['id'],c,p) for tf in INTERVALS for w,_,_ in windows(tf) for s in SPECS for c in COSTS for p in PATHS}
        found=[(r['interval'],r['window'],r['candidate'],r['cost'],r['path']) for r in rows]
        need(len(rows)==264==len(expected) and len(set(found))==264 and set(found)==expected,'Missing or duplicate scenarios')
        # Check scalar totals again without treating a green workflow as profitability.
        for r in rows:
            total=sum((D(t['gross_pnl'])-D(t['entry_fee'])-
                D(t['exit_fee'])+sum((D(f['amount']) for f in t['funding_events']),D(0)) for t in r['trades']),D(0))
            need(abs(total-D(r['metrics']['net_usdc']))<D('1e-16'),'Aggregation net mismatch')
        primary=[r for r in rows if (r['interval'],r['candidate'],r['window'])==PRIMARY]
        cohorts=[r for r in rows if r['interval']=='5m' and r['candidate']==PRIMARY[1] and r['window'].startswith('calendar_')]
        summary={'status':'COMPLETED_EXPLORATORY_ABLATION','provenance':provenance(),'cases':len(rows),'primary':PRIMARY,
            'primary_sign':classify(primary),'primary_metrics':[{'cost':r['cost'],'path':r['path'],'metrics':r['metrics'],'halt':r['halt_reason'],
                 'halted_fraction':r['halted_fraction'],'max_drawdown_pct':r['sampled_max_drawdown_pct']} for r in primary],
            'observed55_primary_all_cases':all(r['metrics']['observed55_and_positive'] for r in primary),
            'observed60_primary_all_cases':all(r['metrics']['observed60_and_positive'] for r in primary),
            'primary_full_cases_with_100_trades':sum(r['metrics']['trades']>=100 for r in primary),
            'primary_calendar_cases_with_positive_net':sum(D(r['metrics']['net_usdc'])>0 for r in cohorts),
            'research_gate55':'NOT_ESTABLISHED_RETROSPECTIVE_AND_MULTIPLE_COMPARISONS',
            'data_inventory':load(root/'data-inventory.json'),'checks':load(root/'market/checks.json'),
            'frozen_sha256':digest(frozen),'future_income':'NOT_ESTABLISHED','real_orders':0}
        write_json(out/'SUMMARY.json',summary)
        with gzip.open(out/'all-results.json.gz','wt',encoding='utf-8') as f:json.dump(rows,f,ensure_ascii=False,allow_nan=False)
        columns=['interval','window','candidate','cost','path','net_usdc','return_pct','trades','wins','net_win_rate',
            'price_only_usdc','spread_slippage_usdc','fees_usdc','funding_usdc','fixed_trades_stress_net_usdc',
            'mean_net_usdc','profit_factor','mean_hold_minutes','forced_end_trades','sampled_max_drawdown_pct','halt_reason',
            'halted_fraction','potential_entry_signals','potential_signals_cost_eligible','space_filter_rejections']
        with (out/'all-scenarios.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
            for r in rows:w.writerow({k:({**r,**r['metrics']}).get(k) for k in columns})
        text=['# 固定較長窗口與時間尺度實驗','',f"Primary: {PRIMARY} — **{summary['primary_sign']}**",'',
            '264個原生1m/5m/15m情境。回顧性、部分行情已曝光，不是實盤或未見最終驗證。',
            '全段戶口不補錢不重設；三個日曆分段是獨立實驗，不能串接收益。','',
            '|粒度|策略|成本|路徑|淨利USDC|贏/平倉|勝率|停機|',
            '|---|---|---|---|---:|---:|---:|---|']
        for r in rows:
            if r['window'] not in ('full',) and not(r['interval']=='1m' and r['window']=='common_2d'):continue
            m=r['metrics'];rate='undefined' if m['net_win_rate'] is None else f"{m['net_win_rate']*100:.2f}%"
            text.append(f"|{r['interval']}|{r['candidate']}|{r['cost']}|{r['path']}|{float(m['net_usdc']):+.8f}|{m['wins']}/{m['trades']}|{rate}|{r['halt_reason']}|")
        text+=['','同一2日窗口及日曆分段詳見CSV和完整交易；不同15/49日期間不能直接排名。',
            'OHLC內路徑、spread/depth/slippage、funding oracle代理及目前metadata仍是假設。',
            '延長歷史不保證增加交易：連敗3次或回撤停機之後不再開倉。',
            'SPACE過濾只用過去範圍，不預測將來；確認離場不會停用止損。',
            '55/60觀察值不是未來勝率。沒有實盤下單、合併或部署。']
        (out/'REPORT.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
        print('\n'.join(text),flush=True);return 0
    except Exception as exc:
        write_json(out/'status.json',{'status':'INCOMPLETE_NO_VALIDATED_RESULT','error':str(exc),'provenance':provenance()})
        print('INCOMPLETE_NO_VALIDATED_RESULT '+str(exc),flush=True);return 2
    finally:manifest(out)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('prepare','compute','aggregate'))
    p.add_argument('--out',type=Path,required=True);p.add_argument('--prepared',type=Path);p.add_argument('--results',type=Path)
    p.add_argument('--tf',choices=tuple(INTERVALS));a=p.parse_args()
    if a.mode=='prepare':raise SystemExit(prepare(a.out))
    if a.mode=='compute':raise SystemExit(compute(a.prepared,a.out,a.tf))
    raise SystemExit(aggregate(a.prepared,a.results,a.out))
