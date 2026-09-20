"""Explicit artificial fixtures only. These tests are not market performance."""
import copy
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal as D
import io
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paperlab import engine as core
from paperlab.backtest import assumed_quote
from paperlab.common import Config
from audit_data import write_json
import paired
import inputs
import verify_execution as verify
import run_paired as runner
import model as native


def fixture(quarters=190):
    start=1782864000000;minutes=[];last=3000.0
    for j in range(quarters*15):
        close=3000+15*math.sin(j/110)+math.sin(j/13)+.0001*j
        minutes.append(dict(t=start+j*60000,T=start+(j+1)*60000-1,o=str(last),c=str(close),
            h=str(max(last,close)+.08),l=str(min(last,close)-.08),v='1',n=1));last=close
    coarse=[]
    for i in range(0,len(minutes),15):
        b=minutes[i:i+15]
        coarse.append(dict(t=b[0]['t'],T=b[-1]['T'],o=b[0]['o'],c=b[-1]['c'],
            h=str(max(D(x['h']) for x in b)),l=str(min(D(x['l']) for x in b)),v='15',n=15))
    funding=[dict(time=t+50,rate='0.0000125') for t in range(start-3600000,minutes[-1]['T'],3600000)]
    base=dict(schema='longer-eth-history-v1',network='mainnet',coin='ETH',source='ARTIFICIAL_TEST_FIXTURE',
        metadata=dict(sz_decimals=4,max_leverage=25,point_in_time=False),
        funding=funding,downloaded_ms=minutes[-1]['T']+5000)
    data={'1m':{**copy.deepcopy(base),'interval':'1m','candles':minutes},
          '15m':{**copy.deepcopy(base),'interval':'15m','candles':coarse}}
    return data,coarse[120]['t'],coarse[-1]['T']+1


def direct(mode='FINE_1M_TP_CAP',direction=1):
    cfg=Config();at=1789000002000
    holder={'i':120,'f':{'upper':D(3000),'lower':D(2990)}}
    eng=paired.ExecutionEngine(cfg,core.new_state(cfg,at,'ARTIFICIAL'),paired.SPECS[1],mode,holder)
    qt=assumed_quote(at,D(3000),'1',{'sz_decimals':4,'max_leverage':25})
    sig=core.Signal(direction,at-2001,D(1),D(3000),'ARTIFICIAL')
    assert eng.open_position(qt,sig)
    return eng,qt


