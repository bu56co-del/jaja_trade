"""Artificial fixtures and causal/account regressions; not historical profitability."""
import sys,copy,tempfile,unittest
from pathlib import Path
from decimal import Decimal as D
from dataclasses import asdict
from unittest.mock import patch
sys.path.append(str(Path(__file__).parents[2]/'fixedrisk/tests'))
import test_fixed as fixture
import path_execution as p
import run_exitmodel as r
import reference_exitmodel as v


def opened(model='CROSS_1000MS'):
    cfg=r.old.configuration('RISK125',fixture.COST);h={'i':630,'choice':{'trace':{}}}
    e=p.CrossingFixed(cfg,r.mean.core.new_state(cfg,fixture.TS,'SYNTHETIC_ONLY'),r.old.SPEC,r.mean.MODE,h,execution_model=model)
    h['engine']=e;q=r.mean.assumed_quote(fixture.TS,'100',fixture.COST['spread_bps'],fixture.META)
    sg=r.mean.core.Signal(-1,fixture.TS-2001,D('.4'),D(100),'fixture')
    assert e.open_position(q,sg)
    return e,q


class CrossingTests(unittest.TestCase):
    def trade(self):return {'direction':-1,'stop':'101','target':'99'}
    def test_fixed_models(self):self.assertEqual(p.MODELS,{'SAMPLED':None,'CROSS_0MS':0,'CROSS_1000MS':1000,'CROSS_5000MS':5000})
    def test_linear_endpoints(self):
        self.assertEqual(p.interpolate(2000,2000,100,21000,102),100);self.assertEqual(p.interpolate(21000,2000,100,21000,102),102)
    def test_linear_midpoint(self):self.assertEqual(p.interpolate(11500,2000,100,21000,102),101)
    def test_no_gap_interpolation(self):
        with self.assertRaises(ValueError):p.interpolate(60000,59998,100,62000,110)
    def test_no_before_segment(self):
        with self.assertRaises(ValueError):p.interpolate(1,2000,100,21000,110)
    def test_nan_rejected(self):
        with self.assertRaises(ValueError):p.interpolate(2000,2000,'NaN',21000,110)
    def test_stop_first(self):self.assertEqual(p.first_cross(2000,100,21000,102,self.trade(),D(1)),(11500,'STOP_OBSERVED_PRICE'))
    def test_target_first(self):self.assertEqual(p.first_cross(2000,100,21000,98,self.trade(),D(1)),(11500,'TARGET_OBSERVED_PRICE'))
    def test_flat_inside(self):self.assertIsNone(p.first_cross(2000,100,21000,100,self.trade(),D(1)))
    def test_no_hit(self):self.assertIsNone(p.first_cross(2000,100,21000,'100.9',self.trade(),D(1)))
    def test_millisecond_rounds_forward(self):self.assertEqual(p.first_cross(2000,100,21000,103,self.trade(),D(1))[0],8334)
    def test_endpoint_touch_counts(self):self.assertEqual(p.first_cross(2000,100,21000,101,self.trade(),D(1))[0],21000)
    def test_execution_factor_changes_trigger(self):
        self.assertLess(p.first_cross(2000,100,21000,102,self.trade(),D('1.001'))[0],11500)
    def test_long_unsupported(self):
        with self.assertRaises(ValueError):p.first_cross(2000,100,21000,102,dict(self.trade(),direction=1),D(1))
    def test_independent_binary_grid(self):
        for x in ('98','99.9','100','100.1','102'):
            for y in ('97','99','100','101','103'):
                # Only arm crossing search when initial quote inside the band.
                for factor in (D(1),D('1.000150005')):
                    if not D(99)<D(x)*factor<D(101):continue
                    a=p.first_cross(2000,D(x),21000,D(y),self.trade(),factor)
                    b=v.crossing(2000,D(x),21000,D(y),D(101),D(99),factor)
                    self.assertEqual(a,b)


