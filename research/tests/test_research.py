"""Artificial fixtures only. These tests are NOT historical performance results."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal as D
from unittest.mock import patch

from test_backtest import fixture
from paperlab import engine as core, backtest as bt
import strategies as s
from statistics_report import metrics, wilson, fixed_trade_stress
from verify import audit_row, overlap_check, equal, load
import run as runner


def data(n=680):
    result=bt.validate_dataset(fixture(n))
    result.update(network='mainnet',downloaded_ms=result['candles'][-1]['T']+5000)
    return result


class StrategyTests(unittest.TestCase):
    def test_finite_grid(self):
        specs=s.candidates()
        self.assertEqual(len(specs),14)
        self.assertEqual(len({r['id'] for r in specs}),14)
        self.assertEqual(sum(r['round']==1 for r in specs),8)
        self.assertEqual(sum(r['round']==2 for r in specs),4)
    def test_zero_efficiency(self):
        self.assertEqual(s.efficiency([{'c':'10'}]*25,20),0)
    def test_directional_efficiency(self):
        self.assertEqual(s.efficiency([{'c':str(i+10)} for i in range(25)],20),1)
    def test_insufficient_efficiency(self):
        self.assertEqual(s.efficiency([{'c':'10'}],20),0)
    def test_complete_five_minute_only(self):
        bars=data()['candles'][:119]
        out=s.five_minute_closes(bars)
        self.assertEqual(len(out),23)
        altered=copy.deepcopy(bars)
        altered[-1]['c']='99999'
        self.assertEqual(out,s.five_minute_closes(altered))
    def test_missing_minute_not_aggregated(self):
        bars=data()['candles'][:120]
        del bars[2]
        self.assertEqual(len(s.five_minute_closes(bars)),23)
    def test_future_candles_do_not_change_prefix(self):
        d=data(); original=d['candles'][:120]
        reference=[s.eligible(original,1,x) for x in s.candidates()]
        d['candles'][120]['c']='9999999'
        self.assertEqual(reference,[s.eligible(d['candles'][:120],1,x) for x in s.candidates()])
    def test_breakout_excludes_current_high(self):
        bars=[{'c':'100','h':'101','l':'99'} for _ in range(120)]
        bars[-1].update(c='102',h='900')
        self.assertEqual(s.breakout_direction(bars,20),1)
    def test_breakout_requires_new_cross(self):
        bars=[{'c':'100','h':'101','l':'99'} for _ in range(120)]
        bars[-2].update(c='102',h='102')
        bars[-1].update(c='103',h='103')
        self.assertEqual(s.breakout_direction(bars,20),0)
    def test_short_breakout(self):
        bars=[{'c':'100','h':'101','l':'99'} for _ in range(120)]
        bars[-1].update(c='98',l='98')
        self.assertEqual(s.breakout_direction(bars,40),-1)
    def test_hook_restored_on_exception(self):
        saved=(bt.DirectionEngine,core.strategy_signal)
        with self.assertRaises(RuntimeError):
            with s.installed(s.candidates()[-1]):
                raise RuntimeError('fixture')
        self.assertEqual(saved,(bt.DirectionEngine,core.strategy_signal))
    def test_unknown_candidate_rejected(self):
        with self.assertRaises(ValueError):
            s.replay(data(),120,680,dict(id='unbounded'), 'base_assumptions','OHLC')
    def test_original_control_unchanged(self):
        d=data()
        old=bt.replay(d,120,680,9,21,'both','base_assumptions','OHLC')
        new=s.replay(d,120,680,s.candidates()[0],'base_assumptions','OHLC')
        self.assertEqual(old['trades'],new['trades'])
        self.assertEqual(old['model_net_pnl_usdc'],new['model_net_pnl_usdc'])
    def test_direction_filters_do_not_block_opposite_exit(self):
        spec=next(x for x in s.candidates() if x['family']=='efficiency')
        at=1735689600000+120*60000+2000
        quote=bt.assumed_quote(at,'3000','1',data()['metadata'])
        with s.installed(spec):
            e=bt.DirectionEngine(core.Config(),core.new_state(core.Config(),at,'TEST_ONLY'),'both')
            e.research_bars=data()['candles'][:120]
            signal=core.Signal(1,at-2001,D(3),D(3000),'fixture')
            with patch.object(s,'eligible',return_value=True):
                self.assertTrue(e.open_position(quote,signal))
            e.warm_started=True
            sig=core.Signal(-1,at+57999,D(3),D(3000),'fixture opposite')
            newquote=bt.assumed_quote(at+60000,'3000','1',data()['metadata'])
            # Avoid data-gap halt, so specifically exercise opposite-signal exit.
            e.state['last_quote_ms']=at+50000
            with patch.object(s,'eligible',return_value=False), patch.object(core,'strategy_signal',return_value=sig):
                e.tick(newquote,data()['candles'][:120])
            self.assertIsNone(e.position)
            self.assertEqual(e.state['trades'][-1]['close_reason'],'OPPOSITE_EMA_CROSS')


class ArithmeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=data()
        cls.spec=s.candidates()[0]
        cls.row=s.replay(cls.data,120,680,cls.spec,'base_assumptions','OHLC')
    def test_independent_audit(self):
        self.assertEqual(audit_row(self.row,self.data,self.spec)['status'],'PASS_INDEPENDENT_MODEL_ARITHMETIC')
    def test_fee_mutation(self):
        r=copy.deepcopy(self.row);self.assertTrue(r['trades']);r['trades'][0]['entry_fee']='0'
        with self.assertRaises(ValueError):audit_row(r,self.data,self.spec)
    def test_simultaneous_total_mutation(self):
        r=copy.deepcopy(self.row);r['model_end_usdc']='11';r['model_net_pnl_usdc']='1'
        with self.assertRaises(ValueError):audit_row(r,self.data,self.spec)
    def test_drawdown_mutation(self):
        r=copy.deepcopy(self.row);r['model_max_drawdown_pct']='100'
        with self.assertRaises(ValueError):audit_row(r,self.data,self.spec)
    def test_signal_mutation(self):
        r=copy.deepcopy(self.row);r['trades'][0]['signal_atr']='999'
        with self.assertRaises(ValueError):audit_row(r,self.data,self.spec)
    def test_price_mutation(self):
        r=copy.deepcopy(self.row);r['trades'][0]['entry']='2'
        with self.assertRaises(ValueError):audit_row(r,self.data,self.spec)
    def test_unresolved_funding_blocks_result(self):
        r=copy.deepcopy(self.row);r['pending_funding']=[{'reason':'missing'}]
        with self.assertRaises(ValueError):audit_row(r,self.data,self.spec)
    def test_no_trades_rate_undefined(self):
        m=metrics([])
        self.assertIsNone(m['net_win_rate']);self.assertFalse(m['observed_55'])
    def test_flat_trades_not_excluded(self):
        trade=dict(gross_pnl='0',entry_fee='0',exit_fee='0',funding_events=[],closed_ms=1000)
        m=metrics([trade]);self.assertEqual(m['flat'],1);self.assertEqual(m['net_win_rate'],0)
    def test_win_rate_not_return(self):
        trade=dict(gross_pnl='0.0001',entry_fee='0',exit_fee='0',funding_events=[],closed_ms=1000)
        m=metrics([trade]);self.assertTrue(m['observed_60']);self.assertFalse(m['return_ge_5pct'])
    def test_one_winner_has_wide_wilson(self):
        lo,hi=wilson(1,1)
        self.assertLess(lo,.55);self.assertEqual(hi,1)
    def test_wilson_55_of_100_not_proof_of_55(self):
        self.assertLess(wilson(55,100)[0],.55)
    def test_invalid_counts(self):
        with self.assertRaises(ValueError):wilson(4,3)
    def test_fixed_cost_keeps_same_trades(self):
        original=self.row['trades'];stress=fixed_trade_stress(original)
        self.assertEqual(len(original),len(stress))
        for a,b in zip(original,stress):
            self.assertEqual((a['qty'],a['opened_ms'],a['closed_ms']), (b['qty'],b['opened_ms'],b['closed_ms']))
            self.assertEqual(a['funding_events'],b['funding_events'])
        self.assertLess(D(metrics(stress)['net_usdc']),D(metrics(original)['net_usdc']))
    def test_redownload_overlap(self):
        self.assertEqual(overlap_check(self.data,data(900))['candles'],680)
    def test_redownload_price_difference(self):
        fresh=data(900);fresh['candles'][300]['c']='999'
        with self.assertRaises(ValueError):overlap_check(self.data,fresh)
    def test_redownload_funding_difference(self):
        fresh=data(900);fresh['funding'][2]['rate']='.02'
        with self.assertRaises(ValueError):overlap_check(self.data,fresh)
    def test_redownload_network_difference(self):
        fresh=data(900);fresh['network']='testnet'
        with self.assertRaises(ValueError):overlap_check(self.data,fresh)
    def test_duplicate_json_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.json';p.write_text('{"a":1,"a":2}')
            with self.assertRaises(ValueError):load(p)
    def test_nan_comparison_refused(self):
        with self.assertRaises((ValueError,ArithmeticError)):equal('NaN','1','test')


class PipelineTests(unittest.TestCase):
    def test_artificial_pipeline_seals_selection_before_fetch(self):
        old,fresh=data(),data(900)
        saved=[]
        for spec in s.candidates()[:2]:
            for cost in bt.COSTS:
                for path in bt.PATHS:
                    r=bt.replay(old,120,680,spec['fast'],spec['slow'],'both',cost,path)
                    r['window']='full';saved.append(r)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run'
            def fetch(client):
                self.assertTrue((out/'selection.json').is_file())
                self.assertTrue((out/'protocol.json').is_file())
                self.assertEqual(len(load(out/'development.json')),56)
                return fresh
            with patch.object(runner,'baseline_check',return_value=(old,{'results':saved},{'status':'SYNTHETIC_TEST_ONLY'})), \
                 patch.object(runner,'fetch_dataset',side_effect=fetch), \
                 patch.object(runner,'raw_check',return_value={'status':'SYNTHETIC_TEST_ONLY'}),redirect_stdout(io.StringIO()):
                self.assertEqual(runner.run(Path(d),Path(d),out),0)
            summary=load(out/'SUMMARY.json')
            self.assertEqual(summary['development_scenarios'],56)
            self.assertEqual(summary['evaluation_scenarios'],12)
            self.assertEqual(summary['new_bars'],220)
            self.assertFalse(summary['research_gate_55'])
            self.assertEqual(summary['evidence'],'INSUFFICIENT_SAMPLE')
            self.assertEqual(len(load(out/'all-results.json')),68)
            selected=load(out/'selection.json')['strategy']['id']
            self.assertEqual(summary['selected'],selected)
            for row in load(out/'evaluation-results.json'):
                assigned=sum(f['metrics']['closed_trades'] for f in row['reporting_folds'])
                self.assertEqual(assigned,row['closed_trades'])
            with self.assertRaises(FileExistsError):runner.run(Path(d),Path(d),out)


if __name__=='__main__':unittest.main()