class PolicyTests(unittest.TestCase):
    def test_fixed_grid(self):
        self.assertEqual(len(paired.SPECS)*len(paired.MODES)*2*2,36)
        self.assertEqual(paired.PRIMARY,('CONFIRM_360','FINE_1M_TP_CAP'))
    def test_no_optimized_selection(self):
        self.assertIn('No tuning',runner.protocol()['frozen_rules'])
    def test_failure_long_two_closes(self):
        t={'direction':1,'opened_ms':100,'signal_atr':'4','breakout_boundary':'3000'}
        f={'last_two_ends':[110,120],'last_two_closes':[D(2998),D(2998)]}
        self.assertTrue(paired.failed_breakout(f,t));self.assertTrue(verify.policy_exit(f,t,paired.SPECS[2]))
    def test_failure_short_two_closes(self):
        t={'direction':-1,'opened_ms':100,'signal_atr':'4','breakout_boundary':'3000'}
        f={'last_two_ends':[110,120],'last_two_closes':[D(3002),D(3002)]}
        self.assertTrue(paired.failed_breakout(f,t))
    def test_failure_not_before_entry(self):
        t={'direction':1,'opened_ms':100,'signal_atr':'4','breakout_boundary':'3000'}
        f={'last_two_ends':[99,120],'last_two_closes':[D(2998),D(2998)]}
        self.assertFalse(paired.failed_breakout(f,t))
    def test_failure_one_confirm_not_enough(self):
        t={'direction':1,'opened_ms':100,'signal_atr':'4','breakout_boundary':'3000'}
        f={'last_two_ends':[110,120],'last_two_closes':[D(3001),D(2998)]}
        self.assertFalse(paired.failed_breakout(f,t))
    def test_failure_exact_threshold_not_enough(self):
        t={'direction':1,'opened_ms':100,'signal_atr':'4','breakout_boundary':'3000'}
        f={'last_two_ends':[110,120],'last_two_closes':[D(2999),D(2999)]}
        self.assertFalse(paired.failed_breakout(f,t))
    def test_entry_anchor_immutable(self):
        eng,q=direct();anchor=eng.position['breakout_boundary'];eng.holder['f']['upper']=D(5000)
        self.assertEqual(eng.position['breakout_boundary'],anchor)
    def test_cap_long_never_better_than_target(self):
        eng,q=direct();target=D(eng.position['target']);at=q.observed_ms+21000
        eng.close_position(assumed_quote(at,D(3040),'1',{'sz_decimals':4,'max_leverage':25}),'TARGET_OBSERVED_PRICE')
        self.assertEqual(D(eng.state['trades'][0]['exit']),target)
        self.assertGreater(D(eng.state['trades'][0]['target_cap_haircut_usdc']),0)
    def test_cap_short_never_better_than_target(self):
        eng,q=direct(direction=-1);target=D(eng.position['target']);at=q.observed_ms+21000
        eng.close_position(assumed_quote(at,D(2960),'1',{'sz_decimals':4,'max_leverage':25}),'TARGET_OBSERVED_PRICE')
        self.assertEqual(D(eng.state['trades'][0]['exit']),target)
    def test_cap_adjusts_fee(self):
        eng,q=direct();eng.close_position(assumed_quote(q.observed_ms+21000,D(3040),'1',{'sz_decimals':4,'max_leverage':25}),'TARGET_OBSERVED_PRICE')
        t=eng.state['trades'][0];self.assertEqual(D(t['exit_fee']),D(t['qty'])*D(t['target'])*D('.00045'))
    def test_stop_gap_not_improved(self):
        eng,q=direct();price=D(2900)
        eng.tick(assumed_quote(q.observed_ms+21000,price,'1',{'sz_decimals':4,'max_leverage':25}))
        t=eng.state['trades'][0];self.assertEqual(t['close_reason'],'STOP_OBSERVED_PRICE')
        self.assertLess(D(t['exit']),D(t['stop']));self.assertEqual(D(t['target_cap_haircut_usdc']),0)
    def test_uncapped_mode_keeps_observed_target(self):
        eng,q=direct('FINE_1M');eng.close_position(assumed_quote(q.observed_ms+21000,D(3040),'1',{'sz_decimals':4,'max_leverage':25}),'TARGET_OBSERVED_PRICE')
        self.assertGreater(D(eng.state['trades'][0]['exit']),D(eng.state['trades'][0]['target']))
    def test_cap_not_applied_to_time_exit(self):
        eng,q=direct();eng.close_position(assumed_quote(q.observed_ms+21000,D(3040),'1',{'sz_decimals':4,'max_leverage':25}),'MAX_HOLD_TIME')
        self.assertEqual(D(eng.state['trades'][0]['target_cap_haircut_usdc']),0)
    def test_cap_valuation_no_phantom_peak(self):
        eng,q=direct();t=eng.position;qt=assumed_quote(q.observed_ms+21000,D(3040),'1',{'sz_decimals':4,'max_leverage':25})
        expected=eng.cash+D(t['qty'])*(D(t['target'])-D(t['entry']))-D(t['qty'])*D(t['target'])*D('.00045')
        self.assertEqual(D(eng.valuation(qt)['net_equity']),expected)
    def test_fill_hook_restored(self):
        eng,q=direct();original=core.market_fill
        with patch.object(core.Engine,'close_position',side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):eng.close_position(assumed_quote(q.observed_ms+21000,D(3040),'1',{'sz_decimals':4,'max_leverage':25}),'TARGET_OBSERVED_PRICE')
        self.assertIs(core.market_fill,original)
    def test_signal_hook_restored(self):
        original=core.strategy_signal
        with self.assertRaises(RuntimeError):
            with paired.installed({},paired.SPECS[0]):raise RuntimeError('test')
        self.assertIs(core.strategy_signal,original)
    def test_no_trade_rate_undefined(self):
        m=paired.summarize([],'base_assumptions','FINE_1M_TP_CAP')
        self.assertIsNone(m['net_win_rate']);self.assertIsNone(m['wilson95_iid']);self.assertEqual(m['trades'],0)
    def test_fee_decomposition_with_cap(self):
        eng,q=direct();eng.close_position(assumed_quote(q.observed_ms+21000,D(3040),'1',{'sz_decimals':4,'max_leverage':25}),'TARGET_OBSERVED_PRICE')
        m=paired.summarize(eng.state['trades'],'base_assumptions','FINE_1M_TP_CAP')
        v=D(m['price_only_usdc'])-D(m['spread_slippage_usdc'])-D(m['target_cap_haircut_usdc'])-D(m['fees_usdc'])+D(m['funding_usdc'])
        self.assertEqual(v,D(m['net_usdc']))
    def test_network_guard(self):
        for event in ('socket.connect','socket.getaddrinfo','subprocess.Popen'):
            with self.assertRaises(RuntimeError):runner.no_network(event,())
        runner.no_network('open',())


class DataAndReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data,cls.start,cls.end=fixture();cls.fv=paired.features(cls.data['15m']['candles'])
        cls.ref=verify.reference_features(cls.data['15m']['candles'],cls.fv)
    def replay(self,mode='FINE_1M_TP_CAP',spec=None,path='OHLC'):
        return paired.replay(self.data,self.start,self.end,spec or paired.SPECS[1],mode,'base_assumptions',path,self.fv)
    def test_valid_pair(self):self.assertEqual(inputs.validate_pair(self.data,self.start,self.end)['groups'],190)
    def test_missing_minute_refused(self):
        d=copy.deepcopy(self.data);d['1m']['candles'].pop(1870)
        with self.assertRaises(ValueError):inputs.validate_pair(d,self.start,self.end)
    def test_missing_15m_refused(self):
        d=copy.deepcopy(self.data);d['15m']['candles'].pop(140)
        with self.assertRaises(ValueError):inputs.validate_pair(d,self.start,self.end)
    def test_cross_resolution_mutation_refused(self):
        d=copy.deepcopy(self.data);d['15m']['candles'][140]['h']='3040'
        with self.assertRaises(ValueError):inputs.validate_pair(d,self.start,self.end)
    def test_missing_funding_refused(self):
        d=copy.deepcopy(self.data);d['1m']['funding']=[]
        with self.assertRaises(ValueError):inputs.validate_pair(d,self.start,self.end)
    def test_wrong_network_refused(self):
        d=copy.deepcopy(self.data);d['1m']['network']='testnet'
        with self.assertRaises(ValueError):inputs.validate_pair(d,self.start,self.end)
    def test_unclosed_bar_refused(self):
        d=copy.deepcopy(self.data);d['1m']['downloaded_ms']=d['1m']['candles'][-1]['T']
        with self.assertRaises(ValueError):inputs.validate_pair(d,self.start,self.end)
    def test_future_coarse_candles_cannot_change_prefix(self):
        bars=copy.deepcopy(self.data['15m']['candles'])
        for b in bars[155:]:
            for k in 'ohlc':b[k]=str(D(b[k])*2)
        changed=paired.features(bars)
        for i in range(120,156):self.assertEqual(changed[i],self.fv[i])
    def test_features_match_independent_reference(self):self.assertEqual(len(self.ref),70)
    def test_changed_anchor_feature_rejected(self):
        f=copy.deepcopy(self.fv);f[130]['upper']+=1
        with self.assertRaises(ValueError):verify.reference_features(self.data['15m']['candles'],f)
    def test_exact_legacy_baseline(self):
        for s in paired.SPECS[:2]:
            for cost in ('base_assumptions','cost_stress'):
                for path in ('OHLC','OLHC'):
                    actual=paired.replay(self.data,self.start,self.end,s,'COARSE_15M',cost,path,self.fv)
                    oldspec=next(x for x in native.SPECS if x['id']=='BREAK_TREND_CONFIRM_'+str(s['max_minutes']))
                    expected=native.replay(self.data['15m'],120,190,oldspec,cost,path)
                    self.assertEqual(actual['ending_usdc'],expected['ending_usdc'])
                    self.assertEqual(actual['sampled_max_drawdown_pct'],expected['sampled_max_drawdown_pct'])
                    for a,b in zip(actual['trades'],expected['trades']):
                        for k in b:
                            if k!='open_reason':self.assertEqual(a[k],b[k],k)
    def test_signal_only_at_15m_boundary(self):
        row=self.replay()
        self.assertTrue(row['trades'])
        for t in row['trades']:
            self.assertEqual(t['opened_ms']%900000,2000)
            self.assertLess(t['signal_bar_ms'],t['opened_ms'])
    def test_all_modes_and_policies_independently_audited(self):
        for spec in paired.SPECS:
            for mode in paired.MODES:
                for path in ('OHLC','OLHC'):
                    row=self.replay(mode,spec,path);verify.audit(row,self.data,self.ref)
    def test_mutated_fee_rejected(self):
        row=self.replay();row['trades'][0]['entry_fee']='10'
        with self.assertRaises(ValueError):verify.audit(row,self.data,self.ref)
    def test_mutated_target_cap_rejected(self):
        row=self.replay();row['trades'][0]['target_cap_haircut_usdc']='1'
        with self.assertRaises(ValueError):verify.audit(row,self.data,self.ref)
    def test_mutated_join_rejected(self):
        row=self.replay();row['trades'][0]['signal_bar_ms']+=900000
        with self.assertRaises(ValueError):verify.audit(row,self.data,self.ref)
    def test_mutated_anchor_rejected(self):
        row=self.replay();row['trades'][0]['breakout_boundary']='1'
        with self.assertRaises(ValueError):verify.audit(row,self.data,self.ref)
    def test_mutated_drawdown_rejected(self):
        row=self.replay();row['sampled_max_drawdown_pct']='100'
        with self.assertRaises(ValueError):verify.audit(row,self.data,self.ref)
    def test_halt_not_reset(self):
        row=self.replay();self.assertEqual(row['account_config']['max_consecutive_losses'],3)
        if row['halt_ms']:
            self.assertTrue(all(t['opened_ms']<=row['halt_ms'] for t in row['trades']))
    def test_shadow_not_return_or_win_rate(self):
        x=paired.shadow_signals(self.data,self.start,self.end,self.fv)
        self.assertIsNone(x['win_rate']);self.assertIsNone(x['account_return']);self.assertTrue(x['signals'])
    def test_shadow_censored(self):
        x=paired.shadow_signals(self.data,self.start,self.end,self.fv)
        self.assertTrue(any(s['labels']['360']['status']=='RIGHT_CENSORED' for s in x['signals']))
    def test_shadow_does_not_change_main(self):
        before=self.replay();paired.shadow_signals(self.data,self.start,self.end,self.fv);after=self.replay()
        self.assertEqual(before,after)
    def test_unknown_spec_refused(self):
        with self.assertRaises(ValueError):self.replay(spec=dict(id='new',max_minutes=0,exit='none'))
    def test_no_data_gap_replay(self):
        d=copy.deepcopy(self.data);d['1m']['candles'].pop(1820)
        with self.assertRaises(ValueError):paired.replay(d,self.start,self.end,paired.SPECS[0],'FINE_1M','base_assumptions','OHLC',self.fv)


