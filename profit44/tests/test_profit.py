"""Artificial fixtures only; software checks are not market-return evidence."""
import copy
from dataclasses import replace
from decimal import Decimal as D
import json
import math
from pathlib import Path
import tempfile
import unittest

from paperlab import engine as core
from paperlab.common import Config
from paperlab.backtest import assumed_quote
from inputs44 import aggregate, blocks_for
import signals as prior
import signals_profit as sig
import verify_profit as ref
import run_profit as run
from risk_engine import ProfitEngine


def spec(entry='EARLY',stop='STRUCTURE',reward='2.5',room='NONE'):
    return next(s for s in sig.specs() if (s['entry'],s['stop'],s['reward'],s['room'])==(entry,stop,reward,room))


def feature(i=120,**kw):
    values=dict(bar_ms=i*300000-1,close=D(109),atr=D(1),o=D(108),h=D('109.5'),l=D(107),prev=D(108),
                upper=D(110),lower=D(99),previous_upper=D(110),previous_lower=D(99),swing_low=D(100),swing_high=D(110),
                lines={'9':[D(106),D(107)],'21':[D(105),D(106)],'20':[D(106),D(107)],'50':[D(105),D(106)]})
    values.update({k:D(str(v)) for k,v in kw.items()})
    return values


def setup():
    return {120:feature(h='111.0',close='110.5',o='110',l='109'),
            121:feature(121,h='105.7',l='105.2',close='105.4',o='106',prev='110.5'),
            122:feature(122,h='106.4',l='105.3',close='106.2',o='105.6',prev='105.4')}


def mirror(fv):
    result={}
    for i,f in fv.items():
        g=copy.deepcopy(f)
        for key in ('close','prev','o'):g[key]=D(220)-f[key]
        for a,b in (('h','l'),('upper','lower'),('previous_upper','previous_lower'),('swing_high','swing_low')):
            g[a],g[b]=D(220)-f[b],D(220)-f[a]
        g['lines']={k:[D(220)-v for v in a] for k,a in f['lines'].items()}
        result[i]=g
    return result


def artificial(n=2880):
    start=1735689600000;bars=[]
    def price(i):return 2000+14*math.sin(i/51)+5*math.sin(i/7)
    for i in range(n):
        o,c=price(i-1),price(i)
        bars.append(dict(t=start+i*60000,T=start+(i+1)*60000-1,o=str(o),h=str(max(o,c)+.7),l=str(min(o,c)-.7),c=str(c),v='1',n=10))
    return dict(id='synthetic',days=['2025-01-01','2025-01-02'],candles=bars,signals=aggregate(bars),
                funding=[dict(time=t+76,rate='0.0000125') for t in range(start,start+n*60000,3600000)],
                metadata=dict(sz_decimals=4,max_leverage=25),start=start+600*60000,end=start+n*60000)


