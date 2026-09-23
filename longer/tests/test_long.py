"""Offline fixtures: correctness tests, never investment-performance evidence."""
import copy
from contextlib import redirect_stdout
from decimal import Decimal as D
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import market_data as md
import model
import verify_long as verify
import run_long as run
from audit_data import write_json, digest
from paperlab import backtest as old, engine as core
from paperlab.common import Config
from test_backtest import fixture


def fake(tf='1m',n=420):
    dt=md.INTERVALS[tf];start=md.ms('2026-07-01T00:00:00+00:00');bars=[];last=3000.0
    for i in range(n):
        close=3000+13*math.sin(i/9)+7*math.sin(i/23)
        bars.append({'t':start+i*dt,'T':start+(i+1)*dt-1,'o':str(last),
            'h':str(max(last,close)+.2),'l':str(min(last,close)-.2),'c':str(close),'v':'1','n':10})
        last=close
    funding=[{'time':t+76,'rate':'0.0000125'} for t in range(start-md.HOUR,bars[-1]['T'],md.HOUR)]
    return {'schema':'longer-eth-history-v1','network':'mainnet','coin':'ETH','interval':tf,
        'candles':bars,'funding':funding,'metadata':{'sz_decimals':4,'max_leverage':25,'point_in_time':False}}


def rawbars(tf='5m',n=130):
    d=fake(tf,n);return [{**b,'s':'ETH','i':tf} for b in d['candles']]

