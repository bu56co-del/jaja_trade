"""Artificial fixtures only. Passing these tests says nothing about market profit."""
import copy
from decimal import Decimal as D
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(str(Path(__file__).parents[2]/'reversal44/tests'))
from test_patterns import artificial
from inputs44 import aggregate
import signals_alt as s
import reference_alt as r
import run_alt as run
from paperlab import engine as core
from paperlab.backtest import assumed_quote
from paperlab.common import Config


def spec(family='BB_FADE',n=20,trigger='DIRECT',filt='NONE'):
    return next(v for v in s.specs() if v['family']==family and v['param']==n and v['trigger']==trigger and v['filter']==filt)


def node(**kw):
    x=dict(bar_ms=600*60000-1,close=D(98),prev=D(100),atr=D('.2'),er=D('.2'),rsi14=D(20),rsi2=D(3),rsi2prev=D(30),
           e20=[D(101),D(102)],e50=[D(99),D(100)],volume_ratio=D(2),
           bands={str(n):dict(mean=D(100),lower=D(99),upper=D(101),width=D('.04')) for n in (20,40)},
           prior_bands={str(n):dict(mean=D(100),lower=D(99),upper=D(101),width=D('.02')) for n in (20,40)})
    x.update(kw);return x


class FormulaTests(unittest.TestCase):
    def test_grid_unique(self):self.assertEqual((len(s.specs()),len({x['id'] for x in s.specs()})),(25,25))
    def test_primary_exists(self):self.assertIn(s.PRIMARY,[x['id'] for x in s.specs()])
    def test_constant_rsi(self):self.assertEqual(s.rsi([D(10)]*120,2)[-1],D(50))
    def test_rsi_up(self):self.assertEqual(s.rsi(list(map(D,range(120))),14)[-1],100)
    def test_rsi_down(self):self.assertEqual(s.rsi(list(map(D,range(120,0,-1))),2)[-1],0)
    def test_flat_band(self):self.assertEqual(s.band([D(100)]*40,20)['width'],0)
    def test_population_sigma(self):self.assertEqual(s.band([D(1),D(3)],2)['lower'],0)
    def test_ema_first_seed(self):self.assertEqual(s.ema([D(1),D(3)],3),[D(1),D(2)])
    def test_bb_low_long(self):self.assertEqual(s.raw_direction(node(),spec()),1)
    def test_bb_high_short(self):self.assertEqual(s.raw_direction(node(close=D(102),rsi14=D(80)),spec()),-1)
    def test_band_alone_not_enough(self):self.assertEqual(s.raw_direction(node(rsi14=D(50)),spec()),0)
    def test_rsi_alone_not_enough(self):self.assertEqual(s.raw_direction(node(close=D(100)),spec()),0)
    def test_exact_boundary_no_signal(self):self.assertEqual(s.raw_direction(node(close=D(99)),spec()),0)
    def test_expansion_positive(self):self.assertEqual(s.raw_direction(node(close=D(102),rsi14=D(60)),spec('BB_EXPAND')),1)
    def test_expansion_low_volume(self):self.assertEqual(s.raw_direction(node(close=D(102),rsi14=D(60),volume_ratio=D(1)),spec('BB_EXPAND')),0)
    def test_expansion_width(self):
        n=node(close=D(102),rsi14=D(60));n['bands']['20']['width']=D('.01')
        self.assertEqual(s.raw_direction(n,spec('BB_EXPAND')),0)
    def test_expansion_prior_outside(self):self.assertEqual(s.raw_direction(node(close=D(102),prev=D(102),rsi14=D(60)),spec('BB_EXPAND')),0)
    def test_expansion_ema_reject(self):
        n=node(close=D(102),rsi14=D(60),e20=[D(99),D(98)])
        self.assertEqual(s.raw_direction(n,spec('BB_EXPAND',filt='EMA20_50')),0)
    def test_rsi_dip_threshold(self):self.assertEqual(s.raw_direction(node(),spec('RSI_DIP',5)),1)
    def test_rsi_dip_not_new(self):self.assertEqual(s.raw_direction(node(rsi2prev=D(2)),spec('RSI_DIP',5)),0)
    def test_rsi_dip_short(self):self.assertEqual(s.raw_direction(node(rsi2=D(99),rsi2prev=D(70)),spec('RSI_DIP',5)),-1)
    def test_dip_trend_filter(self):self.assertEqual(s.raw_direction(node(),spec('RSI_DIP',5,filt='EMA50')),0)
    def test_dip_trend_permit(self):self.assertEqual(s.raw_direction(node(close=D(101)),spec('RSI_DIP',5,filt='EMA50')),1)
    def test_unknown_family(self):
        with self.assertRaises(ValueError):s.raw_direction(node(),dict(family='bad',param=20))


class CausalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block=artificial(1200);cls.f=s.features(cls.block['signals']);cls.ref=r.feature_reference(cls.block['signals'])
    def test_independent_features(self):r.close_feature(self.f,self.ref,'All synthetic features')
    def test_all_independent_decisions(self):
        for ss in s.specs()[1:]:r.check_choices(s.decisions(self.block,self.f,ss),r.decision_reference(self.block,self.ref,ss))
    def test_future_prices_not_seen(self):
        b=copy.deepcopy(self.block)
        for bar in b['candles'][1000:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        b['signals']=aggregate(b['candles']);new=s.features(b['signals'])
        for i in self.f:
            if i*5<=1000:self.assertEqual(new[i],self.f[i])
        for ss in s.specs()[1:]:
            a=s.decisions(self.block,self.f,ss);z=s.decisions(b,new,ss)
            for i in a:
                if i<=1000:self.assertEqual(a[i],z[i])
    def test_prefix_equivalence(self):
        b=copy.deepcopy(self.block);b['candles']=b['candles'][:1000];b['signals']=aggregate(b['candles'])
        f=s.features(b['signals'])
        for ss in s.specs()[1:]:
            a=s.decisions(self.block,self.f,ss);short=s.decisions(b,f,ss)
            self.assertEqual(short,{k:a[k] for k in short})
    def test_micro_never_setup_same_bar(self):
        for ss in s.specs()[1:]:
            if ss['trigger']=='MICRO':
                for k,v in s.decisions(self.block,self.f,ss).items():
                    if v['direction']:self.assertTrue(1<=k-v['trace']['setup_minute']<=5)
    def test_no_repeated_event(self):
        for ss in s.specs()[1:]:
            ids=[v['trace']['setup_minute'] for v in s.decisions(self.block,self.f,ss).values() if v['direction']]
            self.assertEqual(len(ids),len(set(ids)))
    def test_refuses_unknown_spec(self):
        ss=spec();ss['param']=21
        with self.assertRaises(ValueError):s.decisions(self.block,self.f,ss)
    def test_independent_mutation_detected(self):
        actual=copy.deepcopy(self.f);actual[120]['atr']+=1
        with self.assertRaises(ValueError):r.close_feature(actual,self.ref,'Tampered')
    def test_hooks_restored_on_error(self):
        original=core.strategy_signal
        with self.assertRaises(RuntimeError):
            with run.installed({}):raise RuntimeError('test')
        self.assertIs(original,core.strategy_signal)
    def test_expired_micro_not_backfilled(self):
        b=artificial(630);fv={i:node(close=D(100),rsi14=D(50)) for i in range(120,126)};fv[120]=node()
        for bar in b['candles'][598:]:bar.update(o='100',h='101',l='99',c='100')
        b['candles'][606].update(o='100',h='103',l='99',c='102')
        a=s.decisions(b,fv,spec(trigger='MICRO'))
        self.assertFalse(any(x['direction'] for x in a.values()))
    def test_micro_next_closed_candle_can_trigger(self):
        b=artificial(630);fv={i:node(close=D(100),rsi14=D(50)) for i in range(120,126)};fv[120]=node()
        for bar in b['candles'][598:]:bar.update(o='100',h='101',l='99',c='100')
        b['candles'][600].update(o='100',h='103',l='99',c='102')
        a=s.decisions(b,fv,spec(trigger='MICRO'))
        self.assertEqual(a[600]['direction'],0);self.assertEqual(a[601]['direction'],1)
        self.assertEqual(sum(x['direction']!=0 for x in a.values()),1)
    def test_fixed_risk_config(self):
        cfg=run.protocol()['account']
        self.assertEqual((cfg['initial_balance'],cfg['risk_fraction_per_trade'],cfg['max_consecutive_losses']),('10','0.0125',3))
    def test_full_trade_ledgers(self):
        count=0
        for ss in s.specs()[1:]:
            a=s.decisions(self.block,self.f,ss);z=r.decision_reference(self.block,self.ref,ss)
            for cost in ('base_assumptions','cost_stress'):
                row=run.replay(self.block,ss,cost,'OHLC',a,z)
                count+=len(row['trades']);self.assertFalse(row['open_position']);self.assertFalse(row['integrity_warnings'])
        self.assertGreater(count,0)
    def test_price_path_olhc(self):
        ss=spec('RSI_DIP',10);a=s.decisions(self.block,self.f,ss);z=r.decision_reference(self.block,self.ref,ss)
        row=run.replay(self.block,ss,'base_assumptions','OLHC',a,z)
        self.assertEqual(row['independent_audit']['trades'],len(row['trades']))
    def test_fee_mutation_refused(self):
        ss=spec('RSI_DIP',10);a=s.decisions(self.block,self.f,ss);z=r.decision_reference(self.block,self.ref,ss)
        row=run.replay(self.block,ss,'base_assumptions','OHLC',a,z);self.assertTrue(row['trades'])
        row['trades'][0]['entry_fee']='1'
        with self.assertRaises(ValueError):r.audit(row,self.block,z)
    def test_trace_mutation_refused(self):
        ss=spec('RSI_DIP',10);a=s.decisions(self.block,self.f,ss);z=r.decision_reference(self.block,self.ref,ss)
        row=run.replay(self.block,ss,'base_assumptions','OHLC',a,z);self.assertTrue(row['trades'])
        row['trades'][0]['pattern_trace']['setup_minute']+=1
        with self.assertRaises(ValueError):r.audit(row,self.block,z)
    def test_room_rejects_near_mean(self):
        cfg=Config();holder={'i':601,'choice':{'trace':{'mean':'2000'}}};eng=run.AlternativeEngine(cfg,core.new_state(cfg,1735689600000,'TEST_ONLY'),spec(filt='ROOM'),run.MODE,holder)
        q=assumed_quote(1735725662000,'2000','1',dict(sz_decimals=4,max_leverage=25))
        sig=core.Signal(1,q.observed_ms-2001,D(1),D(2000),'TEST')
        self.assertFalse(eng.open_position(q,sig));self.assertEqual(eng.counts['MEAN_ROOM'],1)
    def test_incomplete_combine_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):run.combine(Path(td),Path(td)/'out')
    def test_no_output_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):run.run(0,Path(td),Path(td))


if __name__=='__main__':unittest.main()
