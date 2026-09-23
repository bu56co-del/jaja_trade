"""Synthetic regressions, not profitable-market evidence."""
from decimal import Decimal as D
from pathlib import Path
import copy,unittest,tempfile,sys
sys.path.append(str(Path(__file__).parents[2]/'reversal44/tests'))
from test_patterns import artificial
import filters_bb as f
import reference_bb as v
import run_refine as r
import signals_mean as base
import verify_mean as br
import run_direction as direction
import run_mean as m


def short_fixture():
    b=artificial(1200)
    def price(i):
        if i<600:return D(2000)
        if i<615:return D(2000)+D(i-599)*20/15
        if i<630:return D(2020)-D(i-614)
        if i<645:return D(2005)-D(i-629)*20/15
        return D(1985)
    for i,bar in enumerate(b['candles']):
        o,c=price(i-1),price(i)
        bar.update(o=str(o),c=str(c),h=str(max(o,c)+D('.2')),l=str(min(o,c)-D('.2')))
    return b

def spec(trend='NONE',room='NONE',body='NONE'):
    return next(s for s in f.specs() if (s['trend'],s['room'],s['body'])==(trend,room,body))
def node(**kw):
    x=dict(mean=D(98),fast=D(100),slow=D(100),rise1=D(0),rise4=D(0),high=D(102),low=D(98),close=D(100));x.update(kw);return x

class FormulaTests(unittest.TestCase):
    def test_grid(self):self.assertEqual(len({s['id'] for s in f.specs()}),12)
    def test_primary_declared(self):self.assertIn(f.PRIMARY,[s['id'] for s in f.specs()])
    def test_ema_formula(self):
        xs=[D(i)+D('0.3') for i in range(40)]
        for n in (9,21):self.assertLess(abs(f.ema(xs,n)[-1]-v.ma_last(xs,n)),D('1e-20'))
    def test_none_passes(self):self.assertEqual(f.gate(node(),D(1),spec(),D(100),m.COSTS['base_assumptions'])[0],[])
    def test_up_veto(self):self.assertEqual(f.gate(node(fast=D(101),rise4=D('.6')),D(1),spec(trend='UP_VETO'),D(100),m.COSTS['base_assumptions'])[0],['STRONG_UPTREND'])
    def test_half_atr_boundary(self):self.assertFalse(f.gate(node(fast=D(101),rise4=D('.5')),D(1),spec(trend='UP_VETO'),D(100),m.COSTS['base_assumptions'])[0])
    def test_up_veto_needs_both_conditions(self):self.assertFalse(f.gate(node(fast=D(99),rise4=D('.6')),D(1),spec(trend='UP_VETO'),D(100),m.COSTS['base_assumptions'])[0])
    def test_down_requires_level(self):self.assertIn('NOT_DOWNTREND',f.gate(node(fast=D(101)),D(1),spec(trend='DOWN_ONLY'),D(100),m.COSTS['base_assumptions'])[0])
    def test_down_requires_slope(self):self.assertIn('NOT_DOWNTREND',f.gate(node(fast=D(99),rise1=D('.1')),D(1),spec(trend='DOWN_ONLY'),D(100),m.COSTS['base_assumptions'])[0])
    def test_down_boundary(self):self.assertFalse(f.gate(node(),D(1),spec(trend='DOWN_ONLY'),D(100),m.COSTS['base_assumptions'])[0])
    def test_lower_half(self):self.assertIn('WEAK_CLOSE',f.gate(node(close=D(101)),D(1),spec(body='LOWER_HALF'),D(100),m.COSTS['base_assumptions'])[0])
    def test_midpoint_passes(self):self.assertFalse(f.gate(node(),D(1),spec(body='LOWER_HALF'),D(100),m.COSTS['base_assumptions'])[0])
    def test_zero_range_rejected(self):self.assertIn('WEAK_CLOSE',f.gate(node(high=D(100),low=D(100)),D(1),spec(body='LOWER_HALF'),D(100),m.COSTS['base_assumptions'])[0])
    def test_room_negative(self):self.assertIn('INSUFFICIENT_ROOM',f.gate(node(mean=D(101)),D(1),spec(room='ROOM'),D(100),m.COSTS['base_assumptions'])[0])
    def test_room_depends_on_actual_fill(self):
        a=f.gate(node(mean=D('99.5')),D(1),spec(room='ROOM'),D(100),m.COSTS['base_assumptions'])
        b=f.gate(node(mean=D('99.5')),D(1),spec(room='ROOM'),D(100),m.COSTS['cost_stress'])
        self.assertFalse(a[0]);self.assertIn('INSUFFICIENT_ROOM',b[0]);self.assertLess(D(b[1]['entry_fill']),D(a[1]['entry_fill']))
    def test_nonpositive_inputs(self):
        for price,atr in ((0,1),(100,0)):
            with self.assertRaises(ValueError):f.gate(node(),D(atr),spec(),D(price),m.COSTS['base_assumptions'])
    def test_missing_groups_fail(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):r.combine(Path(d),Path(d)/'out')
    def test_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):r.run(0,Path(d),Path(d))
    def test_incomplete_calendar_fail(self):
        with self.assertRaises(ValueError):r.coverage([])

class CausalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b=short_fixture();cls.fx=base.features(cls.b,15);cls.cs=base.choices(cls.b,cls.fx,f.BASE_SPEC)
        cls.ref=br.reference(cls.b,f.BASE_SPEC);cls.extra=f.extra(cls.b)
    def test_all_filters_independent(self):
        for s in f.specs():
            for cost in m.COSTS:
                a,ta=f.choices(self.b,s,m.COSTS[cost],self.cs,self.extra);b,tb=v.reference(self.b,s,cost,self.ref);v.check(a,b,ta,tb)
    def test_none_exactly_old_short(self):
        a,_=f.choices(self.b,spec(),m.COSTS['base_assumptions'],self.cs,self.extra)
        self.assertEqual(a,direction.gated(self.cs,'SHORT_ONLY'))
    def test_no_new_long_or_backfilled_signal(self):
        for s in f.specs():
            cs,_=f.choices(self.b,s,m.COSTS['base_assumptions'],self.cs,self.extra)
            for j,c in cs.items():
                self.assertIn(c['direction'],(-1,0))
                if c['direction']:self.assertEqual((j%15,self.cs[j]['direction']),(0,-1))
    def test_future_features_no_effect(self):
        b=copy.deepcopy(self.b)
        for bar in b['candles'][900:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        changed=f.extra(b)
        self.assertEqual({j:v for j,v in changed.items() if j<=900},{j:v for j,v in self.extra.items() if j<=900})
    def test_actual_open_only_at_entry(self):
        b=copy.deepcopy(self.b);b['candles'][900]['o']=str(D(b['candles'][900]['o'])*D('1.01'))
        a,ta=f.choices(self.b,spec(room='ROOM'),m.COSTS['base_assumptions'],self.cs,self.extra)
        z,tz=f.choices(b,spec(room='ROOM'),m.COSTS['base_assumptions'],self.cs,self.extra)
        self.assertEqual({j:v for j,v in a.items() if j<900},{j:v for j,v in z.items() if j<900})
    def test_unknown_definition_refused(self):
        with self.assertRaises(ValueError):f.choices(self.b,dict(id='bad'),m.COSTS['base_assumptions'],self.cs,self.extra)
    def test_replay_and_independent_account(self):
        n=0
        for s in f.specs():
            a,ta=f.choices(self.b,s,m.COSTS['base_assumptions'],self.cs,self.extra);b,tb=v.reference(self.b,s,'base_assumptions',self.ref)
            v.check(a,b,ta,tb);row=m.replay(self.b,f.BASE_SPEC,'base_assumptions','OHLC',a,b)
            self.assertFalse(row['open_position']);self.assertEqual(row['initial_usdc'],'10');n+=len(row['trades'])
        self.assertGreater(n,0)
    def test_mutated_filter_detected(self):
        a,ta=f.choices(self.b,spec(),m.COSTS['base_assumptions'],self.cs,self.extra);b,tb=v.reference(self.b,spec(),'base_assumptions',self.ref)
        a[600]['direction']=7
        with self.assertRaises(ValueError):v.check(a,b,ta,tb)
    def test_mutated_financial_result_rejected(self):
        a,_=f.choices(self.b,spec(),m.COSTS['base_assumptions'],self.cs,self.extra);b,_=v.reference(self.b,spec(),'base_assumptions',self.ref)
        row=m.replay(self.b,f.BASE_SPEC,'base_assumptions','OHLC',a,b);self.assertTrue(row['trades']);row['ending_usdc']='999'
        with self.assertRaises(ValueError):v.audit(row,self.b,b)

if __name__=='__main__':unittest.main()
