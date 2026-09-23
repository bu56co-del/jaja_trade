"""Artificial policy, distance, causality and accounting tests, not market evidence."""
import copy, json, sys, tempfile, unittest
from pathlib import Path
from dataclasses import replace, asdict
from decimal import Decimal as D
from unittest.mock import patch
sys.path.append(str(Path(__file__).parents[2]/'leverage1020/tests'))
import test_high as fixtures
import run_tight as r
import reference_tight as v
from exit_config import configuration, exit_values, PROFILES
from paperlab.common import Config, ConfigError
from paperlab import engine as core

COST=r.mean.COSTS['base_assumptions']; META=fixtures.META; TS=fixtures.TS


def engine(profile='BOTH_HALF', mode='EXPOSURE5_WHATIF', atr='.4', cost=COST):
    cfg=configuration(mode,profile,cost);h={'i':630,'choice':{'trace':{}}}
    e=r.ExposureEngine(cfg,core.new_state(cfg,TS,'ARTIFICIAL_ONLY'),r.parent.filters.BASE_SPEC,r.mean.MODE,h)
    h['engine']=e;q=r.mean.assumed_quote(TS,'100',cost['spread_bps'],META)
    return e,q,core.Signal(-1,TS-2001,D(atr),D(100),'fixture')


class PolicyTests(unittest.TestCase):
    def test_five_profiles(self):self.assertEqual(len(PROFILES),5)
    def test_parent_config_unchanged(self):
        for m in r.MODES:
            self.assertEqual(asdict(configuration(m,'BASE',COST)),asdict(r.prior.configuration(m,COST)))
    def test_all_profile_configs(self):
        for m in r.MODES:
            for p in PROFILES:
                for c in r.mean.COSTS.values():configuration(m,p,c).validate()
    def test_exit_factors(self):
        for p in PROFILES:
            for raw in ('0.001','0.3','0.5','0.666666','1'):
                original,stop,target=v.distances(D(raw),D(100),p)
                cfg=configuration(r.MODES[0],p,COST)
                actual=max(D(cfg.stop_floor_fraction),D(raw)/100*D(cfg.atr_multiplier))
                self.assertEqual(actual,stop);self.assertEqual(actual*D(cfg.reward_to_risk),target)
    def test_quarter_floor_is_not_old_floor(self):self.assertEqual(D(exit_values('BOTH_QUARTER')['stop_floor_fraction']),D('.00075'))
    def test_tp_only_does_not_tighten_stop(self):self.assertEqual(D(exit_values('TP_HALF')['atr_multiplier']),D('1.5'))
    def test_sl_only_does_not_tighten_target(self):
        _,s,t=v.distances('.4',100,'SL_HALF');self.assertEqual((s,t),(D('.003'),D('.0108')))
    def test_unknown_profile(self):
        with self.assertRaises(ConfigError):configuration(r.MODES[0],'BEST_FROM_FUTURE',COST)
    def test_unknown_level(self):
        with self.assertRaises(ConfigError):configuration('EXPOSURE100','BOTH_HALF',COST)
    def test_no_account_halt_change(self):
        with self.assertRaises(ConfigError):replace(configuration(r.MODES[0],'BOTH_HALF',COST),account_halt_drawdown='.50').validate()
    def test_no_risk_increase(self):
        with self.assertRaises(ConfigError):replace(configuration(r.MODES[0],'BOTH_HALF',COST),risk_fraction_per_trade='.5').validate()
    def test_no_arbitrary_exit(self):
        with self.assertRaises(ConfigError):replace(configuration(r.MODES[0],'BOTH_HALF',COST),reward_to_risk='10').validate()
    def test_no_live_mode(self):
        with self.assertRaises(ConfigError):replace(configuration(r.MODES[0],'BOTH_HALF',COST),mode='live').validate()
    def test_no_fee_discount(self):
        with self.assertRaises(ConfigError):replace(configuration(r.MODES[0],'BOTH_HALF',COST),taker_fee='0').validate()
    def test_original_default_preserved(self):
        self.assertEqual(Config().stop_floor_fraction,'0.003');self.assertEqual(Config().reward_to_risk,'1.8')
    def test_sizing_does_not_increase_for_tighter_exits(self):
        for m in r.MODES:
            sizes=[]
            for p in PROFILES:
                e,q,s=engine(p,m,atr='.6');self.assertTrue(e.open_position(q,s));sizes.append(e.position['qty'])
            self.assertEqual(len(set(sizes)),1)
    def test_tighter_planned_risk(self):
        a,q,s=engine('BASE',atr='.6');b,qb,sb=engine('BOTH_HALF',atr='.6')
        a.open_position(q,s);b.open_position(qb,sb)
        self.assertLess(D(b.position['planned_loss']),D(a.position['planned_loss']))
    def test_unscaled_volatility_ceiling_retained(self):
        for p in PROFILES:
            e,q,s=engine(p,atr='1');self.assertFalse(e.open_position(q,s))
            self.assertIn('VOLATILITY',e.attempts[-1]['rejected_by'])
    def test_tiny_target_cost_refusal(self):
        e,q,s=engine('BOTH_QUARTER');self.assertFalse(e.open_position(q,s))
        self.assertIn('COST_GATE',e.attempts[-1]['rejected_by'])
    def test_target_equals_cost_boundary_allowed(self):
        e,q,s=engine('BOTH_HALF');self.assertTrue(e.open_position(q,replace(s,atr=D('.0036')*100/(D('1.5')*D('.9')))))
    def test_halt_not_bypassed(self):
        e,q,s=engine();e.halt(TS,'TEST');self.assertFalse(e.open_position(q,s))
    def test_stop_tighter_than_parent(self):
        a,q,s=engine('BASE');b,qb,sb=engine('BOTH_HALF');a.open_position(q,s);b.open_position(qb,sb)
        follow=r.mean.assumed_quote(TS+21000,'100.32',COST['spread_bps'],META)
        a.tick(follow);b.tick(follow)
        self.assertIsNotNone(a.position);self.assertIsNone(b.position)
        self.assertEqual(b.state['trades'][0]['close_reason'],'STOP_OBSERVED_PRICE')
    def test_target_tighter_than_parent(self):
        a,q,s=engine('BASE');b,qb,sb=engine('BOTH_HALF');a.open_position(q,s);b.open_position(qb,sb)
        follow=r.mean.assumed_quote(TS+21000,'99.40',COST['spread_bps'],META)
        a.tick(follow);b.tick(follow)
        self.assertIsNotNone(a.position);self.assertIsNone(b.position)
        self.assertEqual(b.state['trades'][0]['close_reason'],'TARGET_OBSERVED_PRICE')
    def test_dd_priority_kept(self):
        e,q,s=engine('BOTH_HALF',r.MODES[2]);e.open_position(q,s)
        e.tick(r.mean.assumed_quote(TS+21000,'100.4',COST['spread_bps'],META))
        self.assertEqual(e.state['trades'][0]['close_reason'],'ACCOUNT_DRAWDOWN_TRIGGER')
    def test_large_gap_not_certified(self):
        e,q,s=engine('BOTH_HALF',r.MODES[2]);e.open_position(q,s)
        e.tick(r.mean.assumed_quote(TS+21000,'110',COST['spread_bps'],META))
        self.assertGreater(e.margin_summary()['breaches'],0)
        self.assertTrue(e.state['integrity_warnings'])


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AuditTests.setUpClass();cls.block=copy.deepcopy(fixtures.AuditTests.block)
    def row(self):
        cs,ref=r.prepare(self.block,'base_assumptions')
        return r.replay(self.block,r.MODES[0],'SL_HALF','base_assumptions','OHLC',cs,ref),ref
    def test_all_60_profile_level_cost_paths(self):
        trades=0
        for c in r.mean.COSTS:
            cs,ref=r.prepare(self.block,c)
            for m in r.MODES:
                for p in PROFILES:
                    for path in r.mean.PATHS:
                        row=r.replay(self.block,m,p,c,path,cs,ref);trades+=len(row['trades'])
                        self.assertFalse(row['open_position']);self.assertFalse(row['integrity_warnings'])
        self.assertGreater(trades,0)
    def test_original_exits_exact_regression(self):
        for c in r.mean.COSTS:
            cs,ref=r.prepare(self.block,c)
            for m in r.MODES:
                for path in r.mean.PATHS:
                    r.prior.baseline_check(r.replay(self.block,m,'BASE',c,path,cs,ref),r.prior.replay(self.block,m,c,path,cs,ref))
    def test_target_tamper(self):
        row,ref=self.row();self.assertTrue(row['trades']);row['trades'][0]['target']='1'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_stop_tamper(self):
        row,ref=self.row();row['trades'][0]['stop']='999'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_policy_tamper(self):
        row,ref=self.row();row['account_config']['atr_multiplier']='1.5'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_missing_trade(self):
        row,ref=self.row();row['trades']=[]
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_partial_grid_rejected(self):
        with self.assertRaises(ValueError):r.aggregate([])
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):r.run(Path(d),Path(d))
    def test_failures_preserved_no_headline(self):
        with tempfile.TemporaryDirectory() as d,patch.object(r,'load',side_effect=ValueError('artificial_failure')):
            out=Path(d)/'out'
            with self.assertRaises(ValueError):r.run(Path(d),out)
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'evidence-manifest.json').exists())

if __name__=='__main__':unittest.main()
