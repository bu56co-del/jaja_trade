"""Synthetic software tests, not extra independent market observations."""
import copy, json, sys, tempfile, unittest
from pathlib import Path
from dataclasses import replace, asdict
from decimal import Decimal as D
from unittest.mock import patch
sys.path.append(str(Path(__file__).parents[2]/'leverage1020/tests'))
import test_high as fixtures
import run_fixed as r
import risk_fixed as s
import reference_fixed as v
from paperlab.common import Config, ConfigError, DataError
from paperlab import engine as core

COST=r.mean.COSTS['base_assumptions'];TS=fixtures.TS;META=fixtures.META


def engine(profile='RISK125',atr='.4'):
    cfg=s.configuration(profile,COST);h={'i':630,'choice':{'trace':{}}}
    e=s.FixedRiskEngine(cfg,core.new_state(cfg,TS,'ARTIFICIAL_ONLY'),r.SPEC,r.mean.MODE,h);h['engine']=e
    q=r.mean.assumed_quote(TS,'100',COST['spread_bps'],META)
    return e,q,core.Signal(-1,TS-2001,D(atr),D(100),'fixture')


def plan(cash=D(10),peak=D(10),stop=D('.006'),decimals=4):
    return s.size_plan(cash,peak,D(100),D('99.9850005'),D('100.0150005'),stop,D('.0012'),D('.00045'),decimals)


