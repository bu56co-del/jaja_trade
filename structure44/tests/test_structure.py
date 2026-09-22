"""Artificial market fixtures only. Tests are not profitability evidence."""
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
import copy, json, sys, tempfile, unittest
sys.path.append(str(Path(__file__).parents[2]/'entry44/tests'))
from test_entry import fixture
import signals_structure as s
import reference_structure as ref
import run_structure as run
import reference_bb
from paperlab import engine as core


def spec(name):
    return next(x for x in s.specs() if x['id']==name)


def market():
    block, raw, _ = fixture(720)
    for k in range(39):
        close = D('98') + D(k%3)/10
        for b in block['candles'][k*15:(k+1)*15]:
            b.update(o=str(close), h='98.3', l=str(close-D('.1')), c=str(close))
    for k, high in ((30, '104'), (35, '103')):
        block['candles'][k*15]['h'] = high
    return block, raw, {600:reference_bb.reference_extra(block,600)}


def apply(block, raw, extras, name='LOWER_HIGH', cost='base_assumptions'):
    return s.choices(block, spec(name), run.mean.COSTS[cost], raw, extras, s.contexts(block))


def pair(a='104', b='103', z1='3', z2='2'):
    return [dict(high=a,z=z1), dict(high=b,z=z2)]


class PatternTests(unittest.TestCase):
    def test_four_frozen_specs(self):
        self.assertEqual([x['id'] for x in s.specs()], ['BASE030','BASE035','LOWER_HIGH','Z_WEAKENING'])
        self.assertEqual(s.PRIMARY, 'LOWER_HIGH')
    def test_spec_mutation_refused(self):
        with self.assertRaises(ValueError): s.parent_spec(dict(spec('LOWER_HIGH'),parent='V030_R30'))
    def test_unknown_pattern_refused(self):
        with self.assertRaises(ValueError): s.decision([], 'unknown')
    def test_no_pattern_needs_no_peaks(self):
        self.assertTrue(s.decision([], 'NONE')[0])
    def test_one_peak_rejected(self):
        self.assertFalse(s.decision(pair()[:1], 'LOWER_HIGH')[0])
    def test_lower_high(self):
        self.assertTrue(s.decision(pair(), 'LOWER_HIGH')[0])
    def test_equal_high_not_lower(self):
        self.assertFalse(s.decision(pair(b='104'), 'LOWER_HIGH')[0])
    def test_z_weakening_equal_price_allowed(self):
        self.assertTrue(s.decision(pair(b='104'), 'Z_WEAKENING')[0])
    def test_z_weakening_higher_price_allowed(self):
        self.assertTrue(s.decision(pair(b='105'), 'Z_WEAKENING')[0])
    def test_z_weakening_lower_price_excluded(self):
        self.assertFalse(s.decision(pair(), 'Z_WEAKENING')[0])
    def test_equal_z_not_weakening(self):
        self.assertFalse(s.decision(pair(b='105',z2='3'), 'Z_WEAKENING')[0])
    def test_numeric_tie_not_weakening(self):
        self.assertFalse(s.decision(pair(b='105',z2='2.999999999999'), 'Z_WEAKENING')[0])
    def test_more_than_numeric_tie(self):
        self.assertTrue(s.decision(pair(b='105',z2='2.999999999998'), 'Z_WEAKENING')[0])
    def test_zero_sd_rejected(self):
        for p in ('LOWER_HIGH','Z_WEAKENING'):
            self.assertEqual(s.decision(pair(z1=None),p)[1], 'ZERO_STANDARD_DEVIATION')


