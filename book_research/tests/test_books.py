"""Synthetic software fixtures only: never investment performance evidence."""
import copy
from dataclasses import asdict
from decimal import Decimal as D
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from paperlab import engine as core, backtest as bt
from test_backtest import fixture
import book_strategies as s
import book_verify as v
import run_books as run
import strategies as previous
from statistics_report import metrics


def data(n=680):
    d=bt.validate_dataset(fixture(n))
    d.update(network='mainnet', downloaded_ms=d['candles'][-1]['T']+5000)
    return d


def trade_data(n=680):
    result=data(n)
    price=D(3000)
    for i,bar in enumerate(result['candles']):
        opening=price
        price += D(2) if i%120<85 else D(-1)
        bar.update(o=str(opening),c=str(price),h=str(max(opening,price)+D('.05')),
                   l=str(min(opening,price)-D('.05')))
    result['source']='SYNTHETIC_RAMP_PULLBACK_FOR_BRANCH_COVERAGE_NOT_MARKET_DATA'
    return result


class IndicatorTests(unittest.TestCase):
    def test_grid_count(self):
        self.assertEqual(len(s.candidates()),8)
        self.assertEqual(len({p['id'] for p in s.candidates()+s.controls()}),11)
    def test_rsi_up(self):self.assertEqual(s.rsi2(list(map(D,range(10,30)))),100)
    def test_rsi_down(self):self.assertEqual(s.rsi2(list(map(D,range(30,10,-1)))),0)
    def test_rsi_flat(self):self.assertEqual(s.rsi2([D(3)]*10),50)
    def test_rsi_short(self):self.assertIsNone(s.rsi2([D(1),D(2)]))
    def test_rsi_seed(self):self.assertEqual(s.rsi2([D(3),D(5),D(4)]),D(200)/3)
    def test_rsi_wilder(self):self.assertEqual(s.rsi2([D(3),D(5),D(4),D(6)]),D(600)/7)
    def test_stochastic_flat(self):self.assertEqual(s.stochastic5([dict(h='3',l='3',c='3')]*5),50)
    def test_stochastic_bounds(self):
        for c,n in [('3',0),('5',100)]:
            self.assertEqual(s.stochastic5([dict(h='5',l='3',c=c)]*5),n)
    def test_stochastic_short(self):self.assertIsNone(s.stochastic5([]))
    def test_only_complete5(self):
        bars=data()['candles'][:119]
        self.assertEqual(len(s.complete_five(bars)),23)
        other=copy.deepcopy(bars);other[-1]['c']='999999'
        self.assertEqual(s.complete_five(bars),s.complete_five(other))
    def test_missing5(self):
        bars=data()['candles'][:120];del bars[4]
        self.assertEqual(len(s.complete_five(bars)),23)
    def test_wrong_close5(self):
        bars=data()['candles'][:120];bars[4]['T']+=1
        self.assertEqual(len(s.complete_five(bars)),23)
    def test_bad_history_length(self):
        with self.assertRaises(ValueError):s.features(data()['candles'][:119],s.candidates()[0])
    def test_rsi_fresh_boundary(self):
        bars=data()['candles']
        self.assertTrue(s.features(bars[:120],s.candidates()[0])['fresh5'])
        self.assertFalse(s.features(bars[1:121],s.candidates()[0])['fresh5'])
    def test_causal_prefix(self):
        d=data();before=d['candles'][:120]
        expected=[s.features(before,p) for p in s.candidates()]
        d['candles'][120]['c']='99999'
        self.assertEqual(expected,[s.features(d['candles'][:120],p) for p in s.candidates()])
    def test_reference_random_agreement(self):
        rng=random.Random(42);bars=data()['candles']
        for j in range(125,650,7):
            history=bars[j-120:j]
            # Independent formulas, including directional exits, on synthetic varying prices.
            for spec in s.candidates():
                f=s.features(history,spec)
                self.assertEqual(s.entry(f,spec),v.reference(history,spec))
                for direction in (-1,1):
                    self.assertEqual(s.exit_signal(f,direction,spec),v.reference(history,spec,direction))
    def test_long_rsi_entry(self):
        f=dict(fresh5=True,rsi=D(4),mean5=D(105),last5=D(103),close=D(103),mean=D(100))
        self.assertEqual(s.entry(f,s.candidates()[0]),1)
    def test_short_rsi_entry(self):
        f=dict(fresh5=True,rsi=D(96),mean5=D(95),last5=D(97),close=D(97),mean=D(100))
        self.assertEqual(s.entry(f,s.candidates()[0]),-1)
    def test_stale5_no_entry(self):
        f=dict(fresh5=False,rsi=D(1),mean5=D(105),last5=D(103),close=D(103),mean=D(100))
        self.assertEqual(s.entry(f,s.candidates()[0]),0)
    def test_no_countertrend_rsi(self):
        f=dict(fresh5=True,rsi=D(1),mean5=D(105),last5=D(103),close=D(103),mean=D(110))
        self.assertEqual(s.entry(f,s.candidates()[0]),0)
    def test_elder_requires_confirmation(self):
        p=s.candidates()[4]; f=dict(trend=1,k=D(40),prior_k=[D(10)]*3,above=False,below=False)
        self.assertEqual(s.entry(f,p),0);f['above']=True;self.assertEqual(s.entry(f,p),1)
    def test_elder_short(self):
        p=s.candidates()[4];f=dict(trend=-1,k=D(40),prior_k=[D(90)]*3,above=False,below=True)
        self.assertEqual(s.entry(f,p),-1)
    def test_elder_needs_setup(self):
        p=s.candidates()[4];f=dict(trend=1,k=D(70),prior_k=[D(50)]*3,above=True,below=False)
        self.assertEqual(s.entry(f,p),0)
    def test_rsi_exit_strength(self):
        p=s.candidates()[0];f=dict(fresh5=True,rsi=D(71),last5=D(100),mean5=D(100))
        self.assertTrue(s.exit_signal(f,1,p));self.assertFalse(s.exit_signal(f,-1,p))
    def test_no_stale5_exit(self):
        p=s.candidates()[0];f=dict(fresh5=False,rsi=D(99),last5=D(103),mean5=D(102))
        self.assertFalse(s.exit_signal(f,1,p))


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d=trade_data()
        cls.rows=[s.replay(cls.d,120,680,p,'base_assumptions','OHLC') for p in s.candidates()]
    def test_all_book_rows_audit(self):
        for p,r in zip(s.candidates(),self.rows):self.assertEqual(v.audit(r,self.d,p)['book_entry_and_signal_exits'],'PASS')
    def test_unknown_grid_rejected(self):
        p={**s.candidates()[0],'threshold':2}
        with self.assertRaises(ValueError):s.replay(self.d,120,680,p,'base_assumptions','OHLC')
    def test_original_controls_exact(self):
        for p in s.controls():
            a=previous.replay(self.d,120,680,p,'base_assumptions','OHLC')
            b=s.replay(self.d,120,680,p,'base_assumptions','OHLC')
            self.assertEqual(a,b)
    def test_hook_restored(self):
        before=(bt.DirectionEngine,core.strategy_signal)
        with self.assertRaises(RuntimeError):
            with s.installed(s.candidates()[0],__import__('collections').Counter()):raise RuntimeError('synthetic')
        self.assertEqual(before,(bt.DirectionEngine,core.strategy_signal))
    def test_verifier_restored(self):
        old=v.v.signal_check
        bad=copy.deepcopy(self.rows[0]);bad['pending_funding']=[{}]
        with self.assertRaises(ValueError):v.audit(bad,self.d,s.candidates()[0])
        self.assertIs(v.v.signal_check,old)
    def test_trade_bearing_fixture_exists(self):self.assertTrue(any(r['trades'] for r in self.rows))
    def test_mutated_book_fee_refused(self):
        index=next(i for i,r in enumerate(self.rows) if r['trades'])
        r=copy.deepcopy(self.rows[index]);r['trades'][0]['entry_fee']='0'
        with self.assertRaises(ValueError):v.audit(r,self.d,s.candidates()[index])
    def test_mutated_book_direction_refused(self):
        index=next(i for i,r in enumerate(self.rows) if r['trades'])
        r=copy.deepcopy(self.rows[index]);r['trades'][0]['direction']*=-1
        with self.assertRaises(ValueError):v.audit(r,self.d,s.candidates()[index])
    def test_future_ohlc_does_not_change_past_trades(self):
        d=copy.deepcopy(self.d);cut=500
        for b in d['candles'][cut:]:
            for k in 'ohlc':b[k]=str(D(b[k])*D('1.1'))
        for spec in s.candidates()[::2]:
            a=s.replay(self.d,120,cut,spec,'base_assumptions','OHLC')
            b=s.replay(d,120,cut,spec,'base_assumptions','OHLC')
            self.assertEqual(a['trades'],b['trades'])
    def test_risk_stop_priority_over_book_exit(self):
        p=s.candidates()[0];at=self.d['candles'][120]['t']+2000
        with s.installed(p,__import__('collections').Counter()):
            e=bt.DirectionEngine(core.Config(),core.new_state(core.Config(),at,'SYNTHETIC_TEST'),'both')
            quote=bt.assumed_quote(at,'3000','1',self.d['metadata'])
            self.assertTrue(e.open_position(quote,core.Signal(1,at-2001,D(2),D(3000),'fixture')))
            e.warm_started=True;e.state['last_quote_ms']=at+59000
            with patch.object(s,'exit_signal',return_value=True):
                e.tick(bt.assumed_quote(at+60000,'2990','1',self.d['metadata']),self.d['candles'][:120])
            self.assertEqual(e.state['trades'][0]['close_reason'],'STOP_OBSERVED_PRICE')
    def test_limits_unchanged(self):
        conf=run.protocol()['account']
        self.assertEqual(conf,asdict(core.Config()))
        self.assertEqual(conf['max_consecutive_losses'],3)
        self.assertEqual(conf['risk_fraction_per_trade'],'0.0125')