class SignalTests(unittest.TestCase):
    def test_24_unique(self):self.assertEqual((len(sig.specs()),len({s['id'] for s in sig.specs()})),(24,24))
    def test_primary_predeclared(self):self.assertEqual(run.protocol()['primary'],'EARLY_STRUCTURE_R2.5_ROOM')
    def test_early_not_boundary(self):
        self.assertEqual(sig.decisions(setup(),spec())[122]['direction'],1)
        self.assertEqual(sig.decisions(setup(),spec(entry='BOUNDARY'))[122]['direction'],0)
    def test_boundary_later(self):
        f=setup();f[122]['close']=D(111)
        self.assertEqual(sig.decisions(f,spec(entry='BOUNDARY'))[122]['direction'],1)
    def test_short_mirror(self):self.assertEqual(sig.decisions(mirror(setup()),spec())[122]['direction'],-1)
    def test_not_touch_bar(self):self.assertEqual(sig.decisions(setup(),spec())[121]['direction'],0)
    def test_no_rebound(self):
        f=setup();f[122]['close']=D('105.6');self.assertEqual(sig.decisions(f,spec())[122]['direction'],0)
    def test_reverse_body_rejected(self):
        f=setup();f[122]['o']=D(107);self.assertEqual(sig.decisions(f,spec())[122]['direction'],0)
    def test_no_touch(self):
        f=setup();f[121].update(l=D(108),h=D(109));self.assertEqual(sig.decisions(f,spec())[122]['direction'],0)
    def test_origin_failure(self):
        f=setup();f[121]['close']=D(98);self.assertEqual(sig.decisions(f,spec())[122]['direction'],0)
    def test_ema_opposite(self):
        f=setup();f[122]['lines']['20']=[D(90),D(89)];self.assertEqual(sig.decisions(f,spec())[122]['direction'],0)
    def test_both_sides_not_arm(self):
        f=setup();f[120]['l']=D(98);self.assertEqual(sig.decisions(f,spec())[122]['direction'],0)
    def test_structure_contains_confirmation_wick(self):
        f=setup();f[122]['l']=D(104)
        self.assertEqual(sig.decisions(f,spec())[122]['trace']['structure_low'],'104')
    def test_frozen_anchor_and_extreme(self):
        f=setup();f[122]['swing_low']=D(20);f[122]['h']=D(114)
        t=sig.decisions(f,spec())[122]['trace'];self.assertEqual((t['anchor'],t['extreme']),('100','111.0'))
    def test_touch_expiry(self):
        f=setup();f[122]['close']=D(105);f[123]=copy.deepcopy(f[122]);f[124]=setup()[122]
        self.assertEqual(sig.decisions(f,spec())[124]['direction'],0)
    def test_unknown_spec(self):
        s=spec();s['reward']='100'
        with self.assertRaises(ValueError):sig.decisions(setup(),s)
    def test_all_manual_independent(self):
        for f in (setup(),mirror(setup())):
            for s in sig.specs():ref.check_decisions(sig.decisions(f,s),ref.reference_decisions(f,s))
    def test_risk_variants_share_signals(self):
        decisions=[sig.decisions(setup(),s)[122]['direction'] for s in sig.specs() if s['entry']=='EARLY']
        self.assertEqual(decisions,[1]*12)


