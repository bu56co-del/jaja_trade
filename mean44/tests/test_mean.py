"""Artificial fixtures only; none of these returns are market evidence."""
import copy
from decimal import Decimal as D
from pathlib import Path
import sys,tempfile,unittest,json,gzip,hashlib
from unittest.mock import patch
sys.path.append(str(Path(__file__).parents[2]/'reversal44/tests'))
from test_patterns import artificial
import signals_mean as s
import verify_mean as v
import run_mean as run
from paperlab import engine as core


def spec(family='BB_REENTRY',minutes=5,filt='NONE',exit='SMA5'):
    return next(x for x in s.specs() if (x['family'],x['minutes'],x['filter'],x['exit'])==(family,minutes,filt,exit))


def node(**kw):
    out=dict(prior_close=D(97),prior_lower=D(98),prior_upper=D(102),lower=D(98),upper=D(102),close=D(99),open=D(98),
             prior_rsi=D(8),rsi=D(20),er=D('.2'),sma5=D(100))
    out.update(kw);return out


class FormulaTests(unittest.TestCase):
    def test_grid(self):self.assertEqual((len(s.specs()),len({x['id'] for x in s.specs()})),(25,25))
    def test_primary(self):self.assertEqual(s.PRIMARY,'BB_REENTRY_15_RANGE_SMA5')
    def test_flat_rsi(self):self.assertEqual(s.relative([D(1)]*40)[-1],50)
    def test_up_rsi(self):self.assertEqual(s.relative(list(map(D,range(40))))[-1],100)
    def test_down_rsi(self):self.assertEqual(s.relative(list(map(D,range(40,0,-1))))[-1],0)
    def test_flat_band(self):self.assertEqual(s.band([D(3)]*40),(3,3))
    def test_population_band(self):self.assertEqual(s.band([D(1),D(3)]*10),(0,4))
    def test_bb_reentry_long(self):self.assertEqual(s.entry(node(),spec()),1)
    def test_bb_reject_long_past_opposite_band(self):self.assertEqual(s.entry(node(close=D(103),open=D(98)),spec()),0)
    def test_bb_reject_short_past_opposite_band(self):self.assertEqual(s.entry(node(prior_close=D(103),close=D(97),open=D(102)),spec()),0)
    def test_bb_not_recovered(self):self.assertEqual(s.entry(node(close=D(97)),spec()),0)
    def test_bb_prior_inside(self):self.assertEqual(s.entry(node(prior_close=D(99)),spec()),0)
    def test_bb_short(self):self.assertEqual(s.entry(node(prior_close=D(103),close=D(101),open=D(102)),spec()),-1)
    def test_body_disagree(self):self.assertEqual(s.entry(node(open=D(100)),spec()),0)
    def test_range_reject(self):self.assertEqual(s.entry(node(er=D('.31')),spec(filt='RANGE')),0)
    def test_range_boundary(self):self.assertEqual(s.entry(node(er=D('.30')),spec(filt='RANGE')),1)
    def test_rsi_recovery_long(self):self.assertEqual(s.entry(node(),spec(family='RSI_RECOVERY')),1)
    def test_rsi_still_low(self):self.assertEqual(s.entry(node(rsi=D(9)),spec(family='RSI_RECOVERY')),0)
    def test_rsi_was_not_extreme(self):self.assertEqual(s.entry(node(prior_rsi=D(12)),spec(family='RSI_RECOVERY')),0)
    def test_rsi_short(self):self.assertEqual(s.entry(node(prior_rsi=D(95),rsi=D(85),close=D(101),open=D(102)),spec(family='RSI_RECOVERY')),-1)
    def test_no_dynamic_exit(self):self.assertFalse(s.exits(node(),'HARD',1))
    def test_ma_exit_long(self):self.assertTrue(s.exits(node(close=D(101)),'SMA5',1))
    def test_ma_exit_short(self):self.assertTrue(s.exits(node(),'SMA5',-1))
    def test_ma_equality_not_exit(self):self.assertFalse(s.exits(node(close=D(100)),'SMA5',1))
    def test_rsi_exit_long(self):self.assertTrue(s.exits(node(rsi=D(80)),'RSI70',1))
    def test_rsi_exit_short(self):self.assertTrue(s.exits(node(rsi=D(20)),'RSI70',-1))
    def test_unknown_exit(self):
        with self.assertRaises(ValueError):s.exits(node(),'BAD',1)
    def test_unsupported_timeframe(self):
        with self.assertRaises(ValueError):s.features({},7)


class CausalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b=artificial(1200);cls.fs={m:s.features(cls.b,m) for m in (5,15)}
    def test_reference_all_candidates(self):
        for sp in s.specs()[1:]:v.check(s.choices(self.b,self.fs[sp['minutes']],sp),v.reference(self.b,sp))
    def test_same_warmup(self):
        for m in (5,15):self.assertEqual(min(self.fs[m]),600)
    def test_signal_clock(self):
        for sp in s.specs()[1:]:
            for j,c in s.choices(self.b,self.fs[sp['minutes']],sp).items():
                if c['direction'] or c['exit_long'] or c['exit_short']:self.assertEqual(j%sp['minutes'],0)
    def test_future_mutation(self):
        b=copy.deepcopy(self.b)
        for bar in b['candles'][900:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        for m in (5,15):
            new=s.features(b,m)
            for j in self.fs[m]:
                if j<=900:self.assertEqual(new[j],self.fs[m][j])
    def test_prefix_equivalence(self):
        b=copy.deepcopy(self.b);b['candles']=b['candles'][:900]
        for m in (5,15):
            ff=s.features(b,m);self.assertEqual(ff,{k:self.fs[m][k] for k in ff})
    def test_entry_independent_of_exit_style(self):
        for fam in ('BB_REENTRY','RSI_RECOVERY'):
            sets=[]
            for ex in ('HARD','SMA5','RSI70'):
                sp=spec(family=fam,exit=ex);cs=s.choices(self.b,self.fs[5],sp);sets.append([(k,v['direction']) for k,v in cs.items()])
            self.assertEqual(sets[0],sets[1]);self.assertEqual(sets[1],sets[2])
    def test_bad_direction_detected(self):
        sp=spec();a=s.choices(self.b,self.fs[5],sp);b=copy.deepcopy(a);b[600]['direction']=7
        with self.assertRaises(ValueError):v.check(a,b)
    def test_bad_exit_detected(self):
        sp=spec();a=s.choices(self.b,self.fs[5],sp);b=copy.deepcopy(a);b[600]['exit_long']=not b[600]['exit_long']
        with self.assertRaises(ValueError):v.check(a,b)
    def test_restore_hook_after_error(self):
        old=core.strategy_signal
        with self.assertRaises(RuntimeError):
            with run.installed({}):raise RuntimeError('artificial interruption')
        self.assertIs(old,core.strategy_signal)
    def test_real_engine_all_new_variants(self):
        self.rows=[]
        for sp in s.specs()[1:]:
            cs=s.choices(self.b,self.fs[sp['minutes']],sp);ref=v.reference(self.b,sp)
            r=run.replay(self.b,sp,'base_assumptions','OHLC',cs,ref);self.rows.append(r)
            self.assertEqual(r['initial_usdc'],'10');self.assertFalse(r['open_position'])
        self.assertGreater(sum(len(r['trades']) for r in self.rows),0)
    def test_account_corruption_rejected(self):
        sp=spec(family='RSI_RECOVERY');cs=s.choices(self.b,self.fs[5],sp);ref=v.reference(self.b,sp)
        r=run.replay(self.b,sp,'base_assumptions','OHLC',cs,ref);self.assertTrue(r['trades'])
        bad=copy.deepcopy(r);bad['ending_usdc']='999'
        with self.assertRaises(ValueError):v.audit(bad,self.b,ref)
        bad=copy.deepcopy(r);bad['trades'][0]['entry_fee']='0'
        with self.assertRaises(ValueError):v.audit(bad,self.b,ref)
    def test_exit_reason_corruption_rejected(self):
        sp=spec(family='RSI_RECOVERY');cs=s.choices(self.b,self.fs[5],sp);ref=v.reference(self.b,sp)
        r=run.replay(self.b,sp,'base_assumptions','OHLC',cs,ref)
        r['trades'][0]['close_reason']='NOT_REAL'
        with self.assertRaises(ValueError):v.audit(r,self.b,ref)
    def test_missing_groups_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):run.combine(Path(d),Path(d)/'out')
    def test_no_output_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):run.run(0,Path(d),Path(d))
    def test_stale_baseline_detected(self):
        a={'trades':[],'ending_usdc':'10','halt_reason':None,'halt_ms':None,'sampled_max_drawdown_pct':'0','metrics':{}}
        b=copy.deepcopy(a);b['ending_usdc']='9'
        with self.assertRaises(ValueError):run.verify_baseline(a,b)

if __name__=='__main__':unittest.main()