class PipelineTests(unittest.TestCase):
    def test_old_tail_not_unseen(self):
        self.assertEqual(run.KNOWN_CUTOFF,1789836000000)
        self.assertGreater(run.KNOWN_CUTOFF,1789832460000)
    def test_known_hash_anchor(self):self.assertEqual(len(run.KNOWN_DATA_HASH),64)
    def test_source_scope_disclosed(self):
        self.assertIn('not complete',run.protocol()['source_scope'])
        self.assertEqual(len(run.protocol()['sources']),6)
    def test_network_merge_refuses_mismatch(self):
        a,b=data(),data(700);b['network']='testnet'
        with self.assertRaises(ValueError):run.merge_data(a,b)
    def test_merge_detects_changed_data(self):
        a,b=data(),data(700);b['candles'][100]['c']='100'
        with self.assertRaises(ValueError):run.merge_data(a,b)
    def test_merged_hole_refused(self):
        a,b=data(),data(700);b['candles']=b['candles'][682:]
        with self.assertRaises(ValueError):run.merge_data(a,b)
    def test_no_trades_not_zero_win_rate(self):
        rows=[dict(closed_trades=0,model_net_pnl_usdc='0',evidence_status='INSUFFICIENT_SAMPLE',metrics=metrics([]),cost='cost_stress',path='OHLC',model_max_drawdown_pct='0',halt_reason='')]*4
        a=run.aggregate(rows);self.assertEqual(a['sign'],'NO_TRADES');self.assertFalse(a['research_gate_55'])
    def test_all_pipeline_sealed_before_fetch(self):
        old,fresh=data(),data(800)
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'experiment'
            def fetch(client):
                choice=json.loads((out/'selection.json').read_text())
                self.assertIn(choice['strategy'],s.candidates())
                self.assertTrue((out/'protocol.json').is_file())
                self.assertEqual(len(json.loads((out/'development.json').read_text())),44)
                return fresh
            with patch.object(run,'known_data',return_value=(old,{'SYNTHETIC_FIXTURE':True})), \
                 patch.object(run,'KNOWN_CUTOFF',old['candles'][-1]['T']+1), \
                 patch.object(run,'fetch_dataset',side_effect=fetch), \
                 patch.object(run.v,'raw_check',return_value={'SYNTHETIC_FIXTURE':True}),redirect_stdout(io.StringIO()):
                self.assertEqual(run.run(Path(td),Path(td),out),0)
            summary=json.loads((out/'SUMMARY.json').read_text())
            rows=json.loads((out/'all-results.json').read_text())
            self.assertEqual(len(rows),60)
            self.assertEqual(summary['new_bars'],120)
            self.assertFalse(summary['primary']['research_gate_55'])
            self.assertTrue((out/'all_scenarios.csv').is_file())
            self.assertTrue((out/'evidence-manifest.json').is_file())
            for r in rows:
                if r['phase']=='NEW_PERIOD_EVALUATION':self.assertGreaterEqual(r['start_ms'],old['candles'][-1]['T']+1)
            with self.assertRaises(FileExistsError):run.run(Path(td),Path(td),out)
    def test_failure_does_not_make_summary(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'failure'
            with patch.object(run,'known_data',side_effect=ValueError('synthetic broken provenance')),redirect_stdout(io.StringIO()):
                self.assertEqual(run.run(Path(td),Path(td),out),2)
            self.assertFalse((out/'SUMMARY.json').exists())
            self.assertEqual(json.loads((out/'status.json').read_text())['status'],'FAILED_NO_VALIDATED_RESULT')


if __name__=='__main__':unittest.main()