class CausalityTests(unittest.TestCase):
    def test_latest_two_confirmed(self):
        block,_,_=market()
        self.assertEqual([p['bar_index'] for p in s.contexts(block)[600]], [30,35])
    def test_current_unclosed_bar_not_used(self):
        block,_,_=market()
        before=s.contexts(block)[600]
        for b in block['candles'][600:]:
            for key in 'ohlc':b[key]=str(D(b[key])*2)
        self.assertEqual(before,s.contexts(block)[600])
    def test_right_confirmation_waits_for_close(self):
        block,_,_=market()
        block['candles'][585]['h']='110'
        self.assertNotIn(39,[p['bar_index'] for p in s.contexts(block)[600]])
        after=s.contexts(block)[615]
        self.assertIn(39,[p['bar_index'] for p in after])
        self.assertEqual(after[-1]['confirmed_ms'],block['candles'][614]['T'])
    def test_equal_adjacent_high_excluded(self):
        block,_,_=market();block['candles'][36*15]['h']='103'
        self.assertNotIn(35,[p['bar_index'] for p in s.contexts(block)[600]])
        self.assertNotIn(36,[p['bar_index'] for p in s.contexts(block)[600]])
    def test_three_hour_inclusive_boundary(self):
        block,_,_=market();block['candles'][30*15]['h']='98.3';block['candles'][28*15]['h']='104'
        self.assertEqual([p['bar_index'] for p in s.contexts(block)[600]], [28,35])
    def test_older_than_three_hours_excluded(self):
        block,_,_=market();block['candles'][30*15]['h']='98.3';block['candles'][27*15]['h']='104'
        self.assertNotIn(27,[p['bar_index'] for p in s.contexts(block)[600]])
    def test_uses_last_pair_not_favourable_pair(self):
        block,_,_=market();block['candles'][37*15]['h']='105'
        self.assertEqual([p['bar_index'] for p in s.contexts(block)[600]],[35,37])
        self.assertFalse(s.decision(s.contexts(block)[600],'LOWER_HIGH')[0])
    def test_z_does_not_use_confirmation_close(self):
        block,_,_=market();before=s.contexts(block)[600][0]
        for b in block['candles'][31*15:32*15]:b['c']='97.95'
        after=s.contexts(block)[600][0]
        self.assertEqual(before['z'],after['z'])
    def test_variance_population(self):
        block,_,_=market();bars=s.aggregate(block['candles'],15);p=s.peak(bars,30)
        values=[D(b['c']) for b in bars[11:31]];avg=sum(values)/20
        self.assertEqual(D(p['variance20']),sum((v-avg)**2 for v in values)/20)
    def test_prefix_same_decision(self):
        block,raw,extra=market();a,t=apply(block,raw,extra)
        short=copy.deepcopy(block);short['candles']=short['candles'][:615]
        short_raw={j:r for j,r in raw.items() if j<615}
        b,u=apply(short,short_raw,extra)
        self.assertEqual(t,u);self.assertEqual({j:r for j,r in a.items() if j<615},b)
    def test_no_future_peak_injection(self):
        block,raw,extra=market();ctx=s.contexts(block);ctx[600][-1]['confirmed_ms']=block['candles'][600]['t']
        with self.assertRaises(ValueError): s.choices(block,spec('LOWER_HIGH'),run.mean.COSTS['base_assumptions'],raw,extra,ctx)
    def test_old_baselines_exact(self):
        block,raw,extra=market()
        for n in ('BASE030','BASE035'):
            out,_=apply(block,raw,extra,n)
            expected,_=s.threshold.choices(block,s.parent_spec(spec(n)),run.mean.COSTS['base_assumptions'],raw,extra)
            self.assertEqual(out,expected)
    def test_no_new_entry_or_direction(self):
        block,raw,extra=market();out,_=apply(block,raw,extra)
        self.assertEqual([j for j,r in out.items() if r['direction']],[600])
        self.assertEqual(out[600]['direction'],-1)
    def test_independent_features_all_patterns_costs(self):
        block,raw,extra=market()
        for sp in s.specs():
            for cost in run.mean.COSTS:
                out,t=s.choices(block,sp,run.mean.COSTS[cost],raw,extra,s.contexts(block))
                expected,u=ref.reference(block,sp,cost,raw);ref.check(out,expected,t,u)
    def test_constructed_z_weakening_is_admitted(self):
        block,raw,extra=market()
        for k in range(31,39):
            price = D(97)+D(k%4)
            for bar in block['candles'][k*15:(k+1)*15]:
                bar.update(o=str(price),c=str(price),h='101.5',l='96.9')
        block['candles'][35*15]['h']='105'
        extra={600:reference_bb.reference_extra(block,600)}
        out,t=apply(block,raw,extra,'Z_WEAKENING')
        expected,u=ref.reference(block,spec('Z_WEAKENING'),'base_assumptions',raw)
        ref.check(out,expected,t,u)
        self.assertEqual(out[600]['direction'],-1)
        self.assertEqual(t[0]['reason'],'Z_WEAKENING')
    def test_peak_tamper_detected(self):
        block,raw,extra=market();out,t=apply(block,raw,extra);e,u=ref.reference(block,spec('LOWER_HIGH'),'base_assumptions',raw)
        t[0]['peaks'][0]['high']='10000'
        with self.assertRaises(ValueError):ref.check(out,e,t,u)
    def test_missing_event_detected(self):
        block,raw,extra=market();out,t=apply(block,raw,extra);e,u=ref.reference(block,spec('LOWER_HIGH'),'base_assumptions',raw)
        with self.assertRaises(ValueError):ref.check(out,e,[],u)


class AccountTests(unittest.TestCase):
    def test_sixteen_fixture_accounts_and_hook_restoration(self):
        block,raw,extra=market();before=core.strategy_signal;count=0
        for sp in s.specs():
            for cost in run.mean.COSTS:
                out,t=s.choices(block,sp,run.mean.COSTS[cost],raw,extra,s.contexts(block))
                expected,u=ref.reference(block,sp,cost,raw);ref.check(out,expected,t,u)
                for path in run.mean.PATHS:
                    row=run.mean.replay(block,run.cross.old.previous.filters.BASE_SPEC,cost,path,out,expected)
                    self.assertFalse(row['open_position']);self.assertFalse(row['integrity_warnings']);count+=1
        self.assertEqual(count,16);self.assertIs(before,core.strategy_signal)
    def test_corrupt_cash_rejected(self):
        block,raw,extra=market();out,t=apply(block,raw,extra)
        expected,u=ref.reference(block,spec('LOWER_HIGH'),'base_assumptions',raw)
        row=run.mean.replay(block,run.cross.old.previous.filters.BASE_SPEC,'base_assumptions','OHLC',out,expected)
        row['ending_usdc']='999'
        with self.assertRaises(ValueError):ref.audit(row,block,expected)
    def test_incomplete_grid_rejected(self):
        with self.assertRaises(ValueError):run.aggregate([])
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):run.run(Path('unused'),Path(td))
    def test_failure_evidence_no_profit_summary(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'results'
            with patch.object(run,'load_parent',side_effect=ValueError('fixture bad input')):
                with self.assertRaises(ValueError):run.run(Path('unused'),out)
            self.assertEqual(json.loads((out/'status.json').read_text())['status'],'FAILED_NO_COMPLETE_RESULT')
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'evidence-manifest.json').exists())


if __name__=='__main__':unittest.main()