class PolicyTests(unittest.TestCase):
    def test_exact_three_profiles(self):self.assertEqual(s.PROFILES,('BASE_B','RISK125','RISK125_TP_HALF'))
    def test_all_configurations(self):
        for p in s.PROFILES:
            for c in r.mean.COSTS.values():s.configuration(p,c).validate()
    def test_baseline_is_original(self):
        cfg=s.configuration('BASE_B',COST)
        self.assertEqual(asdict(cfg),asdict(replace(Config(),max_hold_seconds=21600)))
    def test_parent_defaults_not_changed(self):
        self.assertEqual((Config().max_notional_to_equity,Config().risk_fraction_per_trade),('1.15','0.0125'))
    def test_unknown_profile(self):
        with self.assertRaises(ConfigError):s.configuration('RISK999',COST)
    def test_no_risk_increase(self):
        with self.assertRaises(ConfigError):replace(s.configuration('RISK125',COST),risk_fraction_per_trade='.05').validate()
    def test_no_halt_loosening(self):
        with self.assertRaises(ConfigError):replace(s.configuration('RISK125',COST),account_halt_drawdown='.1').validate()
    def test_no_live_config(self):
        with self.assertRaises(ConfigError):replace(s.configuration('RISK125',COST),mode='live').validate()
    def test_no_fee_discount(self):
        with self.assertRaises(ConfigError):s.configuration('RISK125',dict(COST,taker_fee='0'))
    def test_no_reserve_removal(self):
        with self.assertRaises(ConfigError):replace(s.configuration('RISK125',COST),minimum_cash_reserve='0').validate()
    def test_margin_option_not_exposure(self):
        cfg=s.configuration('RISK125',COST);self.assertEqual((cfg.leverage_for_margin,cfg.max_notional_to_equity),(5,'2'))
    def test_risk_budget_not_multiplied_by_leverage(self):
        q,a=plan();self.assertEqual(D(a['risk_budget']),D('.125'))
    def test_headroom_and_floor(self):
        q,a=plan(cash=D('9.6'));self.assertEqual(D(a['risk_budget']),D('.0904'))
    def test_peak_more_restrictive_than_initial_floor(self):
        q,a=plan(cash=D('10'),peak=D('10.5'));self.assertEqual(D(a['risk_budget']),D('.015'))
    def test_budget_exhausted(self):
        q,a=plan(cash=D('9.5'));self.assertEqual(q,0)
    def test_reserve_exhausted(self):
        q,a=plan(cash=D(3));self.assertEqual(q,0)
    def test_lot_floored(self):
        q,a=plan();raw=min(map(D,a['quantity_caps'].values()))
        self.assertLessEqual(q,raw);self.assertGreater(q+D('.0001'),raw)
    def test_exposure_cap_after_fee(self):
        q,a=plan(stop=D('.003'))
        post=D(10)-q*(D('99.9850005')*D('.00045')+D(100)-D('99.9850005'))
        self.assertLessEqual(q*100/post,2);self.assertIn('POST_ENTRY_EXPOSURE',a['binding_caps'])
    def test_bigger_stop_smaller_size(self):self.assertGreater(plan(stop=D('.003'))[0],plan(stop=D('.009'))[0])
    def test_invalid_numbers(self):
        for cash in (D('NaN'),D('Infinity'),D(0),D(-1)):
            with self.assertRaises(DataError):plan(cash=cash)
    def test_bad_precision(self):
        for x in (-1,9,True):
            with self.assertRaises(DataError):plan(decimals=x)
    def test_reference_size_equivalence(self):
        for cash,peak in ((D(10),D(10)),(D('9.6'),D(10)),(D(10),D('10.5'))):
            q,a=plan(cash,peak)
            other,b=v.reference_plan(cash,peak,D(100),D('.00045'),D('.00005'),D('.0001'),D('.4'),D(100),4,'RISK125')
            self.assertEqual(q,other);self.assertEqual(D(a['risk_budget']),b['risk_budget'])
    def test_tp_half_same_size(self):
        a,q,sg=engine();b,qb,sb=engine('RISK125_TP_HALF')
        self.assertTrue(a.open_position(q,sg));self.assertTrue(b.open_position(qb,sb))
        self.assertEqual(a.position['qty'],b.position['qty']);self.assertEqual(a.position['stop'],b.position['stop'])
        self.assertEqual(D(a.position['entry'])-D(a.position['target']),2*(D(b.position['entry'])-D(b.position['target'])))
    def test_min_notional_refuses(self):
        e,q,sg=engine();e.state['peak_equity']='10.5';self.assertFalse(e.open_position(q,sg))
        self.assertIn('MIN_NOTIONAL',e.attempts[0]['rejected_by'])
    def test_small_tp_keeps_cost_gate(self):
        e,q,sg=engine('RISK125_TP_HALF',atr='.2');self.assertFalse(e.open_position(q,sg))
        self.assertIn('COST_GATE',e.attempts[0]['rejected_by'])
    def test_volatility_ceiling_kept(self):
        e,q,sg=engine(atr='1');self.assertFalse(e.open_position(q,sg));self.assertIn('VOLATILITY',e.attempts[0]['rejected_by'])
    def test_halt_cannot_restart(self):
        e,q,sg=engine();e.halt(TS,'TEST');self.assertFalse(e.open_position(q,sg));self.assertEqual(e.attempts,[])
    def test_only_short(self):
        e,q,sg=engine();self.assertFalse(e.open_position(q,replace(sg,direction=1)))
    def test_bad_signal_time(self):
        e,q,sg=engine();self.assertFalse(e.open_position(q,replace(sg,bar_ms=TS)))
    def test_plan_under_one_point_two_five(self):
        e,q,sg=engine();self.assertTrue(e.open_position(q,sg))
        self.assertLessEqual(D(e.position['planned_loss']),D('.125'))
    def test_gap_not_certified(self):
        e,q,sg=engine();e.open_position(q,sg)
        e.tick(r.mean.assumed_quote(TS+21000,'180',COST['spread_bps'],META))
        self.assertGreater(e.margin_summary()['breaches'],0);self.assertTrue(e.state['integrity_warnings'])


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AuditTests.setUpClass();cls.block=copy.deepcopy(fixtures.AuditTests.block)
    def make_row(self):
        cs,ref,_=r.prepare(self.block,'base_assumptions')
        return r.replay(self.block,'RISK125','base_assumptions','OHLC',cs,ref),ref
    def test_all_twelve_synthetic_accounts(self):
        n=0
        for c in r.mean.COSTS:
            cs,ref,_=r.prepare(self.block,c)
            for p in s.PROFILES:
                for path in r.mean.PATHS:
                    row=r.replay(self.block,p,c,path,cs,ref);n+=len(row['trades'])
                    self.assertFalse(row['open_position']);self.assertFalse(row['integrity_warnings'])
        self.assertGreater(n,0)
    def test_target_tamper(self):
        row,ref=self.make_row();self.assertTrue(row['trades']);row['trades'][0]['target']='1'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_qty_tamper(self):
        row,ref=self.make_row();row['trades'][0]['qty']='100'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_budget_tamper(self):
        row,ref=self.make_row();row['sizing_attempts'][0]['risk_budget']='1'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_attempt_removed(self):
        row,ref=self.make_row();row['sizing_attempts']=[]
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_peak_tamper(self):
        row,ref=self.make_row();row['sizing_attempts'][0]['peak_equity']='20'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_original_account_matches(self):
        cs,ref,_=r.prepare(self.block,'base_assumptions')
        a=r.replay(self.block,'BASE_B','base_assumptions','OHLC',cs,ref)
        b=r.mean.replay(self.block,r.SPEC,'base_assumptions','OHLC',cs,ref);self.assertEqual(a,b)
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):r.run(Path(d),Path(d))
    def test_incomplete_grid(self):
        with self.assertRaises(ValueError):r.aggregate([])
    def test_failed_load_preserved(self):
        with tempfile.TemporaryDirectory() as d,patch.object(r,'load',side_effect=ValueError('fixture')):
            p=Path(d)/'output'
            with self.assertRaises(ValueError):r.run(Path(d),p)
            self.assertFalse((p/'SUMMARY.json').exists());self.assertTrue((p/'evidence-manifest.json').exists())
    def test_future_suffix_cannot_change_signals(self):
        b=copy.deepcopy(self.block)
        for c in b['candles'][900:]:
            for k in 'ohlc':c[k]=str(D(c[k])*2)
        a,_,_=r.prepare(self.block,'base_assumptions');z,_,_=r.prepare(b,'base_assumptions')
        self.assertEqual({k:v for k,v in a.items() if k<900},{k:v for k,v in z.items() if k<900})

if __name__=='__main__':unittest.main()