class AdmissionTests(unittest.TestCase):
    def test_three_timeframes_allowed(self):
        for tf in md.INTERVALS:md.Client.validate_body({'type':'candleSnapshot','req':{'coin':'ETH','interval':tf,'startTime':1,'endTime':100}})
    def test_no_order_endpoint(self):
        with self.assertRaises(ValueError):md.Client.validate_body({'type':'order','coin':'ETH'})
    def test_no_wallet_queries(self):
        with self.assertRaises(ValueError):md.Client.validate_body({'type':'clearinghouseState','user':'0x0'})
    def test_no_credentials(self):
        with self.assertRaises(ValueError):md.Client.validate_body({'type':'metaAndAssetCtxs','signature':'secret'})
    def test_only_eth(self):
        with self.assertRaises(ValueError):md.Client.validate_body({'type':'candleSnapshot','req':{'coin':'BTC','interval':'5m','startTime':1,'endTime':100}})
    def test_other_timeframe_refused(self):
        with self.assertRaises(ValueError):md.Client.validate_body({'type':'candleSnapshot','req':{'coin':'ETH','interval':'1d','startTime':1,'endTime':100}})
    def test_response_range_bounded(self):
        with self.assertRaises(ValueError):md.Client.validate_body({'type':'candleSnapshot','req':{'coin':'ETH','interval':'1m','startTime':1,'endTime':5001*60000}})
    def test_book_whitelist(self):
        md.Client.validate_body({'type':'l2Book','coin':'ETH'})
        with self.assertRaises(ValueError):md.Client.validate_body({'type':'l2Book','coin':'BTC'})
    def test_fixed_dates_fit_under_5000(self):
        for tf in md.INTERVALS:self.assertLessEqual((md.ms(md.END)-md.ms(md.STARTS[tf]))//md.INTERVALS[tf]+120,5000)
    def test_full_valid_bars(self):
        for tf in md.INTERVALS:
            b=rawbars(tf);self.assertEqual(len(md.bars(b,tf,b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)),130)
    def test_missing_bar(self):
        b=rawbars();x=b[:30]+b[31:]
        with self.assertRaises(ValueError):md.bars(x,'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)
    def test_duplicate_bar(self):
        b=rawbars();x=b+[b[40]]
        with self.assertRaises(ValueError):md.bars(x,'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)
    def test_missing_first_bar(self):
        b=rawbars()
        with self.assertRaises(ValueError):md.bars(b[1:],'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)
    def test_missing_last_bar(self):
        b=rawbars()
        with self.assertRaises(ValueError):md.bars(b[:-1],'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)
    def test_unclosed_bar(self):
        b=rawbars()
        with self.assertRaises(ValueError):md.bars(b,'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+1)
    def test_wrong_bar_interval(self):
        b=rawbars();b[1]['i']='1m'
        with self.assertRaises(ValueError):md.bars(b,'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)
    def test_invalid_ohlc(self):
        b=rawbars();b[10]['h']='1'
        with self.assertRaises(ValueError):md.bars(b,'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)
    def test_nan(self):
        with self.assertRaises(ValueError):md.number('NaN')
    def test_boolean_amount(self):
        with self.assertRaises(ValueError):md.number(True)
    def test_negative_volume(self):
        b=rawbars();b[10]['v']='-1'
        with self.assertRaises(ValueError):md.bars(b,'5m',b[0]['t'],b[-1]['T']+1,b[-1]['T']+5000)
    def test_missing_funding(self):
        with self.assertRaises(ValueError):md.rates([],3600000,3*3600000)
    def test_duplicate_funding(self):
        r={'coin':'ETH','time':3600076,'fundingRate':'0.0001'}
        with self.assertRaises(ValueError):md.rates([r,r],3600000,7200000)
    def test_negative_and_zero_funding_allowed(self):
        r=[{'coin':'ETH','time':3600076,'fundingRate':'-0.0001'},{'coin':'ETH','time':7200076,'fundingRate':'0'}]
        self.assertEqual(len(md.rates(r,3600000,3*3600000)),2)
    def test_second_download_disagreement(self):
        d={'5m':fake('5m')};other=copy.deepcopy(d);other['5m']['candles'][1]['c']='3001'
        with self.assertRaises(ValueError):md.compare(d,other)
    def test_cross_resolution_ohlc(self):
        low=fake('1m',420);hi=copy.deepcopy(low);hi['interval']='5m';hi['candles']=[]
        for i in range(0,420,5):
            b=low['candles'][i:i+5];hi['candles'].append({'t':b[0]['t'],'T':b[-1]['T'],'o':b[0]['o'],'c':b[-1]['c'],
                'h':str(max(D(x['h']) for x in b)),'l':str(min(D(x['l']) for x in b))})
        self.assertEqual(md.cross_resolution(low,hi)['groups'],84)
        hi['candles'][1]['c']='100'
        with self.assertRaises(ValueError):md.cross_resolution(low,hi)

class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data={tf:fake(tf) for tf in md.INTERVALS}
        cls.fv={tf:model.features(d['candles']) for tf,d in cls.data.items()}
        cls.ref={tf:verify.check_features(d,cls.fv[tf]) for tf,d in cls.data.items()}
    def test_six_ablation_specs(self):self.assertEqual(len(model.SPECS),6)
    def test_original_source_unchanged(self):
        import hashlib
        self.assertEqual(hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),run.ENGINE_HASH)
    def test_original_1m_exact_ledger(self):
        d=old.validate_dataset(fixture(680));d['interval']='1m';fv=model.features(d['candles'])
        for cost in old.COSTS:
            for path in old.PATHS:
                a=model.replay(d,120,680,model.SPECS[0],cost,path,fv);b=old.replay(d,120,680,9,21,'both',cost,path)
                self.assertEqual(a['trades'],b['trades']);self.assertEqual(a['ending_usdc'],b['model_end_usdc'])
                self.assertEqual(a['sampled_max_drawdown_pct'],b['model_max_drawdown_pct'])
                self.assertEqual(a['halt_reason'],b['halt_reason'])
    def test_independent_features_all_intervals(self):
        for tf in self.data:self.assertEqual(len(self.ref[tf]),len(self.fv[tf]))
    def test_no_lookahead(self):
        for tf,d in self.data.items():
            changed=copy.deepcopy(d)
            for b in changed['candles'][300:]:
                for k in 'ohlc':b[k]=str(D(b[k])*2)
            fv=model.features(changed['candles'])
            for i in range(120,301):self.assertEqual(fv[i],self.fv[tf][i])
    def test_independent_detects_feature_mutation(self):
        fv=copy.deepcopy(self.fv['5m']);fv[180]['atr']+=1
        with self.assertRaises(ValueError):verify.check_features(self.data['5m'],fv)
    def test_conf_exit_needs_two_bars(self):
        # A single recent negative spread is insufficient unless both comparisons hold.
        self.assertTrue(any(not f['confirm_long'] for f in self.fv['5m'].values()))
        for f in self.fv['5m'].values():
            self.assertFalse(f['confirm_long'] and f['confirm_short'])
    def test_entry_unchanged_across_exit_ablation(self):
        for f in self.fv['5m'].values():
            self.assertEqual(model.entry_signal(f,model.SPECS[0]),model.entry_signal(f,model.SPECS[1]))
            self.assertEqual(model.entry_signal(f,model.SPECS[2]),model.entry_signal(f,model.SPECS[3]))
            self.assertEqual(model.entry_signal(f,model.SPECS[3]),model.entry_signal(f,model.SPECS[4]))
    def test_space_only_filters_not_creates_entry(self):
        for f in self.fv['5m'].values():self.assertIn(model.entry_signal(f,model.SPECS[5]),(0,model.entry_signal(f,model.SPECS[4])))
    def test_stop_not_blocked_by_confirmation(self):
        from dataclasses import replace
        start=md.ms('2026-07-01T12:00:00+00:00');spec=model.SPECS[4]
        cfg=replace(Config(),max_hold_seconds=21600,gap_halt_seconds=945)
        eng=model.DiagnosticEngine(cfg,core.new_state(cfg,start,'TEST'),spec)
        q=old.assumed_quote(start,'3000','1',self.data['15m']['metadata'])
        self.assertTrue(eng.open_position(q,core.Signal(1,start-2001,D(2),D(3000),'TEST')))
        eng.tick(old.assumed_quote(start+300000,'2980','1',self.data['15m']['metadata']),None)
        self.assertIsNone(eng.position);self.assertEqual(eng.state['trades'][0]['close_reason'],'STOP_OBSERVED_PRICE')
    def test_hooks_restore_on_exception(self):
        saved=core.strategy_signal
        with self.assertRaises(RuntimeError):
            with model.installed({},model.SPECS[0],{}):raise RuntimeError('fail')
        self.assertIs(core.strategy_signal,saved)
    def test_native_all_cases_audited(self):
        for tf in self.data:
            for spec in model.SPECS:
                for cost in old.COSTS:
                    for path in old.PATHS:
                        r=model.replay(self.data[tf],120,420,spec,cost,path,self.fv[tf]);verify.audit(r,self.data[tf],self.ref[tf])
                        self.assertFalse(r['open_position']);self.assertFalse(r['pending_funding']);self.assertFalse(r['integrity_warnings'])
                        self.assertNotEqual(r['halt_reason'],'DATA_GAP_WITH_POSITION')
                        self.assertLessEqual(D(r['metrics']['fixed_trades_stress_net_usdc']),D(r['metrics']['net_usdc'])+D('1e-15'))
    def test_single_account_no_overlaps(self):
        r=model.replay(self.data['15m'],120,420,model.SPECS[4],'base_assumptions','OHLC',self.fv['15m'])
        self.assertEqual(r['initial_usdc'],'10')
        for a,b in zip(r['trades'],r['trades'][1:]):self.assertGreaterEqual(b['opened_ms']-a['closed_ms'],900000)
    def test_same_risk_limits(self):
        r=model.replay(self.data['5m'],120,420,model.SPECS[4],'base_assumptions','OHLC',self.fv['5m'])
        for name in ('risk_fraction_per_trade','account_halt_drawdown','max_consecutive_losses','max_entries_per_24h','cooldown_seconds','stop_floor_fraction','stop_ceiling_fraction','max_notional_to_equity'):
            self.assertEqual(r['account_config'][name],getattr(Config(),name))
    def test_cost_gate_not_relaxed(self):
        r=model.replay(self.data['5m'],120,420,model.SPECS[4],'cost_stress','OHLC',self.fv['5m'])
        self.assertEqual(r['trades'],[]);self.assertEqual(r['potential_signals_cost_eligible'],0)
    def test_amount_mutations_detected(self):
        r=model.replay(self.data['5m'],120,420,model.SPECS[0],'base_assumptions','OHLC',self.fv['5m']);self.assertTrue(r['trades'])
        for field in ('entry_fee','gross_pnl','qty','exit','stop'):
            x=copy.deepcopy(r);x['trades'][0][field]=str(D(x['trades'][0][field])+1)
            with self.assertRaises(ValueError):verify.audit(x,self.data['5m'],self.ref['5m'])
    def test_aggregate_mutations_detected(self):
        r=model.replay(self.data['5m'],120,420,model.SPECS[0],'base_assumptions','OHLC',self.fv['5m'])
        for field in ('net_usdc','fees_usdc','funding_usdc','price_only_usdc','fixed_trades_stress_net_usdc'):
            x=copy.deepcopy(r);x['metrics'][field]=str(D(x['metrics'][field])+1)
            with self.assertRaises(ValueError):verify.audit(x,self.data['5m'],self.ref['5m'])
    def test_flat_no_trade_undefined(self):
        self.assertIsNone(model.metrics([],'base_assumptions')['net_win_rate'])
        self.assertIsNone(model.metrics([],'base_assumptions')['wilson95_iid'])
    def test_wilson_small_sample(self):
        self.assertLess(model.wilson(1,1)[0],.3)
        self.assertLess(model.wilson(55,100)[0],.55)
    def test_window_end_does_not_extend_data(self):
        for tf in self.data:
            r=model.replay(self.data[tf],120,200,model.SPECS[4],'base_assumptions','OHLC',self.fv[tf])
            self.assertLessEqual(max((t['closed_ms'] for t in r['trades']),default=0),self.data[tf]['candles'][199]['T'])
    def test_halt_not_reset_in_longer_window(self):
        d=fake('5m',800)
        last=3000.0
        for i,b in enumerate(d['candles']):
            c=3000+.3*math.sin(i/2)
            b.update(o=str(last),c=str(c),h=str(max(last,c)+.01),l=str(min(last,c)-.01));last=c
        fv=model.features(d['candles'])
        short=model.replay(d,120,420,model.SPECS[0],'base_assumptions','OHLC',fv)
        long=model.replay(d,120,800,model.SPECS[0],'base_assumptions','OHLC',fv)
        self.assertTrue(short['halt_reason']);self.assertEqual(short['trades'],long['trades'])
        self.assertGreater(long['halted_fraction'],short['halted_fraction'])

class PipelineTests(unittest.TestCase):
    def test_264_cases(self):
        cases={(tf,w,s['id'],c,p) for tf in md.INTERVALS for w,_,_ in model.windows(tf) for s in model.SPECS for c in old.COSTS for p in old.PATHS}
        self.assertEqual(len(cases),264)
    def test_calendar_splits_nonoverlap_cover_full(self):
        for tf in ('5m','15m'):
            w=model.windows(tf);parts=w[2:]
            self.assertEqual(parts[0][1],w[0][1]);self.assertEqual(parts[-1][2],w[0][2])
            self.assertTrue(all(a[2]==b[1] for a,b in zip(parts,parts[1:])))
    def test_common_window_identical(self):
        self.assertEqual(len({(a,b) for tf in md.INTERVALS for n,a,b in model.windows(tf) if n=='common_2d'}),1)
    def test_primary_frozen(self):self.assertEqual(tuple(run.protocol()['primary']),('5m','BREAK_TREND_CONFIRM_360','full'))
    def test_no_future_label(self):self.assertIn('PARTLY_PREVIOUSLY_SEEN',run.protocol()['epistemic_status'])
    def test_frozen_before_fetch_and_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'prepared'
            def fail(path):
                self.assertTrue((out/'frozen.json').is_file());raise ValueError('no network')
            with patch.object(run,'collect',side_effect=fail),redirect_stdout(io.StringIO()):self.assertEqual(run.prepare(out),2)
            self.assertFalse((out/'ready.json').exists());self.assertTrue((out/'manifest.json').exists())
    def test_existing_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):run.prepare(Path(tmp))
    def test_missing_shard_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'out'
            with patch.object(run,'prepared',return_value=({},{})),redirect_stdout(io.StringIO()):
                self.assertEqual(run.aggregate(Path(tmp),Path(tmp)/'missing',out),2)
            self.assertFalse((out/'SUMMARY.json').exists())
    def test_no_trade_not_positive(self):
        rows=[{'metrics':{'trades':0,'net_usdc':'0'}}]*4
        self.assertEqual(run.classify(rows),'NO_TRADES')
    def test_loss_plus_no_trade_is_explicit(self):
        rows=[{'metrics':{'trades':1,'net_usdc':'-.1'}}]*2+[{'metrics':{'trades':0,'net_usdc':'0'}}]*2
        self.assertEqual(run.classify(rows),'LOSS_AND_NO_TRADES_OR_FLAT')

class IntegrationTests(unittest.TestCase):
    def test_recorded_transport_download_normalization(self):
        end=md.ms('2026-07-04T00:00:00+00:00')
        starts={tf:end-130*dt for tf,dt in md.INTERVALS.items()}
        class Response:
            def __init__(self,value):self.raw=json.dumps(value).encode()
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def geturl(self):return 'https://api.hyperliquid.xyz/info'
            def read(self,limit):return self.raw[:limit]
        class Opener:
            def open(self,request,timeout):
                body=json.loads(request.data)
                if body['type']=='metaAndAssetCtxs':return Response([{'universe':[{'name':'ETH','szDecimals':4,'maxLeverage':25}]},[{}]])
                if body['type']=='candleSnapshot':
                    r=body['req'];dt=md.INTERVALS[r['interval']]
                    return Response([{'t':t,'T':t+dt-1,'o':'3000','h':'3001','l':'2999','c':'3000','s':'ETH','i':r['interval'],'v':'1','n':3} for t in range(r['startTime'],r['endTime'],dt)])
                return Response([{'time':t+76,'fundingRate':'0.0000125','coin':'ETH'} for t in range((body['startTime']+md.HOUR-1)//md.HOUR*md.HOUR,body['endTime'],md.HOUR)])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'raw'
            client=md.Client(path,opener=Opener(),sleeper=lambda _:None,clock=lambda:end+5000)
            data=md.download_round(client,end,starts)
            self.assertEqual(md.audit_raw(path,data)['status'],'PASS_RAW_BYTES_TO_NORMALIZED')
            self.assertGreater(md.cross_resolution(data['1m'],data['5m'])['groups'],0)
            self.assertGreater(md.cross_resolution(data['5m'],data['15m'])['groups'],0)
            altered=copy.deepcopy(data);altered['5m']['funding'][0]['rate']='0'
            with self.assertRaises(ValueError):md.audit_raw(path,altered)
    def test_full_264_artificial_pipeline(self):
        data={tf:fake(tf,300) for tf in md.INTERVALS}
        def tiny_windows(tf):
            b=data[tf]['candles'];at=lambda i:b[0]['t']+i*md.INTERVALS[tf]
            if tf=='1m':return [('common_2d',at(120),at(300))]
            return [('full',at(120),at(300)),('common_2d',at(240),at(300))]+[(f'calendar_{j+1}',at(a),at(a+60)) for j,a in enumerate((120,180,240))]
        def collect(path):
            path.mkdir();write_json(path/'data.json',data);write_json(path/'checks.json',{'source':'SYNTHETIC_TEST_ONLY'})
            return data,{'source':'SYNTHETIC_TEST_ONLY'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(run,'windows',side_effect=tiny_windows),patch.object(run,'collect',side_effect=collect),redirect_stdout(io.StringIO()):
            root=Path(tmp);prep=root/'prepared';results=root/'results';results.mkdir()
            self.assertEqual(run.prepare(prep),0)
            for tf in md.INTERVALS:self.assertEqual(run.compute(prep,results/tf,tf),0,(results/tf/'status.json').read_text())
            self.assertEqual(run.aggregate(prep,results,root/'final'),0,(root/'final/status.json').read_text() if (root/'final/status.json').exists() else '')
            summary=json.loads((root/'final/SUMMARY.json').read_text())
            self.assertEqual(summary['cases'],264);self.assertEqual(summary['real_orders'],0)
            self.assertEqual(summary['future_income'],'NOT_ESTABLISHED')
            self.assertEqual(len(list(__import__('csv').DictReader((root/'final/all-scenarios.csv').open()))),264)
            # A success file with edited economics must not survive aggregation.
            path=results/'5m/results.json';r=json.loads(path.read_text());r[0]['metrics']['net_usdc']='99';write_json(path,r)
            self.assertEqual(run.aggregate(prep,results,root/'tampered'),2)
            self.assertFalse((root/'tampered/SUMMARY.json').exists())
    def test_three_losses_persist_in_native_simulation(self):
        d=fake('15m',600);last=3000
        for i,b in enumerate(d['candles']):
            c=3000+.2*math.sin(i/2);b.update(o=str(last),h=str(max(c,last)+.01),l=str(min(c,last)-.01),c=str(c));last=c
        r=model.replay(d,120,600,model.SPECS[0],'base_assumptions','OHLC')
        self.assertEqual(r['halt_reason'],'CONSECUTIVE_LOSSES');self.assertEqual(r['metrics']['trades'],3)
        self.assertGreater(r['potential_signals_after_halt'],0)

if __name__=='__main__':unittest.main()
