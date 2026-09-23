"""Artificial data only. These tests are not market-performance evidence."""
from decimal import Decimal as D
from pathlib import Path
import copy
import json
import math
import tempfile
import unittest
from unittest.mock import patch

import inputs44 as inp
import signals as sg
import reference44 as vr
import run44 as run


def spec(family='FAKE',ratio='none',trend='none'):
    return next(s for s in sg.specs() if s['family']==family and s['ratio']==ratio and s['trend']==trend)


def f(i=120,**kw):
    d=dict(bar_ms=i*300000-1,close=D(105),atr=D(1),o=D(105),h=D(109),l=D(101),prev=D(105),
           upper=D(110),lower=D(100),previous_upper=D(110),previous_lower=D(100),swing_low=D(100),swing_high=D(110),
           lines={str(n):[D(104),D(105)] for n in (9,21,20,50)})
    d.update({k:D(str(v)) if k not in ('lines',) else v for k,v in kw.items()})
    return d


def fakeout(ratio='none',trend='none'):
    x={120:f(h=111,close=104.1,o=110.5,l=103)}
    return sg.decisions(x,spec('FAKE',ratio,trend))


def retest():
    return {120:f(h=111,close=110.5,o=109,l=108),
            121:f(121,h=110.7,close=110.1,o=110.3,l=109.9,prev=110.5),
            122:f(122,h=112,close=111,o=110.2,l=110,prev=110.1)}


def mirror(fv):
    out={}
    for i,x in fv.items():
        y=copy.deepcopy(x)
        for k in ('close','o','prev'):y[k]=D(220)-x[k]
        for a,b in (('h','l'),('upper','lower'),('swing_high','swing_low'),('previous_upper','previous_lower')):
            y[a],y[b]=D(220)-x[b],D(220)-x[a]
        y['lines']={k:[D(220)-v for v in arr] for k,arr in x['lines'].items()};out[i]=y
    return out


def artificial(n=1440):
    start=1735689600000;bars=[]
    def px(i):return 2000+12*math.sin(i/25)+4*math.sin(i/3)
    for i in range(n):
        o,c=px(i-1),px(i)
        bars.append(dict(t=start+i*60000,T=start+(i+1)*60000-1,o=str(o),h=str(max(o,c)+.8),l=str(min(o,c)-.8),c=str(c),v='1',n=10))
    return dict(id='synthetic',days=['2025-01-01'],candles=bars,signals=inp.aggregate(bars),
                funding=[dict(time=t+76,rate='0.0000125') for t in range(start,start+n*60000,3600000)],
                metadata=dict(sz_decimals=4,max_leverage=25),start=start+600*60000,end=start+n*60000)


