"""Artificial fixtures only. No historical-performance assertions or network."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import patch
sys.path.append(str(Path(__file__).parents[2]/'entry44/tests'))
import test_entry as previous_tests
from risk5 import configuration, ResearchConfig, ExposureEngine, affordable_quantity, MODES
import run_leverage as r
import reference5 as v
from paperlab.common import Config, ConfigError, DataError
from paperlab import engine as core

COST=r.mean.COSTS['base_assumptions'];META={'sz_decimals':4,'max_leverage':25};TS=1760000042000


def engine(mode='EXPOSURE5_WHATIF',price='100',cost=COST):
    cfg=configuration(mode,cost);h={'i':630,'choice':{'trace':{}}}
    e=ExposureEngine(cfg,core.new_state(cfg,TS,'ARTIFICIAL_TEST'),r.parent.filters.BASE_SPEC,r.mean.MODE,h);h['engine']=e
    q=r.mean.assumed_quote(TS,price,cost['spread_bps'],META)
    sig=core.Signal(-1,TS-2001,D(price)*D('.004'),D(price),'fixture')
    return e,q,sig


class PolicyTests(unittest.TestCase):
    def test_modes_fixed(self):self.assertEqual(len(MODES),4)
    def test_default_still_refuses_5(self):
        with self.assertRaises(ConfigError):replace(Config(),leverage_for_margin=5).validate()
    def test_all_bounded_configs(self):
        for m in MODES:
            for c in r.mean.COSTS.values():configuration(m,c).validate()
    def test_no20x(self):
        with self.assertRaises(ConfigError):replace(configuration('EXPOSURE5_WHATIF',COST),leverage_for_margin=20).validate()
    def test_no_halt_relax(self):
        with self.assertRaises(ConfigError):replace(configuration('EXPOSURE5_WHATIF',COST),account_halt_drawdown='.1').validate()
    def test_no_risk_relax(self):
        with self.assertRaises(ConfigError):replace(configuration('EXPOSURE5_WHATIF',COST),risk_fraction_per_trade='.1').validate()
    def test_original_limits_not_relaxed(self):
        c=configuration('REQUEST5_ORIGINAL_LIMITS',COST)
        self.assertEqual((c.max_notional_to_equity,c.minimum_cash_reserve,c.risk_fraction_per_trade),('1.15','3.50','0.0125'))
    def test_unknown_mode(self):
        with self.assertRaises(ConfigError):configuration('LIVE',COST)
    def test_no_live(self):
        with self.assertRaises(ConfigError):replace(configuration('EXPOSURE5_WHATIF',COST),mode='live').validate()
    def test_5x_original_limits_reject(self):
        e,q,s=engine('REQUEST5_ORIGINAL_LIMITS');self.assertFalse(e.open_position(q,s))
        self.assertTrue({'NOTIONAL_CAP','MARGIN_RESERVE','RISK_MINIMUM_CONFLICT'}.issubset(e.attempts[0]['rejected_by']))
    def test_full5_budget_passes(self):
        e,q,s=engine();self.assertTrue(e.open_position(q,s));self.assertTrue(D('4.9')<D(e.attempts[0]['post_entry_mark_exposure'])<=5)
    def test_halted_never_opens(self):
        e,q,s=engine();e.halt(TS,'TEST');self.assertFalse(e.open_position(q,s))
    def test_stale_refused(self):
        e,q,s=engine();s=replace(s,bar_ms=TS-120000);self.assertFalse(e.open_position(q,s))
    def test_single_position(self):
        e,q,s=engine();e.open_position(q,s);self.assertFalse(e.open_position(q,s));self.assertEqual(len(e.state['trades']),1)
    def test_same_quantity_lower_initial_margin(self):
        a,q,s=engine('BASE');b,_,_=engine('LEVERAGE5_SAME_SIZE');self.assertTrue(a.open_position(q,s));self.assertTrue(b.open_position(q,s))
        self.assertEqual(a.position['qty'],b.position['qty']);self.assertEqual(D(a.position['initial_margin_model'])*2/5,D(b.position['initial_margin_model']))
    def test_independent_size_grid(self):
        for cost_name,cost in r.mean.COSTS.items():
            for p in ('100','2000','3456.789'):
                for balance in ('9.5','10','11.123456','20'):
                    e,q,_=engine(price=p,cost=cost);a=affordable_quantity(D(balance),q,e.cfg)
                    f,h,s=v.COST[cost_name];b=v.size(D(balance),D(p),f,h,s,'EXPOSURE5_WHATIF',4);self.assertEqual(a,b)
    def test_rounding_and_reserve(self):
        e,q,_=engine();qty=affordable_quantity(D(10),q,e.cfg);fee=D(e.cfg.taker_fee)
        ep=core.market_fill(q,-1,D(1),e.cfg).price;xp=core.market_fill(q,1,D(1),e.cfg).price
        unit=q.mark/5+(q.mark-ep)+fee*(ep+xp)
        self.assertLessEqual(qty*unit,10);self.assertGreater((qty+D('.0001'))*unit,10)
    def test_nonfinite_cash_refused(self):
        e,q,_=engine()
        for x in ('0','-1','NaN','Infinity'):
            with self.assertRaises(DataError):affordable_quantity(D(x),q,e.cfg)
    def test_tiny_cash_cannot_bypass_minimum(self):
        e,q,_=engine();self.assertLess(affordable_quantity(D('.1'),q,e.cfg)*q.mark,10)
    def test_fee_stress_reduces_affordable_size(self):
        a,q,_=engine();b,qb,_=engine(cost=r.mean.COSTS['cost_stress'])
        self.assertLess(affordable_quantity(D(10),qb,b.cfg),affordable_quantity(D(10),q,a.cfg))
    def test_drawdown_halts_large_position(self):
        e,q,s=engine();e.open_position(q,s)
        adverse=r.mean.assumed_quote(TS+21000,'101.2',COST['spread_bps'],META);e.tick(adverse)
        self.assertEqual(e.state['halt_reason'],'ACCOUNT_DRAWDOWN_TRIGGER');self.assertIsNone(e.position)
    def test_large_gap_maintenance_flagged(self):
        e,q,s=engine();e.open_position(q,s)
        gap=r.mean.assumed_quote(TS+21000,'125',COST['spread_bps'],META);e.tick(gap)
        self.assertGreater(e.margin_summary()['breaches'],0);self.assertIn('LIQUIDATION_PATH_NOT_MODELLED',e.state['integrity_warnings'])


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous_tests.AccountTests.setUpClass();cls.block=copy.deepcopy(previous_tests.AccountTests.block)
    def row(self,mode='EXPOSURE5_WHATIF'):
        cs,ref=r.prepare(self.block,'base_assumptions');return r.replay(self.block,mode,'base_assumptions','OHLC',cs,ref),ref
    def test_full_fixture_all_modes_paths_costs(self):
        total=0
        for cost in r.mean.COSTS:
            cs,ref=r.prepare(self.block,cost)
            for mode in MODES:
                for path in r.mean.PATHS:
                    row=r.replay(self.block,mode,cost,path,cs,ref);total+=len(row['trades'])
                    self.assertFalse(row['open_position']);self.assertFalse(row['pending_funding'])
        self.assertGreater(total,0)
    def test_baseline_unchanged(self):
        cs,ref=r.prepare(self.block,'base_assumptions')
        old=r.mean.replay(self.block,r.parent.filters.BASE_SPEC,'base_assumptions','OHLC',cs,ref)
        new=r.replay(self.block,'BASE','base_assumptions','OHLC',cs,ref);r.baseline_check(new,old)
    def test_balance_tamper(self):
        row,ref=self.row();row['ending_usdc']='999'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_qty_tamper(self):
        row,ref=self.row();self.assertTrue(row['trades']);row['trades'][0]['qty']='.01'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_margin_diagnostic_tamper(self):
        row,ref=self.row();row['margin_diagnostic']['minimum_buffer_usdc']='999'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_missing_trade(self):
        row,ref=self.row();row['trades']=[]
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_future_mutation(self):
        b=copy.deepcopy(self.block)
        for bar in b['candles'][900:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        a,ra=r.prepare(self.block,'base_assumptions');c,rc=r.prepare(b,'base_assumptions')
        self.assertEqual({i:t for i,t in a.items() if i<900},{i:t for i,t in c.items() if i<900})
    def test_partial_grid_rejected(self):
        with self.assertRaises(ValueError):r.aggregate([])
    def test_no_output_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):r.run(Path(d),Path(d))
    def test_failure_has_no_summary(self):
        with tempfile.TemporaryDirectory() as d,patch.object(r,'load',side_effect=ValueError('fixture')):
            out=Path(d)/'out'
            with self.assertRaises(ValueError):r.run(Path(d),out)
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'evidence-manifest.json').exists())

if __name__=='__main__':unittest.main()