class OutputTests(unittest.TestCase):
    def test_existing_out_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):runner.run(Path(tmp),Path(tmp),Path(tmp),Path(tmp))
    def test_failed_data_no_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'out'
            with patch.object(runner,'load_inputs',side_effect=ValueError('Missing data')),redirect_stdout(io.StringIO()):
                self.assertEqual(runner.run(Path(tmp),Path(tmp),Path(tmp),out),2)
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'status.json').exists())
    def test_manifest_traversal_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_json(Path(tmp)/'manifest.json',{'../escape':'bad'})
            with self.assertRaises(ValueError):inputs.manifest(tmp)
    def test_manifest_tamper_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'data').write_text('changed');write_json(Path(tmp)/'manifest.json',{'data':'0'*64})
            with self.assertRaises(ValueError):inputs.manifest(tmp)
    def test_incomplete_summary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):runner.export(Path(tmp),[],{},{},{})
    def test_artificial_full_pipeline(self):
        d,start,end=fixture(155)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'out'
            with patch.object(runner,'START',start),patch.object(runner,'END',end),patch.object(runner,'load_inputs',return_value=(d,[],{})),patch.object(runner,'baseline_check',return_value={'status':'ARTIFICIAL_ONLY'}),redirect_stdout(io.StringIO()):
                result=runner.run(Path(tmp),Path(tmp),Path(tmp),out)
            self.assertEqual(result,0,(out/'status.json').read_text())
            s=inputs.read(out/'SUMMARY.json');self.assertEqual(s['cases'],36);self.assertEqual(s['market_requests_in_this_run'],0)
            self.assertEqual(s['qualified_reliable55'],0);self.assertTrue((out/'shadow-signals.json').is_file())


if __name__=='__main__':unittest.main()
