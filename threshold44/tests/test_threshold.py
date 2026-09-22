"""Artificial fixtures only; no fixture output is evidence of trading profit."""
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import copy, json, subprocess, sys, tempfile, unittest
sys.path.append(str(Path(__file__).parents[2]/'entry44/tests'))
import test_entry as fixture_source
fixture = fixture_source.fixture
import run_threshold as run
import signals_threshold as s
import reference_threshold as ref


def spec(name='V030_R30'): return next(x for x in s.specs() if x['id']==name)
COST=run.mean.COSTS['base_assumptions']


def market(mean=D(98)):
    return dict(high=D(101),low=D(99),close=D(100),mean=mean)


class GateTests(unittest.TestCase):
    def test_nine_definitions(self): self.assertEqual(len({x['id'] for x in s.specs()}),9)
    def test_fixed_baseline_and_primary(self): self.assertEqual((s.BASELINE,s.PRIMARY),('V030_R30','V035_R30'))
    def test_invalid_definition(self):
        with self.assertRaises(ValueError): s.gate(market(),D('.2'),D(100),'100',dict(spec(),min_vol='0'),COST)
    def test_floor_equality_allowed(self): self.assertNotIn('LOW_VOL',s.gate(market(),D('.2'),D(100),'100',spec(),COST)[0])
    def test_below_floor_refused(self): self.assertIn('LOW_VOL',s.gate(market(),D('.19999'),D(100),'100',spec(),COST)[0])
    def test_relaxed_vol_admission(self): self.assertNotIn('LOW_VOL',s.gate(market(),D('.18'),D(100),'100',spec('V025_R30'),COST)[0])
    def test_stricter_vol_rejection(self): self.assertIn('LOW_VOL',s.gate(market(),D('.21'),D(100),'100',spec('V035_R30'),COST)[0])
    def test_room_equality_allowed(self):
        p=D(100)*(1-D('.00005'))*(1-D('.0001'))
        self.assertNotIn('ROOM',s.gate(market(p*(1-D('.0036'))),D('.2'),D(100),'100',spec(),COST)[0])
    def test_room_below_threshold_refused(self):
        p=D(100)*(1-D('.00005'))*(1-D('.0001'))
        self.assertIn('ROOM',s.gate(market(p*(1-D('.0036'))+D('.00001')),D('.2'),D(100),'100',spec(),COST)[0])
    def test_looser_room_recovers_rejected_setup(self):
        p=D(100)*(1-D('.00005'))*(1-D('.0001'));f=market(p*(1-D('.0033')))
        self.assertNotIn('ROOM',s.gate(f,D('.3'),D(100),'100',spec('V030_R25'),COST)[0])
        self.assertIn('ROOM',s.gate(f,D('.3'),D(100),'100',spec(),COST)[0])
    def test_cost_units(self):
        self.assertEqual(D(s.gate(market(),D('.3'),D(100),'100',spec(),COST)[1]['required_room']),D('.0036'))
        self.assertEqual(D(s.gate(market(),D('.3'),D(100),'100',spec(),run.mean.COSTS['cost_stress'])[1]['required_room']),D('.0081'))
    def test_lower_half_boundary(self): self.assertNotIn('WEAK_CLOSE',s.gate(market(),D('.3'),D(100),'100',spec(),COST)[0])
    def test_weak_close(self): self.assertIn('WEAK_CLOSE',s.gate(dict(market(),close=D('100.1')),D('.3'),D(100),'100',spec(),COST)[0])
    def test_flat_candle(self): self.assertIn('WEAK_CLOSE',s.gate(dict(market(),high=D(100),low=D(100)),D('.3'),D(100),'100',spec(),COST)[0])
    def test_nonpositive_and_nan(self):
        for a in (D(0),D(-1),D('NaN'),D('Infinity')):
            with self.assertRaises(ValueError): s.gate(market(),a,D(100),'100',spec(),COST)
    def test_invalid_bounds(self):
        with self.assertRaises(ValueError): s.gate(dict(market(),close=D(102)),D('.3'),D(100),'100',spec(),COST)
    def test_negative_room(self): self.assertIn('ROOM',s.gate(market(D(102)),D('.3'),D(100),'100',spec(),COST)[0])
    def test_open_changes_room_not_historical_vol(self):
        a=s.gate(market(),D('.3'),D(100),'100',spec(),COST)[1]
        b=s.gate(market(),D('.3'),D(100),'99',spec(),COST)[1]
        self.assertEqual(a['vol_fraction'],b['vol_fraction']);self.assertNotEqual(a['room_fraction'],b['room_fraction'])
    def test_does_not_add_long_or_delayed_entries(self):
        b,r,e=fixture();r[601]['direction']=1;c,_=s.choices(b,spec(),COST,r,e)
        self.assertEqual(c[600]['direction'],-1);self.assertEqual(c[601]['direction'],0)
    def test_reference_all_gates(self):
        b,r,e=fixture()
        for sp in s.specs():
            for cost in run.mean.COSTS:
                a,t=s.choices(b,sp,run.mean.COSTS[cost],r,e);v,vt=ref.reference(b,sp,cost,r);ref.check(a,v,t,vt)
    def test_corrupted_choice_detected(self):
        b,r,e=fixture();a,t=s.choices(b,spec(),COST,r,e);v,vt=ref.reference(b,spec(),'base_assumptions',r)
        a[600]['direction']=0
        with self.assertRaises(ValueError): ref.check(a,v,t,vt)
    def test_missing_grid_refused(self):
        with self.assertRaises(ValueError): run.aggregate([])
    def test_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError): run.run(Path('not-used'),Path(td))
    def test_failure_status_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'output'
            with patch.object(run,'load_parent',side_effect=ValueError('fixture missing data')):
                with self.assertRaises(ValueError): run.run(Path('not-used'),out)
            self.assertEqual(json.loads((out/'status.json').read_text())['status'],'FAILED_NO_COMPLETE_RESULT')
            self.assertFalse((out/'SUMMARY.json').exists())
    def test_offline_guard_does_not_break_ssl(self):
        code="""import sys

def guard(event,args):
 if event.startswith('socket.'): raise PermissionError('offline')
sys.addaudithook(guard)
import ssl, socket
for f in (lambda: socket.socket(),lambda: socket.getaddrinfo('example.invalid',443)):
 try: f()
 except PermissionError: pass
 else: raise AssertionError('socket access allowed')
print('OFFLINE_GUARD_PASS')
"""
        r=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('OFFLINE_GUARD_PASS',r.stdout)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_source.AccountTests.setUpClass();cls.block=copy.deepcopy(fixture_source.AccountTests.block)
    def test_current_b_decisions_exact(self):
        raw,rr,ex=run.prepare(self.block)
        for cost in run.mean.COSTS:
            a,t=s.choices(self.block,spec(),run.mean.COSTS[cost],raw,ex)
            old,oldref,olde,_=run.previous.prepare(self.block,cost)
            b,_=run.previous.strategy.choices(self.block,run.previous.strategy.specs()[1],run.mean.COSTS[cost],old,olde)
            self.assertEqual(a,b)
    def test_all36_fixture_accounts_and_stop_preserved(self):
        raw,rr,ex=run.prepare(self.block);trades=0
        for sp in s.specs():
            for cost in run.mean.COSTS:
                a,t=s.choices(self.block,sp,run.mean.COSTS[cost],raw,ex);v,vt=ref.reference(self.block,sp,cost,rr);ref.check(a,v,t,vt)
                for path in run.mean.PATHS:
                    row=run.mean.replay(self.block,run.previous.filters.BASE_SPEC,cost,path,a,v)
                    self.assertFalse(row['open_position']);self.assertFalse(row['integrity_warnings'])
                    for tr in row['trades']:
                        stop=max(D('.003'),D('1.5')*D(tr['signal_atr'])/D(tr['signal_close']))
                        self.assertEqual(D(tr['stop']),D(tr['entry'])*(1+stop))
                    trades+=len(row['trades'])
        self.assertGreater(trades,0)
    def test_future_suffix_independent(self):
        b=copy.deepcopy(self.block)
        for bar in b['candles'][900:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        r,_,e=run.prepare(self.block);rr,_,ee=run.prepare(b)
        for sp in s.specs():
            a,_=s.choices(self.block,sp,COST,r,e);c,_=s.choices(b,sp,COST,rr,ee)
            self.assertEqual({i:v for i,v in a.items() if i<900},{i:v for i,v in c.items() if i<900})
    def test_entry_bar_hlc_not_used(self):
        b=copy.deepcopy(self.block);r,_,e=run.prepare(b)
        for sp in s.specs():
            a,_=s.choices(b,sp,COST,r,e)
            for j in r:
                if a[j]['direction']:
                    other=copy.deepcopy(b);other['candles'][j].update(h='9000',l='1',c='100')
                    rr,_,ee=run.prepare(other);c,_=s.choices(other,sp,COST,rr,ee)
                    self.assertEqual(a[j],c[j]);return
        self.fail('Fixture had no qualifying entry')


if __name__=='__main__':unittest.main()
