"""Artificial fixtures; never interpreted as historical profitability evidence."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from dataclasses import replace,asdict
from decimal import Decimal as D
from unittest.mock import patch
sys.path.append(str(Path(__file__).parents[2]/'entry44/tests'))
import test_entry as previous_tests
from risk_high import configuration,ExposureEngine,affordable_quantity,MODES,LEVELS
import run_high as r
import reference_high as v
from paperlab.common import Config,ConfigError,DataError
from paperlab import engine as core

COST=r.mean.COSTS['base_assumptions'];META={'sz_decimals':4,'max_leverage':25};TS=1760000042000

def engine(mode='EXPOSURE20_WHATIF',price='100',cost=COST):
    cfg=configuration(mode,cost);h={'i':630,'choice':{'trace':{}}}
    e=ExposureEngine(cfg,core.new_state(cfg,TS,'ARTIFICIAL_TEST'),r.parent.filters.BASE_SPEC,r.mean.MODE,h);h['engine']=e
    q=r.mean.assumed_quote(TS,price,cost['spread_bps'],META)
    sig=core.Signal(-1,TS-2001,D(price)*D('.004'),D(price),'fixture')
    return e,q,sig

class PolicyTests(unittest.TestCase):
    def test_levels(self):self.assertEqual(list(LEVELS.values()),[5,10,20])
    def test_default_remains_low_risk(self):
        for n in (5,10,20):
            with self.assertRaises(ConfigError):replace(Config(),leverage_for_margin=n).validate()
    def test_fixed_configurations(self):
        for m in MODES:
            for c in r.mean.COSTS.values():
                cfg=configuration(m,c);cfg.validate()
                self.assertEqual(D(cfg.risk_fraction_per_trade),D('.0125')*LEVELS[m])
                self.assertEqual(cfg.account_halt_drawdown,'0.05')
    def test_no_other_leverage(self):
        with self.assertRaises(ConfigError):replace(configuration(MODES[0],COST),leverage_for_margin=10).validate()
    def test_no_halt_relax(self):
        with self.assertRaises(ConfigError):replace(configuration(MODES[2],COST),account_halt_drawdown='.1').validate()
    def test_no_risk_relax(self):
        with self.assertRaises(ConfigError):replace(configuration(MODES[2],COST),risk_fraction_per_trade='.5').validate()
    def test_no_minimum_bypass(self):
        with self.assertRaises(ConfigError):replace(configuration(MODES[2],COST),min_open_notional='1').validate()
    def test_no_live(self):
        with self.assertRaises(ConfigError):replace(configuration(MODES[2],COST),mode='live').validate()
    def test_unknown_mode(self):
        with self.assertRaises(ConfigError):configuration('EXPOSURE100_WHATIF',COST)
    def test_5_config_matches_old(self):
        import risk5
        self.assertEqual(asdict(configuration(MODES[0],COST)),asdict(risk5.configuration(MODES[0],COST)))
    def test_exposure_and_no_unfunded_position(self):
        for m in MODES:
            e,q,s=engine(m);self.assertTrue(e.open_position(q,s));a=e.attempts[-1]
            self.assertLess(D(a['post_entry_mark_exposure']),LEVELS[m])
            self.assertGreater(D(a['post_entry_mark_exposure']),D('.98')*LEVELS[m])
    def test_halted_never_opens(self):
        e,q,s=engine();e.halt(TS,'TEST');self.assertFalse(e.open_position(q,s))
    def test_stale_refused(self):
        e,q,s=engine();self.assertFalse(e.open_position(q,replace(s,bar_ms=TS-120000)))
    def test_single_position(self):
        e,q,s=engine();e.open_position(q,s);self.assertFalse(e.open_position(q,s));self.assertEqual(len(e.state['trades']),1)
    def test_independent_size_grid(self):
        for m in MODES:
            for cn,c in r.mean.COSTS.items():
                for p in ('100','2000','3456.789'):
                    for balance in ('9.5','10','11.123456','20'):
                        e,q,_=engine(m,price=p,cost=c)
                        fee,half,slip=v.COST[cn]
                        self.assertEqual(affordable_quantity(D(balance),q,e.cfg),v.size(D(balance),D(p),fee,half,slip,m,4))
    def test_floor_reserves_and_next_lot_is_not_affordable(self):
        for m in MODES:
            e,q,_=engine(m);qty=affordable_quantity(D(10),q,e.cfg);fee=D(e.cfg.taker_fee)
            ep=core.market_fill(q,-1,D(1),e.cfg).price;xp=core.market_fill(q,1,D(1),e.cfg).price
            unit=q.mark/LEVELS[m]+(q.mark-ep)+fee*(ep+xp)
            self.assertLessEqual(qty*unit,10);self.assertGreater((qty+D('.0001'))*unit,10)
    def test_cash_validation(self):
        e,q,_=engine()
        for n in ('0','-1','NaN','Infinity'):
            with self.assertRaises(DataError):affordable_quantity(D(n),q,e.cfg)
    def test_fee_stress_reduces_size(self):
        for m in MODES:
            a,q,_=engine(m);b,qb,_=engine(m,cost=r.mean.COSTS['cost_stress'])
            self.assertLess(affordable_quantity(D(10),qb,b.cfg),affordable_quantity(D(10),q,a.cfg))
    def test_stop_ceiling_still_refuses(self):
        e,q,s=engine();s=replace(s,atr=D(2))
        self.assertFalse(e.open_position(q,s));self.assertIn('VOLATILITY',e.attempts[-1]['rejected_by'])
    def test_drawdown_retained(self):
        e,q,s=engine();e.open_position(q,s)
        e.tick(r.mean.assumed_quote(TS+21000,'100.3',COST['spread_bps'],META))
        self.assertEqual(e.state['halt_reason'],'ACCOUNT_DRAWDOWN_TRIGGER');self.assertIsNone(e.position)
    def test_maintenance_depends_on_asset_not_selected_leverage(self):
        for m in MODES:
            e,q,s=engine(m);e.open_position(q,s);e.margin_check(q)
            a=e.margin_summary()['minimum_observation']
            self.assertEqual(D(a['maintenance']),D(a['notional'])/50)
    def test_large_gap_flagged_not_certified_safe(self):
        e,q,s=engine();e.open_position(q,s)
        e.tick(r.mean.assumed_quote(TS+21000,'110',COST['spread_bps'],META))
        self.assertGreater(e.margin_summary()['breaches'],0)
        self.assertIn('LIQUIDATION_PATH_NOT_MODELLED',e.state['integrity_warnings'])

class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        previous_tests.AccountTests.setUpClass();cls.block=copy.deepcopy(previous_tests.AccountTests.block)
    def row(self,mode=MODES[0]):
        cs,ref=r.prepare(self.block,'base_assumptions');return r.replay(self.block,mode,'base_assumptions','OHLC',cs,ref),ref
    def test_all_modes_costs_paths(self):
        total=0
        for cost in r.mean.COSTS:
            cs,ref=r.prepare(self.block,cost)
            for mode in MODES:
                for path in r.mean.PATHS:
                    row=r.replay(self.block,mode,cost,path,cs,ref);total+=len(row['trades'])
                    self.assertFalse(row['open_position']);self.assertFalse(row['pending_funding'])
        self.assertGreater(total,0)
    def test_5x_full_equality(self):
        for cost in r.mean.COSTS:
            cs,ref=r.prepare(self.block,cost)
            for path in r.mean.PATHS:
                old=r.prior.replay(self.block,MODES[0],cost,path,cs,ref)
                new=r.replay(self.block,MODES[0],cost,path,cs,ref)
                r.baseline_check(new,old)
    def test_balance_tamper(self):
        row,ref=self.row();row['ending_usdc']='999'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_qty_tamper(self):
        row,ref=self.row();self.assertTrue(row['trades']);row['trades'][0]['qty']='.01'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_margin_tamper(self):
        row,ref=self.row();row['margin_diagnostic']['minimum_buffer_usdc']='999'
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_missing_trade(self):
        row,ref=self.row();row['trades']=[]
        with self.assertRaises(ValueError):v.audit(row,self.block,ref)
    def test_partial_grid_refused(self):
        with self.assertRaises(ValueError):r.aggregate([])
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):r.run(Path(d),Path(d))
    def test_failure_saves_manifest_without_headline(self):
        with tempfile.TemporaryDirectory() as d,patch.object(r,'load',side_effect=ValueError('fixture')):
            out=Path(d)/'out'
            with self.assertRaises(ValueError):r.run(Path(d),out)
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'evidence-manifest.json').exists())

if __name__=='__main__':unittest.main()
