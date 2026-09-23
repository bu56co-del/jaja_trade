"""Artificial data only: tests never claim real trading performance."""
from pathlib import Path
from decimal import Decimal as D
import copy,json,sys,tempfile,unittest
from unittest.mock import patch
sys.path.append(str(Path(__file__).parents[2]/'reversal44/tests'))
from test_patterns import artificial
import higher as h
import verify_h4 as v
import run_h4 as run
import run_mean as oldrun
import filters_bb


def spec(name='NONE_HARD_360'):return next(s for s in h.specs() if s['id']==name)
def data4(start,count=135,delta=1):
    return [dict(t=start+i*h.H4,T=start+(i+1)*h.H4-1,o=str(2000+i*delta),h=str(2002+i*delta),l=str(1998+i*delta),c=str(2000+i*delta),v='100',n=50,s='ETH',i='4h') for i in range(count)]


class ContextTests(unittest.TestCase):
    def test_grid(self):self.assertEqual(len(h.specs()),12);self.assertEqual(len({s['id'] for s in h.specs()}),12)
    def test_hold_not_timeframe(self):self.assertEqual(spec('NONE_HARD_240')['hold'],240);self.assertEqual(spec('DOWN_ONLY_MID15_360')['hold'],360)
    def test_primary(self):self.assertEqual(h.PRIMARY,'DOWN_ONLY_MID15_240')
    def test_normalize(self):self.assertEqual(len(h.normalize(data4(0),0,135*h.H4)),135)
    def test_missing(self):
        with self.assertRaises(ValueError):h.normalize(data4(0)[1:],0,135*h.H4)
    def test_duplicate(self):
        with self.assertRaises(ValueError):h.normalize(data4(0)+data4(0)[:1],0,135*h.H4)
    def test_wrong_market(self):
        a=data4(0);a[0]['s']='BTC'
        with self.assertRaises(ValueError):h.normalize(a,0,135*h.H4)
    def test_bad_ohlc(self):
        a=data4(0);a[0]['h']='1'
        with self.assertRaises(ValueError):h.normalize(a,0,135*h.H4)
    def test_nonfinite(self):
        a=data4(0);a[0]['c']='NaN'
        with self.assertRaises(ValueError):h.normalize(a,0,135*h.H4)
    def test_insufficient_closed_warmup(self):
        with self.assertRaises(ValueError):h.Context(data4(0)).at(120*h.H4-1)
    def test_exact_close_boundary(self):self.assertEqual(h.Context(data4(0)).at(120*h.H4)['last_closed_ms'],120*h.H4-1)
    def test_inprogress_not_used(self):self.assertEqual(h.Context(data4(0)).at(121*h.H4-1)['last_closed_ms'],120*h.H4-1)
    def test_new_bar_only_after_close(self):self.assertEqual(h.Context(data4(0)).at(121*h.H4)['last_closed_ms'],121*h.H4-1)
    def test_stale_context(self):
        with self.assertRaises(ValueError):h.Context(data4(0)).at(137*h.H4)
    def test_future_mutation(self):
        a=data4(0);b=copy.deepcopy(a)
        for r in b[120:]:r['c']='9999'
        self.assertEqual(h.Context(a).at(120*h.H4),h.Context(b).at(120*h.H4))
    def test_gap_context_refused(self):
        with self.assertRaises(ValueError):h.Context(data4(0)[:125]+data4(0)[126:])
    def test_independent_geometry(self):
        for d in (-1,0,1):
            a=data4(0,delta=d);x=h.Context(a).at(130*h.H4);y=v.reference_context(a,130*h.H4)
            for k in x:v.close_feature(x[k],y[k],k)
    def test_up_veto(self):self.assertFalse(h.allow(dict(fast=D(10),slow=D(9),rise4=D(2),rise1=D(1),atr=D(1)),'UP_VETO'))
    def test_down_only(self):self.assertTrue(h.allow(dict(fast=D(9),slow=D(10),rise1=D(-1)),'DOWN_ONLY'))
    def test_rising_fast_refused(self):self.assertFalse(h.allow(dict(fast=D(9),slow=D(10),rise1=D(1)),'DOWN_ONLY'))
    def test_unknown_filter(self):
        with self.assertRaises(ValueError):h.allow({},'wrong')


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b=artificial(1440);cls.rows=data4(cls.b['candles'][0]['t']-125*h.H4)
        cls.a,cls.r,cls.ex=run.base_choices(cls.b,'base_assumptions')
    def test_all_decisions_independent(self):
        for s in h.specs():
            a,ta=h.choices(self.b,s,self.a,self.ex,h.Context(self.rows));b,tb=v.reference_choices(self.b,s,self.r,self.rows);v.check(a,b,ta,tb)
    def test_no_4h_baseline_exact(self):
        s=spec();a,ta=h.choices(self.b,s,self.a,self.ex,h.Context(self.rows));b,tb=v.reference_choices(self.b,s,self.r,self.rows)
        actual=run.replay(self.b,s,'base_assumptions','OHLC',a,b)
        expected=oldrun.replay(self.b,filters_bb.BASE_SPEC,'base_assumptions','OHLC',self.a,self.r)
        self.assertEqual(actual,expected)
    def test_no_new_longs(self):
        for s in h.specs():
            a,_=h.choices(self.b,s,self.a,self.ex,h.Context(self.rows));self.assertTrue(all(c['direction'] in (0,-1) for c in a.values()))
    def test_mid_exit_only_15m_close(self):
        a,_=h.choices(self.b,spec('NONE_MID15_240'),self.a,self.ex,h.Context(self.rows))
        self.assertTrue(any(c['exit_short'] for c in a.values()))
        self.assertTrue(all(j%15==0 for j,c in a.items() if c['exit_short']))
    def test_future_1m_not_used(self):
        s=spec('NONE_MID15_240');a,_=h.choices(self.b,s,self.a,self.ex,h.Context(self.rows))
        changed=copy.deepcopy(self.b)
        for r in changed['candles'][900:]:
            for k in 'ohlc':r[k]=str(D(r[k])*2)
        aa,rr,ex=run.base_choices(changed,'base_assumptions');b,_=h.choices(changed,s,aa,ex,h.Context(self.rows))
        self.assertEqual({j:c for j,c in a.items() if j<900},{j:c for j,c in b.items() if j<900})
    def test_event_mutation_detected(self):
        a,ta=h.choices(self.b,spec(),self.a,self.ex,h.Context(self.rows));b,tb=v.reference_choices(self.b,spec(),self.r,self.rows)
        a[600]['direction']=7
        with self.assertRaises(ValueError):v.check(a,b,ta,tb)
    def flat(self):
        b=artificial(1200)
        for x in b['candles']:
            for k in 'ohlc':x[k]='2000'
        cs={j:dict(direction=-1 if j==615 else 0,atr=D(4) if j==615 else D(0),close=D(2000),trace={},exit_short=False,exit_long=False) for j in range(600,1200)}
        return b,cs
    def test_240_and_360_actual_time_exit(self):
        b,cs=self.flat()
        for hold in (240,360):
            r=run.replay(b,spec(f'NONE_HARD_{hold}'),'base_assumptions','OHLC',cs,cs)
            self.assertEqual(len(r['trades']),1);t=r['trades'][0]
            self.assertEqual(t['closed_ms']-t['opened_ms'],hold*60000);self.assertEqual(t['close_reason'],'MAX_HOLD_TIME')
    def test_dynamic_exit_next_open(self):
        b,cs=self.flat();cs[630]['exit_short']=True
        r=run.replay(b,spec('NONE_MID15_240'),'base_assumptions','OHLC',cs,cs);t=r['trades'][0]
        self.assertEqual(t['closed_ms'],b['candles'][630]['t']+2000);self.assertEqual(t['close_reason'],'DYNAMIC_MID15')
    def test_ledger_mutation_rejected(self):
        b,cs=self.flat();r=run.replay(b,spec(),'base_assumptions','OHLC',cs,cs);r['ending_usdc']='999'
        with self.assertRaises(ValueError):v.audit(r,b,cs)
    def test_wrong_hold_refused(self):
        b,cs=self.flat()
        with self.assertRaises(ValueError):run.replay(b,dict(spec(),hold=300),'base_assumptions','OHLC',cs,cs)
    def test_full_synthetic_cases(self):
        total=0
        for cost in run.COSTS:
            base,rb,ex=run.base_choices(self.b,cost)
            for s in h.specs():
                a,ta=h.choices(self.b,s,base,ex,h.Context(self.rows));b,tb=v.reference_choices(self.b,s,rb,self.rows);v.check(a,b,ta,tb)
                for path in run.PATHS:
                    r=run.replay(self.b,s,cost,path,a,b);self.assertFalse(r['open_position']);self.assertFalse(r['integrity_warnings']);total+=1
        self.assertEqual(total,48)

if __name__=='__main__':unittest.main()
