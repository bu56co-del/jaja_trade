"""Artificial fixtures only; real-data integrity is checked by the research run."""
import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal as D
import history as h
import run_compare as r
sys.path.append(str(Path(__file__).parents[2]/'entry44/tests'))
import test_entry as fixtures


def bar(t=0): return dict(t=t,T=t+59999,s='ETH',i='1m',o='100',h='102',l='99',c='101',v='3',n=2)


class DataTests(unittest.TestCase):
    def test_valid_candles(self): self.assertEqual(len(h.candle_projection([bar(),bar(60000)])),2)
    def test_duplicate(self):
        with self.assertRaises(ValueError): h.candle_projection([bar(),bar()])
    def test_gap(self):
        with self.assertRaises(ValueError): h.candle_projection([bar(),bar(120000)])
    def test_wrong_coin(self):
        with self.assertRaises(ValueError): h.candle_projection([dict(bar(),s='BTC')])
    def test_wrong_interval(self):
        with self.assertRaises(ValueError): h.candle_projection([dict(bar(),i='5m')])
    def test_wrong_close_time(self):
        with self.assertRaises(ValueError): h.candle_projection([dict(bar(),T=60000)])
    def test_invalid_prices(self):
        for value in ('0','-1','NaN','Infinity'):
            with self.assertRaises(ValueError): h.candle_projection([dict(bar(),c=value)])
    def test_bad_ohlc(self):
        with self.assertRaises(ValueError): h.candle_projection([dict(bar(),c='110')])
    def test_official_empty_flat_preserved(self):
        b=dict(bar(),o='100',h='100',l='100',c='100',v='0',n=0)
        self.assertEqual(h.candle_projection([b])[0]['v'],'0')
    def test_nonflat_zero_volume_rejected(self):
        with self.assertRaises(ValueError): h.candle_projection([dict(bar(),v='0',n=0)])
    def test_zero_volume_count_conflict(self):
        with self.assertRaises(ValueError): h.candle_projection([dict(bar(),v='0')])
    def test_exact_overlap_merged_once(self):
        a=h.candle_projection([bar(),bar(60000)]);out,count=h.merge_rows([a,a[1:]],'t')
        self.assertEqual((len(out),count),(2,1))
    def test_conflict_not_overwritten(self):
        a=h.candle_projection([bar()]);b=h.candle_projection([dict(bar(),v='4')])
        with self.assertRaises(ValueError): h.merge_rows([a,b],'t')
    def test_decimal_format_not_a_conflict(self): self.assertTrue(h.same({'v':'0.50'},{'v':'0.5'}))
    def test_trim_edges_not_interior(self):
        xs=h.candle_projection([bar(i*60000) for i in range(1,632)])
        kept,trim=h.trim_full_15m(xs)
        self.assertEqual((kept[0]['t'],len(kept),trim),(900000,615,dict(leading_minutes=14,trailing_minutes=2)))
    def test_trim_cannot_hide_gap(self):
        xs=[dict(t=i*60000,T=i*60000+59999) for i in range(630) if i!=120]
        with self.assertRaises(ValueError): h.trim_full_15m(xs)
    def test_insufficient_history(self):
        with self.assertRaises(ValueError): h.trim_full_15m([dict(t=0,T=59999)])
    def test_missing_funding(self):
        with self.assertRaises(ValueError): h.funding_window([dict(time=0,rate='0')],0,7200000)
    def test_funding_offsets_preserved(self):
        fs=[dict(time=50,rate='0.1'),dict(time=3600100,rate='0.1')]
        self.assertEqual(h.funding_window(fs,0,7200000),fs)
    def test_partial_first_hour(self):
        fs=[dict(time=0,rate='0'),dict(time=3600010,rate='0')]
        self.assertEqual(h.funding_window(fs,1800000,7200000),fs[1:])
    def test_funding_conflict(self):
        with self.assertRaises(ValueError): h.merge_rows([[dict(time=0,rate='0')],[dict(time=0,rate='.1')]],'time')
    def test_funding_wrong_identity(self):
        with self.assertRaises(ValueError): h.funding_projection([dict(time=0,coin='BTC',fundingRate='0')])
    def test_funding_bad_rate(self):
        with self.assertRaises(ValueError): h.funding_projection([dict(time=0,coin='ETH',fundingRate='NaN')])
    def test_path_escape(self):
        with tempfile.TemporaryDirectory() as t:
            for n in ('../secret','/etc/passwd'):
                with self.assertRaises(ValueError): h.child(t,n)
    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'j';p.write_text('{"a":1,"a":2}')
            with self.assertRaises(ValueError): h.read(p)
    def test_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'j';p.write_text('{"a":NaN}')
            with self.assertRaises(ValueError): h.read(p)


class AccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AccountTests.setUpClass();cls.block=copy.deepcopy(fixtures.AccountTests.block)
    def test_only_two_frozen_versions(self):
        self.assertEqual([s['id'] for s in r.specs()],['V030_R30','V035_R30'])
        self.assertTrue(all(s['room_multiple']=='3.0' for s in r.specs()))
    def test_protocol_no_orders(self): self.assertEqual((r.protocol()['new_cases'],r.protocol()['orders'],r.protocol()['regression_cases']),(8,0,216))
    def test_existing_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(FileExistsError): r.run(Path(t),Path(t),Path(t),Path(t))
    def test_failure_keeps_status_not_headline(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'out'
            with patch.object(r,'load_parent',side_effect=ValueError('fixture missing')):
                with self.assertRaises(ValueError): r.run(None,None,None,p)
            self.assertEqual(h.read(p/'status.json')['status'],'FAILED_NO_COMPLETE_RESULT')
            self.assertFalse((p/'SUMMARY.json').exists())
    def test_baseline_corruption_rejected(self):
        with self.assertRaises(ValueError): r.compare_rows({'ending_usdc':'10'},{'ending_usdc':'11'})
    def test_eight_fixture_accounts(self):
        b=self.block;raw,rr,extra=r.old.prepare(b);n=0
        for s in r.specs():
            for c in r.old.mean.COSTS:
                choices,t=r.old.strategy.choices(b,s,r.old.mean.COSTS[c],raw,extra)
                ref,rt=r.old.independent.reference(b,s,c,rr);r.old.independent.check(choices,ref,t,rt)
                for p in r.old.mean.PATHS:
                    row=r.old.mean.replay(b,r.old.previous.filters.BASE_SPEC,c,p,choices,ref)
                    self.assertFalse(row['open_position']);self.assertFalse(row['integrity_warnings'])
                    self.assertEqual(row['initial_usdc'],'10');n+=1
        self.assertEqual(n,8)
    def test_future_suffix_not_used(self):
        a=copy.deepcopy(self.block);b=copy.deepcopy(a)
        for bar in b['candles'][900:]:
            for k in 'ohlc':bar[k]=str(D(bar[k])*2)
        raw,rr,e=r.old.prepare(a);raw2,rr2,e2=r.old.prepare(b)
        for s in r.specs():
            x,_=r.old.strategy.choices(a,s,r.old.mean.COSTS['base_assumptions'],raw,e)
            y,_=r.old.strategy.choices(b,s,r.old.mean.COSTS['base_assumptions'],raw2,e2)
            self.assertEqual({k:v for k,v in x.items() if k<900},{k:v for k,v in y.items() if k<900})
    def test_offline_guard_ssl_and_dns(self):
        code="""import sys

def guard(event,args):
 if event.startswith('socket.'):raise PermissionError('offline')
sys.addaudithook(guard)
import ssl,socket
for f in (socket.socket,lambda:socket.getaddrinfo('example.invalid',443)):
 try:f()
 except PermissionError:pass
 else:raise AssertionError('network allowed')
print('OFFLINE_PASS')
"""
        completed=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(completed.returncode,0,completed.stderr);self.assertIn('OFFLINE_PASS',completed.stdout)

if __name__=='__main__':unittest.main()