class RiskTests(unittest.TestCase):
    def engine(self,s=None,trace=None,cost='base_assumptions'):
        s=s or spec();cs=run.COSTS[cost];cfg=replace(Config(),max_hold_seconds=21600,reward_to_risk=s['reward'],taker_fee=cs['taker_fee'],adverse_slippage_bps=cs['adverse_slippage_bps'])
        tr=dict(structure_low='1990',structure_high='2010',event_atr='2',extreme='2020')
        if trace:tr.update(trace)
        holder=dict(i=120,choice=dict(trace=tr));at=1735689602000
        eng=ProfitEngine(cfg,core.new_state(cfg,at,'SYNTHETIC_TEST'),s,run.MODE,holder)
        quote=assumed_quote(at,'2000',cs['spread_bps'],dict(sz_decimals=4,max_leverage=25))
        signal=core.Signal(1,at-2001,D(2),D(2000),s['id'])
        return eng,quote,signal
    def test_structural_not_tighter(self):
        e,q,s=self.engine();self.assertTrue(e.open_position(q,s));p=e.position['risk_plan']
        self.assertGreaterEqual(D(p['stop_fraction']),D(p['atr_stop_fraction']))
        self.assertLessEqual(D(e.position['stop']),D(p['structural_price']))
    def test_true_atr_kept(self):
        e,q,s=self.engine();e.open_position(q,s);self.assertEqual(D(e.position['signal_atr']),D(2))
    def test_too_wide_rejected(self):
        e,q,s=self.engine(trace={'structure_low':'1900'});self.assertFalse(e.open_position(q,s));self.assertEqual(e.counts['VOLATILITY'],1)
    def test_risk_budget_unchanged(self):
        e,q,s=self.engine();e.open_position(q,s)
        self.assertLessEqual(D(e.position['planned_loss']),D('0.125'));self.assertEqual(e.cfg.risk_fraction_per_trade,'0.0125')
    def test_room_rejects_late_chase(self):
        e,q,s=self.engine(spec(room='ROOM'),trace={'extreme':'2002'});self.assertFalse(e.open_position(q,s));self.assertEqual(e.counts['IMPULSE_ROOM'],1)
    def test_room_permits(self):
        e,q,s=self.engine(spec(room='ROOM'));self.assertTrue(e.open_position(q,s))
    def test_min_notional_and_reserve(self):
        e,q,s=self.engine();e.open_position(q,s);t=e.position
        self.assertGreaterEqual(D(t['qty'])*D(t['entry']),D(10));self.assertLessEqual(D(t['initial_margin_model'])+D(t['entry_fee'])+D('3.5'),D(10))
    def test_halt_preserved(self):
        e,q,s=self.engine();e.state['halt_reason']='CONSECUTIVE_LOSSES';self.assertFalse(e.open_position(q,s))
    def test_single_position(self):
        e,q,s=self.engine();self.assertTrue(e.open_position(q,s));self.assertFalse(e.open_position(q,s));self.assertEqual(len(e.state['trades']),1)
    def test_stale_signal(self):
        e,q,s=self.engine();s=replace(s,bar_ms=s.bar_ms-180000);self.assertFalse(e.open_position(q,s))
    def test_original_cost_gate_not_removed(self):
        e,q,s=self.engine(spec(stop='ATR',reward='1.8'),cost='cost_stress');self.assertFalse(e.open_position(q,s));self.assertEqual(e.counts['COST_GATE'],1)
    def test_rr_does_not_change_size_or_stop(self):
        a=[]
        for reward in ('1.8','2.5','3.0'):
            e,q,s=self.engine(spec(reward=reward));e.open_position(q,s);a.append(e.position)
        self.assertEqual(len({(x['qty'],x['stop'],x['planned_loss']) for x in a}),1)
        self.assertLess(D(a[0]['target']),D(a[-1]['target']))
    def test_target_cap_preserved(self):
        e,q,s=self.engine();e.open_position(q,s);target=D(e.position['target'])
        q2=assumed_quote(q.observed_ms+60000,'2100','1',dict(sz_decimals=4,max_leverage=25))
        e.close_position(q2,'TARGET_OBSERVED_PRICE');self.assertEqual(D(e.state['trades'][0]['exit']),target)
    def test_bad_stop_fill_not_improved(self):
        e,q,s=self.engine();e.open_position(q,s);stop=D(e.position['stop'])
        q2=assumed_quote(q.observed_ms+60000,'1970','1',dict(sz_decimals=4,max_leverage=25))
        e.close_position(q2,'STOP_OBSERVED_PRICE');self.assertLess(D(e.state['trades'][0]['exit']),stop)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block=artificial();cls.fv=prior.features(cls.block['signals']);cls.rf=ref.features_ref(cls.block['signals'])
    def test_independent_features_and_decisions(self):
        ref.check_features(self.fv,self.rf)
        for s in sig.specs():ref.check_decisions(sig.decisions(self.fv,s),ref.reference_decisions(self.rf,s))
    def test_future_prefix_invariance(self):
        bars=copy.deepcopy(self.block['signals'])
        for b in bars[330:]:
            for k in 'ohlc':b[k]=str(D(b[k])*2)
        fv=prior.features(bars)
        for s in (spec(),spec(entry='BOUNDARY')):
            a,b=sig.decisions(fv,s),sig.decisions(self.fv,s)
            self.assertEqual({i:a[i] for i in a if i<=330},{i:b[i] for i in b if i<=330})
    def test_reference_prefix(self):
        for s in (spec(),spec(entry='BOUNDARY')):
            full=ref.reference_decisions(self.rf,s);short=ref.reference_decisions({i:f for i,f in self.rf.items() if i<330},s)
            self.assertEqual(short,{i:full[i] for i in short})
    def test_original_boundary_signals_identical(self):
        old=next(s for s in prior.specs() if s['id']==sig.OLD_ID)
        a=prior.decisions(self.fv,old);b=sig.decisions(self.fv,spec(entry='BOUNDARY'))
        self.assertEqual([r['direction'] for r in a.values()],[r['direction'] for r in b.values()])
    def test_24_synthetic_ledgers(self):
        for s in sig.specs():
            dv=sig.decisions(self.fv,s);rf=ref.reference_decisions(self.rf,s)
            r=run.replay(self.block,s,'base_assumptions','OHLC',self.fv,dv,rf)
            self.assertFalse(r['open_position']);self.assertFalse(r['integrity_warnings']);self.assertEqual(r['initial_usdc'],'10')
    def test_shadow_not_added_to_capital(self):
        self.assertEqual(run.protocol()['account']['initial_balance'],'10');self.assertEqual(run.protocol()['blocks'],27)
    def test_group_coverage(self):self.assertEqual(len({s['id'] for g in range(4) for s in sig.specs()[g::4]}),24)
    def test_missing_group_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):run.combine(Path(t),Path(t)/'out')
    def test_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(FileExistsError):run.run(0,Path(t),Path(t))
    def test_original_source_has_no_mutation(self):
        self.assertTrue(run.protocol()['no_orders']);self.assertTrue(run.protocol()['no_network'])
    def test_gap_not_bridged(self):self.assertEqual([len(x) for x in blocks_for(['2026-05-09','2026-05-13','2026-05-14'])],[1,2])
    def test_baseline_corruption_rejected(self):
        with self.assertRaises(ValueError):run.baseline_equal({'ending_usdc':'11'},{'ending_usdc':'10'})


class AuditMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block=artificial();cls.fv=prior.features(cls.block['signals']);rf=ref.features_ref(cls.block['signals'])
        cls.spec=spec(entry='BOUNDARY',stop='ATR',reward='1.8')
        cls.dv=sig.decisions(cls.fv,cls.spec);cls.ref=ref.reference_decisions(rf,cls.spec)
        cls.row=run.replay(cls.block,cls.spec,'base_assumptions','OHLC',cls.fv,cls.dv,cls.ref)
        assert cls.row['trades'], 'Synthetic mutation test must contain trades'
    def changed(self,field,value):
        row=copy.deepcopy(self.row);row['trades'][0][field]=value
        with self.assertRaises(ValueError):ref.audit(row,self.block,self.ref)
    def test_fee_mutation(self):self.changed('entry_fee','1')
    def test_quantity_mutation(self):self.changed('qty','2')
    def test_stop_mutation(self):self.changed('stop','1')
    def test_target_mutation(self):self.changed('target','999999')
    def test_atr_spoof_rejected(self):self.changed('signal_atr','0.0001')
    def test_risk_plan_mutation(self):
        p=copy.deepcopy(self.row['trades'][0]['risk_plan']);p['stop_fraction']='0.0001';self.changed('risk_plan',p)
    def test_structure_trace_mutation(self):
        p=copy.deepcopy(self.row['trades'][0]['pattern_trace']);p['structure_low']='1';self.changed('pattern_trace',p)
    def test_balance_mutation(self):
        row=copy.deepcopy(self.row);row['ending_usdc']='99'
        with self.assertRaises(ValueError):ref.audit(row,self.block,self.ref)
    def test_funding_mutation(self):
        row=copy.deepcopy(self.row)
        found=False
        for t in row['trades']:
            if t['funding_events']:
                t['funding_events'][0]['amount']='1';found=True;break
        self.assertTrue(found,'Artificial fixture must hold across funding')
        with self.assertRaises(ValueError):ref.audit(row,self.block,self.ref)
    def test_earlier_exit_checked(self):
        row=copy.deepcopy(self.row);row['trades'][0]['closed_ms']+=19000
        with self.assertRaises(ValueError):ref.audit(row,self.block,self.ref)


if __name__=='__main__':unittest.main()