class PatternTests(unittest.TestCase):
    def test_grid(self):self.assertEqual((len(sg.specs()),len({s['id'] for s in sg.specs()})),(27,27))
    def test_ratio_618_accepts(self):self.assertEqual(fakeout('0.618')[120]['direction'],-1)
    def test_ratio_66_is_deeper(self):self.assertEqual(fakeout('0.66')[120]['direction'],0)
    def test_ratio_50_accepts(self):self.assertEqual(fakeout('0.5')[120]['direction'],-1)
    def test_no_ratio_accepts(self):self.assertEqual(fakeout()[120]['direction'],-1)
    def test_false_low_mirror(self):
        x={120:f(h=111,close=104.1,o=110.5,l=103)}
        self.assertEqual(sg.decisions(mirror(x),spec('FAKE','0.618'))[120]['direction'],1)
    def test_reclaim_required(self):self.assertEqual(sg.decisions({120:f(h=112,close=111,o=110)},spec())[120]['direction'],0)
    def test_reversal_body_required(self):self.assertEqual(sg.decisions({120:f(h=112,close=109,o=108)},spec())[120]['direction'],0)
    def test_both_sides_ambiguous(self):self.assertEqual(sg.decisions({120:f(h=112,l=98,close=109,o=110)},spec())[120]['direction'],0)
    def test_margin(self):self.assertIsNone(sg.make_event(120,f(h=110.05),'FAKE'))
    def test_ema_rejects_counter_direction(self):self.assertEqual(fakeout(trend='9_21')[120]['direction'],0)
    def test_ema_permits_trade_direction(self):
        x=f(h=111,close=104,o=110);x['lines']['9']=[D(107),D(105)];x['lines']['21']=[D(108),D(108)]
        self.assertEqual(sg.decisions({120:x},spec(trend='9_21'))[120]['direction'],-1)
    def test_ema2050(self):
        x=f(h=111,close=104,o=110);x['lines']['20']=[D(107),D(105)];x['lines']['50']=[D(108),D(108)]
        self.assertEqual(sg.decisions({120:x},spec(trend='20_50'))[120]['direction'],-1)
    def test_delayed_failed_close(self):
        x={120:f(h=112,close=111,o=110),121:f(121,close=108,o=110)}
        self.assertEqual(sg.decisions(x,spec())[121]['direction'],-1)
    def test_false_expiry(self):
        x={120:f(h=112,close=111,o=110)}
        for i in range(121,124):x[i]=f(i,h=111,close=110.5,o=110.4,l=109)
        x[124]=f(124,close=108,o=110)
        self.assertEqual(sg.decisions(x,spec())[124]['direction'],0)
    def test_single_event_not_repeated(self):
        x={120:f(h=111,close=109,o=110),121:f(121,close=108,o=109)}
        self.assertEqual(sum(v['direction']!=0 for v in sg.decisions(x,spec()).values()),1)
    def test_retest_confirm_only_later(self):
        d=sg.decisions(retest(),spec('RETEST'));self.assertEqual([d[i]['direction'] for i in d],[0,0,1])
    def test_retest_short_symmetry(self):self.assertEqual(sg.decisions(mirror(retest()),spec('RETEST'))[122]['direction'],-1)
    def test_retest_candle_not_closed_beyond_touch(self):
        x=retest();x[122]['close']=D('110.6');self.assertEqual(sg.decisions(x,spec('RETEST'))[122]['direction'],0)
    def test_retest_regains_old_boundary(self):
        x=retest();x[121].update(h=D('109.8'),l=D('109.7'),close=D('109.75'));x[122].update(close=D('109.9'),o=D('109.8'))
        self.assertEqual(sg.decisions(x,spec('RETEST'))[122]['direction'],0)
    def test_retest_origin_invalidates(self):
        x=retest();x[121].update(close=D(99),l=D(98));self.assertEqual(sg.decisions(x,spec('RETEST'))[122]['direction'],0)
    def test_fib_level_formula(self):
        x=retest();x[121].update(l=D('104.2'),h=D('105'),close=D('104.8'),o=D('104.9'));x[122].update(close=D(111),o=D(105))
        a=sg.decisions(x,spec('RETEST','0.618'))[122]
        self.assertEqual(a['direction'],1);self.assertEqual(D(a['trace']['level']),D('104.202'))
        self.assertEqual(sg.decisions(x,spec('RETEST','0.66'))[122]['direction'],0)
    def test_fib_anchor_frozen(self):
        x=retest();x[121]['swing_low']=D(50)
        d=sg.decisions(x,spec('RETEST'))[122];self.assertEqual(D(d['trace']['anchor']),D(100))
    def test_missing_touch(self):
        x=retest();x[121].update(l=D('111'),h=D(112));self.assertEqual(sg.decisions(x,spec('RETEST'))[122]['direction'],0)
    def test_not_same_bar_retest(self):self.assertEqual(sg.decisions({120:retest()[120]},spec('RETEST'))[120]['direction'],0)
    def test_new_spec_refused(self):
        s=spec();s['new']='x'
        with self.assertRaises(ValueError):sg.decisions({120:f()},s)
    def test_12bar_expiry(self):
        x={120:retest()[120]}
        for i in range(121,133):x[i]=f(i,h=111.2,l=110.8,close=111,o=110.9,prev=111)
        x[133]=retest()[121];x[134]=retest()[122]
        self.assertEqual(sg.decisions(x,spec('RETEST'))[134]['direction'],0)
    def test_reference_manual_cases(self):
        for x in (retest(),mirror(retest()),{120:f(h=111,close=104.1,o=110.5,l=103)}):
            for s in sg.specs():vr.check_decisions(sg.decisions(x,s),vr.reference_decisions(x,s))
    def test_expired_touch_does_not_backfill(self):
        x=retest();x[122].update(h=D(110.5),close=D(110.2),o=D(110.1));x[123]=f(123,h=110.5,close=110.2,o=110.1,l=110.0);x[124]=retest()[122]
        self.assertEqual(sg.decisions(x,spec('RETEST'))[124]['direction'],0)


class DataAndExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block=artificial();cls.fv=sg.features(cls.block['signals']);cls.rf=vr.features_ref(cls.block['signals'])
    def test_independent_features(self):vr.check_features(self.fv,self.rf)
    def test_all_independent_decisions(self):
        for s in sg.specs():vr.check_decisions(sg.decisions(self.fv,s),vr.reference_decisions(self.rf,s))
    def test_future_cannot_change_prefix(self):
        bs=copy.deepcopy(self.block['signals'])
        for b in bs[210:]:
            for k in 'ohlc':b[k]=str(D(b[k])*2)
        changed=sg.features(bs)
        for i in range(120,211):self.assertEqual(changed[i],self.fv[i])
        for s in sg.specs():
            a=sg.decisions(changed,s);b=sg.decisions(self.fv,s)
            for i in range(120,211):self.assertEqual(a[i],b[i])
    def test_reference_no_future_dependency(self):
        part={i:x for i,x in self.rf.items() if i<205}
        for s in sg.specs():
            full=vr.reference_decisions(self.rf,s);prefix=vr.reference_decisions(part,s)
            self.assertEqual(prefix,{i:full[i] for i in part})
    def test_aggregation_volume(self):self.assertEqual(self.block['signals'][0]['v'],'5')
    def test_aggregation_gap_rejected(self):
        bs=copy.deepcopy(self.block['candles'][:10]);bs[3]['t']+=60000
        with self.assertRaises(ValueError):inp.aggregate(bs)
    def test_partial_bar_rejected(self):
        with self.assertRaises(ValueError):inp.aggregate(self.block['candles'][:9])
    def test_gap_days_never_joined(self):self.assertEqual([len(x) for x in inp.blocks_for(['2026-05-09','2026-05-11','2026-05-12'])],[1,2])
    def test_44day_expected_shape(self):
        idx=json.loads((Path(sg.__file__).parent/'dataset_index.json').read_text());self.assertEqual(len(idx['days']),44);self.assertEqual(len(inp.blocks_for(idx['days'])),27)
    def test_core_hooks_restored(self):
        old=run.core.strategy_signal
        with self.assertRaises(ValueError):
            with run.installed({}):raise ValueError('test')
        self.assertIs(run.core.strategy_signal,old)
    def test_risk_and_account_integration(self):
        for s in (spec(),spec('RETEST'),sg.specs()[-1]):
            dv=sg.decisions(self.fv,s);ref=vr.reference_decisions(self.rf,s)
            for cost in run.COSTS:
                for path in run.PATHS:
                    r=run.replay(self.block,s,cost,path,self.fv,dv,ref)
                    self.assertFalse(r['open_position']);self.assertEqual(r['initial_usdc'],'10');self.assertEqual(r['account_config']['max_consecutive_losses'],3)
    def test_fee_mutation_rejected(self):
        s=sg.specs()[-1];dv=sg.decisions(self.fv,s);ref=vr.reference_decisions(self.rf,s)
        r=run.replay(self.block,s,'base_assumptions','OHLC',self.fv,dv,ref)
        self.assertTrue(r['trades']);r['trades'][0]['entry_fee']='9'
        with self.assertRaises(ValueError):vr.audit(r,self.block,ref)
    def test_balance_mutation_rejected(self):
        s=sg.specs()[-1];dv=sg.decisions(self.fv,s);ref=vr.reference_decisions(self.rf,s)
        r=run.replay(self.block,s,'base_assumptions','OHLC',self.fv,dv,ref);r['ending_usdc']='999'
        with self.assertRaises(ValueError):vr.audit(r,self.block,ref)
    def test_missing_group_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):run.combine(Path(d),Path(d)/'out')
    def test_protocol_no_orders(self):self.assertTrue(run.protocol()['no_orders']);self.assertTrue(run.protocol()['no_network'])
    def test_group_grid(self):
        allspec=[s['id'] for g in range(6) for s in sg.specs()[g::6]];self.assertEqual(len(set(allspec)),27)
    def test_atr_feature_tamper(self):
        x=copy.deepcopy(self.fv);x[120]['atr']+=1
        with self.assertRaises(ValueError):vr.check_features(x,self.rf)
    def test_no_trade_is_not_zero_winrate(self):
        s=spec();dv={i:dict(direction=0,trace=None,ema_rejected=False) for i in self.fv};ref={i:dict(direction=0,arm_i=None,touch_i=None) for i in self.fv}
        r=run.replay(self.block,s,'base_assumptions','OHLC',self.fv,dv,ref);self.assertIsNone(r['metrics']['net_win_rate'])


if __name__=='__main__':unittest.main()
