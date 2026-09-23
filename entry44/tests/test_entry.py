"""Artificial fixtures; these tests are not historical profitability evidence."""
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import copy,json,sys,tempfile,unittest
sys.path.append(str(Path(__file__).parents[2]/'reversal44/tests'))
sys.path.append(str(Path(__file__).parents[2]/'refine44/tests'))
from test_patterns import artificial
from test_refine import short_fixture
import signals_entry as s
import reference_entry as v
import run_entry as run
import reference_bb as parent_reference
from paperlab import engine as core


def sp(name):return next(x for x in s.specs() if x['id']==name)

def fixture(n=660):
    b=artificial(((n+4)//5)*5)
    b['candles']=b['candles'][:n]
    for bar in b['candles']:bar.update(o='98',h='98.1',l='97.9',c='98')
    for bar in b['candles'][585:600]:bar.update(o='101',h='102',l='99.6',c='100')
    for bar in b['candles'][600:]:bar.update(o='100',h='100.1',l='99.7',c='100')
    cs={j:dict(direction=0,atr=D(0),close=D(b['candles'][j-1]['c']),trace={},exit_long=False,exit_short=False) for j in range(600,n)}
    cs[600].update(direction=-1,atr=D('.3'),close=D(100))
    extras={600:parent_reference.reference_extra(b,600)}
    return b,cs,extras

def confirm(b,bar=600):
    b['candles'][bar].update(o='100',h='100.1',l='99.4',c='99.5')
    b['candles'][bar+1]['o']='99.5'


def decision(b,base,extra,name='MICRO',cost='base_assumptions'):
    return s.choices(b,sp(name),run.mean.COSTS[cost],base,extra)


class SignalTests(unittest.TestCase):
    def test_four_definitions(self):self.assertEqual([x['id'] for x in s.specs()],['BASE','VOL','MICRO','VOL_MICRO'])
    def test_primary_fixed(self):self.assertEqual(s.PRIMARY,'VOL')
    def test_unknown_spec_refused(self):
        b,c,e=fixture()
        with self.assertRaises(ValueError):s.choices(b,{'id':'other'},run.mean.COSTS['base_assumptions'],c,e)
    def test_baseline_exact(self):
        b,c,e=fixture();out,ev=decision(b,c,e,'BASE');self.assertEqual(out,c)
    def test_low_vol_rejected(self):
        b,c,e=fixture();c[600]['atr']=D('.1999');out,ev=decision(b,c,e,'VOL')
        self.assertEqual(out[600]['direction'],0);self.assertEqual(ev[0]['status'],'REJECT_LOW_VOL')
    def test_equal_floor_passes(self):
        b,c,e=fixture();c[600]['atr']=D('.2');out,ev=decision(b,c,e,'VOL');self.assertEqual(out[600]['direction'],-1)
    def test_above_floor_passes(self):
        b,c,e=fixture();out,_=decision(b,c,e,'VOL');self.assertEqual(out,c)
    def test_both_rejects_before_waiting(self):
        b,c,e=fixture();confirm(b);c[600]['atr']=D('.1');out,ev=decision(b,c,e,'VOL_MICRO')
        self.assertFalse(any(x['direction'] for x in out.values()));self.assertEqual(ev[0]['status'],'REJECT_LOW_VOL')
    def test_micro_not_same_bar(self):
        b,c,e=fixture();confirm(b);out,ev=decision(b,c,e)
        self.assertEqual(out[600]['direction'],0);self.assertEqual(out[601]['direction'],-1)
    def test_first_future_minute(self):
        b,c,e=fixture();confirm(b);out,ev=decision(b,c,e)
        self.assertEqual(ev[0]['end_index'],601);self.assertEqual(ev[0]['confirmation_end_ms'],b['candles'][600]['T'])
    def test_fifth_minute_valid(self):
        b,c,e=fixture();confirm(b,604);out,ev=decision(b,c,e)
        self.assertEqual(out[605]['direction'],-1);self.assertEqual(out[605]['trace']['waiting_bars'],'5')
    def test_sixth_minute_expired(self):
        b,c,e=fixture();confirm(b,605);out,ev=decision(b,c,e)
        self.assertFalse(any(x['direction'] for x in out.values()));self.assertEqual(ev[0]['status'],'EXPIRED')
    def test_no_confirmation_expired(self):
        b,c,e=fixture();out,ev=decision(b,c,e);self.assertEqual(ev[0]['status'],'EXPIRED');self.assertEqual(ev[0]['end_index'],605)
    def test_green_candle_cannot_confirm(self):
        b,c,e=fixture();b['candles'][600].update(o='99.4',c='99.5',l='99.3');out,ev=decision(b,c,e)
        self.assertEqual(out[601]['direction'],0)
    def test_equal_previous_low_not_confirmation(self):
        b,c,e=fixture();b['candles'][600].update(o='100',c='99.6',l='99.5');out,_=decision(b,c,e)
        self.assertEqual(out[601]['direction'],0)
    def test_signal_high_cancel(self):
        b,c,e=fixture();b['candles'][600].update(o='102.2',c='102.1',h='102.3',l='102');confirm(b,601)
        out,ev=decision(b,c,e);self.assertEqual(ev[0]['status'],'CANCELLED_HIGH');self.assertFalse(any(x['direction'] for x in out.values()))
    def test_equal_high_not_cancel(self):
        b,c,e=fixture();b['candles'][600].update(o='102',c='102',h='102',l='101.9');out,ev=decision(b,c,e)
        self.assertEqual(ev[0]['status'],'EXPIRED')
    def test_cancel_has_priority(self):
        b,c,e=fixture();e[600]['high']=D(99);confirm(b);out,ev=decision(b,c,e)
        self.assertEqual(ev[0]['status'],'CANCELLED_HIGH');self.assertEqual(out[601]['direction'],0)
    def test_same_setup_only_once(self):
        b,c,e=fixture();confirm(b);confirm(b,603);out,_=decision(b,c,e)
        self.assertEqual(sum(x['direction']<0 for x in out.values()),1)
    def test_frozen_atr_and_mean(self):
        b,c,e=fixture();confirm(b,602);out,ev=decision(b,c,e)
        self.assertEqual(out[603]['atr'],c[600]['atr']);self.assertEqual(D(out[603]['trace']['frozen_mean20']),e[600]['mean'])
        self.assertEqual(out[603]['close'],D('99.5'))
    def test_new_room_rechecked_and_consumed(self):
        b,c,e=fixture();confirm(b);b['candles'][601]['o']='98.2';confirm(b,603)
        out,ev=decision(b,c,e);self.assertEqual(ev[0]['status'],'REJECT_NEW_ROOM');self.assertFalse(any(x['direction'] for x in out.values()))
    def test_new_entry_open_changes_cost_not_past(self):
        b,c,e=fixture();confirm(b);out,_=decision(b,c,e)
        b['candles'][601]['o']='98.2';other,_=decision(b,c,e)
        self.assertEqual(out[600],other[600]);self.assertNotEqual(out[601]['direction'],other[601]['direction'])
    def test_confirmation_execution_bar_hlc_irrelevant(self):
        b,c,e=fixture();confirm(b);out,_=decision(b,c,e)
        b['candles'][601].update(h='300',l='1',c='50');changed,_=decision(b,c,e)
        self.assertEqual(out[601],changed[601])
    def test_short_tail_never_filled(self):
        b,c,e=fixture(601);confirm(b,599);out,ev=decision(b,c,e)
        self.assertEqual(ev[0]['status'],'END_OF_BLOCK_UNCONFIRMED');self.assertEqual(out[600]['direction'],0)
    def test_no_event_no_trade(self):
        b,c,e=fixture();c[600]['direction']=0;out,ev=decision(b,c,e);self.assertEqual(ev,[]);self.assertFalse(any(x['direction'] for x in out.values()))
    def test_complete_reference_search(self):
        for name in [x['id'] for x in s.specs()]:
            for cost in run.mean.COSTS:
                b,c,e=fixture();confirm(b,602)
                a,t=decision(b,c,e,name,cost);r,rt=v.reference(b,sp(name),cost,c);v.check(a,r,t,rt)
    def test_reference_tail(self):
        b,c,e=fixture(601);a,t=decision(b,c,e);r,rt=v.reference(b,sp('MICRO'),'base_assumptions',c);v.check(a,r,t,rt)
    def test_mutation_detected(self):
        b,c,e=fixture();confirm(b);a,t=decision(b,c,e);r,rt=v.reference(b,sp('MICRO'),'base_assumptions',c);a[601]['direction']=0
        with self.assertRaises(ValueError):v.check(a,r,t,rt)
    def test_future_suffix_cannot_change_decisions(self):
        b,c,e=fixture();confirm(b);a,_=decision(b,c,e)
        for bar in b['candles'][606:]:bar.update(o='300',h='301',l='299',c='300')
        r,_=decision(b,c,e);self.assertEqual({j:x for j,x in a.items() if j<606},{j:x for j,x in r.items() if j<606})


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block=short_fixture()
        def price(i):
            if i<600:return D(2000)
            if i<615:return D(2000)+D(i-599)*40/15
            if i<630:return D(2040)-D(i-614)*20/15
            if i<645:return D(2020)-D(i-629)*20/15
            return D(2000)
        for i,b in enumerate(cls.block['candles']):
            o,c=price(i-1),price(i)
            b.update(o=str(o),c=str(c),h=str(max(o,c)+D('.2')),l=str(min(o,c)-D('.2')))
    def test_independent_full_features_and_accounts(self):
        count=0
        for cost in run.mean.COSTS:
            base,ref,extras,_=run.prepare(self.block,cost)
            for spec in s.specs():
                a,t=s.choices(self.block,spec,run.mean.COSTS[cost],base,extras);b,bt=v.reference(self.block,spec,cost,ref);v.check(a,b,t,bt)
                for path in run.mean.PATHS:
                    r=run.mean.replay(self.block,run.filters.BASE_SPEC,cost,path,a,b)
                    self.assertEqual(r['initial_usdc'],'10');self.assertFalse(r['open_position']);self.assertFalse(r['pending_funding']);count+=len(r['trades'])
                    for x,y in zip(r['trades'],r['trades'][1:]):self.assertLess(x['closed_ms'],y['opened_ms'])
                    for x in r['trades']:self.assertEqual(x['direction'],-1)
        self.assertGreater(count,0)
    def test_causal_preparation_future_mutation(self):
        b=copy.deepcopy(self.block)
        for bar in b['candles'][900:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        for cost in run.mean.COSTS:
            c,ref,ex,_=run.prepare(self.block,cost);d,r2,e2,_=run.prepare(b,cost)
            for spec in s.specs():
                a,_=s.choices(self.block,spec,run.mean.COSTS[cost],c,ex);z,_=s.choices(b,spec,run.mean.COSTS[cost],d,e2)
                self.assertEqual({j:v for j,v in a.items() if j<900},{j:v for j,v in z.items() if j<900})
    def test_financial_tamper_fails(self):
        base,ref,ex,_=run.prepare(self.block,'base_assumptions');a,ev=s.choices(self.block,sp('BASE'),run.mean.COSTS['base_assumptions'],base,ex)
        r=run.mean.replay(self.block,run.filters.BASE_SPEC,'base_assumptions','OHLC',a,ref);r['ending_usdc']='999'
        with self.assertRaises(ValueError):v.audit(r,self.block,ref)
    def test_hook_restored(self):
        before=core.strategy_signal
        with self.assertRaises(RuntimeError):
            with run.mean.installed({}):raise RuntimeError('artificial error')
        self.assertIs(before,core.strategy_signal)
    def test_missing_cases_fail(self):
        with self.assertRaises(ValueError):run.aggregate([])
    def test_output_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileExistsError):run.run(Path(d),Path(d))
    def test_failure_manifest_saved(self):
        with tempfile.TemporaryDirectory() as d,patch.object(run,'load_parent',side_effect=ValueError('Artificial corrupt input')):
            out=Path(d)/'out'
            with self.assertRaises(ValueError):run.run(Path(d),out)
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'evidence-manifest.json').exists())
            self.assertEqual(json.loads((out/'status.json').read_text())['status'],'FAILED_NO_VALIDATED_COMPLETE_RESULT')


if __name__=='__main__':unittest.main()