class PendingTests(unittest.TestCase):
    def test_latched_only_once(self):
        e,q=opened();e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE')
        e.arm(q.observed_ms+1,'TARGET_OBSERVED_PRICE',q.mid,'FIXTURE');self.assertEqual(len(e.trigger_log),1)
    def test_no_fill_before_due(self):
        e,q=opened();e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE');self.assertFalse(e.execute_pending(q))
    def test_recross_does_not_cancel(self):
        e,q=opened();e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE')
        later=r.mean.assumed_quote(q.observed_ms+1000,'99.9',fixture.COST['spread_bps'],fixture.META)
        self.assertTrue(e.execute_pending(later));self.assertIsNone(e.position);self.assertEqual(e.trigger_log[0]['status'],'FILLED')
    def test_risk_preempts(self):
        e,q=opened();e.arm(q.observed_ms,'TARGET_OBSERVED_PRICE',q.mid,'FIXTURE')
        bad=r.mean.assumed_quote(q.observed_ms+10,'105',fixture.COST['spread_bps'],fixture.META)
        e.tick(bad);self.assertEqual(e.trigger_log[0]['status'],'PREEMPTED');self.assertIsNone(e.pending_exit)
        self.assertEqual(e.state['trades'][0]['close_reason'],'ACCOUNT_DRAWDOWN_TRIGGER')
    def test_tp_favorable_cap(self):
        e,q=opened('CROSS_0MS');target=e.position['target'];e.arm(q.observed_ms,'TARGET_OBSERVED_PRICE',q.mid,'FIXTURE')
        better=r.mean.assumed_quote(q.observed_ms,'95',fixture.COST['spread_bps'],fixture.META);e.execute_pending(better)
        self.assertEqual(e.state['trades'][0]['exit'],target)
    def test_tp_unfavorable_not_replaced_with_target(self):
        e,q=opened('CROSS_0MS');target=D(e.position['target']);e.arm(q.observed_ms,'TARGET_OBSERVED_PRICE',q.mid,'FIXTURE');e.execute_pending(q)
        self.assertGreater(D(e.state['trades'][0]['exit']),target)
    def test_double_fill_refused(self):
        e,q=opened('CROSS_0MS');e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE');e.execute_pending(q)
        self.assertFalse(e.execute_pending(q))
    def test_no_arm_without_position(self):
        e,q=opened();e.close_position(q,'MAX_HOLD_TIME')
        with self.assertRaises(ValueError):e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE')
    def test_stale_trade_refused(self):
        e,q=opened('CROSS_0MS');e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE');e.pending_exit['trade_id']=999
        with self.assertRaises(ValueError):e.execute_pending(q)
    def test_due_in_unobserved_gap_waits_for_available_quote(self):
        e,q=opened();ts=q.observed_ms//60000*60000+59990
        e.arm(ts,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE')
        nextopen=r.mean.assumed_quote(q.observed_ms//60000*60000+62000,'101',fixture.COST['spread_bps'],fixture.META)
        self.assertTrue(e.execute_pending(nextopen))
        self.assertGreater(e.trigger_log[0]['executed_ms'],e.trigger_log[0]['due_ms'])
    def test_pending_survives_intermediate_quote(self):
        e,q=opened('CROSS_5000MS');e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE')
        mid=r.mean.assumed_quote(q.observed_ms+1000,'100.1',fixture.COST['spread_bps'],fixture.META)
        e.tick(mid);self.assertIsNotNone(e.position);self.assertIsNotNone(e.pending_exit)
    def test_segment_end_cancels_pending(self):
        e,q=opened();e.arm(q.observed_ms,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE');e.close_position(q,'BACKTEST_SEGMENT_END_ASSUMED_FILL')
        self.assertEqual(e.trigger_log[0]['status'],'PREEMPTED');self.assertFalse(e.execute_pending(q))
    def test_time_exit_cancels_pending(self):
        e,q=opened();e.arm(q.observed_ms,'TARGET_OBSERVED_PRICE',q.mid,'FIXTURE');e.close_position(q,'MAX_HOLD_TIME')
        self.assertEqual(e.trigger_log[0]['actual_reason'],'MAX_HOLD_TIME');self.assertIsNone(e.pending_exit)
    def test_funding_during_delay_is_charged(self):
        e,q=opened();ts=(q.observed_ms//3600000+1)*3600000+1
        e.ingest_funding([{'time':ts,'rate':'0.0001'}])
        class Prices:
            def oracle_near(self,t,tolerance):return D(100),ts-1,'PRECEDING_CANDLE_CLOSE_PROXY_NOT_ORACLE'
        e.arm(ts-500,'STOP_OBSERVED_PRICE',q.mid,'FIXTURE')
        e.reconcile_funding(Prices(),ts+500)
        later=r.mean.assumed_quote(ts+500,'100.2',fixture.COST['spread_bps'],fixture.META);e.execute_pending(later)
        self.assertEqual(len(e.state['trades'][0]['funding_events']),1)
        self.assertEqual(e.state['trades'][0]['funding_events'][0]['time'],ts)
    def test_original_config_unchanged(self):
        e,q=opened();self.assertEqual(asdict(e.cfg),asdict(r.old.configuration('RISK125',fixture.COST)))


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.AccountTests.setUpClass();cls.block=copy.deepcopy(fixture.AccountTests.block)
    def row(self,model='CROSS_1000MS'):
        cs,ref,_=r.old.prepare(self.block,'base_assumptions')
        return r.replay(self.block,'RISK125','base_assumptions','OHLC',model,cs,ref),ref
    def test_all_frozen_models_accounts(self):
        n=0
        for c in r.mean.COSTS:
            cs,ref,_=r.old.prepare(self.block,c)
            for profile in r.old.PROFILES:
                for model in p.MODELS:
                    for path in r.mean.PATHS:
                        x=r.replay(self.block,profile,c,path,model,cs,ref);n+=len(x['trades'])
                        if model!='SAMPLED':v.audit(x,self.block,ref)
                        else:self.assertEqual(x,r.old.replay(self.block,profile,c,path,cs,ref))
        self.assertGreater(n,0)
    def test_cash_tamper(self):
        x,ref=self.row();x['ending_usdc']='999'
        with self.assertRaises(ValueError):v.audit(x,self.block,ref)
    def test_missing_trade(self):
        x,ref=self.row();self.assertTrue(x['trades']);x['trades'].pop()
        with self.assertRaises(ValueError):v.audit(x,self.block,ref)
    def test_wrong_stop(self):
        x,ref=self.row();x['trades'][0]['stop']='999'
        with self.assertRaises(ValueError):v.audit(x,self.block,ref)
    def test_wrong_time(self):
        x,ref=self.row();x['trades'][0]['closed_ms']+=1
        with self.assertRaises(ValueError):v.audit(x,self.block,ref)
    def test_missing_attempt(self):
        x,ref=self.row();x['sizing_attempts']=[]
        with self.assertRaises(ValueError):v.audit(x,self.block,ref)
    def test_wrong_peak(self):
        x,ref=self.row();x['sampled_max_drawdown_pct']='0'
        with self.assertRaises(ValueError):v.audit(x,self.block,ref)
    def test_config_tamper(self):
        x,ref=self.row();x['account_config']['account_halt_drawdown']='.99'
        with self.assertRaises(ValueError):v.audit(x,self.block,ref)
    def test_unknown_model(self):
        cs,ref,_=r.old.prepare(self.block,'base_assumptions')
        with self.assertRaises(ValueError):r.replay(self.block,'RISK125','base_assumptions','OHLC','BEST_LOOKING',cs,ref)
    def test_parent_control_changed_balance_fails(self):
        x,ref=self.row('SAMPLED');saved=copy.deepcopy(x);saved.update(dataset='A44',official_zero_volume_fill_events=0);saved['ending_usdc']='99'
        with self.assertRaises(ValueError):r.check_control(x,saved,'A44')
    def test_missing_grid(self):
        with self.assertRaises(ValueError):r.aggregate([])
    def test_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):r.run(Path(d),Path(d))
    def test_no_summary_on_failed_load(self):
        with tempfile.TemporaryDirectory() as d,patch.object(r,'load',side_effect=ValueError('fixture')):
            out=Path(d)/'out'
            with self.assertRaises(ValueError):r.run(Path(d),out)
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'evidence-manifest.json').exists())
    def test_future_data_does_not_change_entries(self):
        b=copy.deepcopy(self.block)
        for bar in b['candles'][900:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        cs1,ref1,_=r.old.prepare(self.block,'base_assumptions');cs2,ref2,_=r.old.prepare(b,'base_assumptions')
        x=r.replay(self.block,'RISK125','base_assumptions','OHLC','CROSS_1000MS',cs1,ref1)
        y=r.replay(b,'RISK125','base_assumptions','OHLC','CROSS_1000MS',cs2,ref2)
        cut=b['candles'][900]['t']
        self.assertEqual([t for t in x['trades'] if t['closed_ms']<cut],[t for t in y['trades'] if t['closed_ms']<cut])

if __name__=='__main__':unittest.main()
