"""Synthetic unit/regression tests. No market-performance claims."""
import copy
from contextlib import redirect_stdout
from decimal import Decimal as D
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paperlab import backtest as bt, engine as core
from test_backtest import fixture
import hybrids as h
import verify_mix as v
import run_mix as runner


def data(n=680):
    d=bt.validate_dataset(fixture(n))
    d.update(network='mainnet',downloaded_ms=d['candles'][-1]['T']+5000)
    return d


def node(**kwargs):
    f=dict(t=7199999,er=D('.25'),trend=1,k=D(50),eff=0,breakout=0,elder=0,rsi=0,seq_up=False,seq_down=False)
    f.update(kwargs)
    return f


def fake_bank(**f):
    b=h.Bank.__new__(h.Bank)
    b.nodes={i:node(t=60000*i-1) for i in range(120,125)}
    b.nodes[124].update(f)
    return b


class CombinationTests(unittest.TestCase):
    def test_grid_count_and_unique(self):
        self.assertEqual(len(h.candidates()),8)
        self.assertEqual(len(h.controls()),6)
        self.assertEqual(len({s['id'] for s in h.candidates()+h.controls()}),14)
    def test_primary_predeclared(self):self.assertEqual(runner.protocol()['primary'],'MIX_VOTE_2OF3')
    def test_break_requires_trend(self):
        self.assertEqual(fake_bank(breakout=1,trend=-1).entry(124,'BREAK_TREND'),0)
        self.assertEqual(fake_bank(breakout=-1,trend=-1).entry(124,'BREAK_TREND'),-1)
    def test_pullback_requires_efficiency(self):
        self.assertEqual(fake_bank(elder=1,er=D('.19')).entry(124,'PULLBACK_EFF'),0)
        self.assertEqual(fake_bank(elder=1,er=D('.20')).entry(124,'PULLBACK_EFF'),1)
    def test_union_accepts_each_component(self):
        for k in ('eff','breakout','elder','rsi'):
            self.assertEqual(fake_bank(**{k:1}).entry(124,'UNION_VETO'),1)
    def test_union_conflict_veto(self):self.assertEqual(fake_bank(eff=1,rsi=-1).entry(124,'UNION_VETO'),0)
    def test_union_no_votes(self):self.assertEqual(fake_bank().entry(124,'UNION_VETO'),0)
    def test_vote_needs_two_families(self):self.assertEqual(fake_bank(eff=1).entry(124,'VOTE_2OF3'),0)
    def test_vote_two_long(self):self.assertEqual(fake_bank(eff=1,elder=1).entry(124,'VOTE_2OF3'),1)
    def test_vote_two_short(self):self.assertEqual(fake_bank(breakout=-1,elder=-1).entry(124,'VOTE_2OF3'),-1)
    def test_vote_majority_conflict(self):self.assertEqual(fake_bank(eff=1,elder=1,breakout=-1).entry(124,'VOTE_2OF3'),0)
    def test_recent_signal_retained_two_bars(self):
        b=fake_bank(elder=1);b.nodes[122]['eff']=1
        self.assertEqual(b.entry(124,'VOTE_2OF3'),1)
    def test_recent_three_bars_expired(self):
        b=fake_bank(elder=1);b.nodes[121]['eff']=1
        self.assertEqual(b.entry(124,'VOTE_2OF3'),0)
    def test_time_gap_expires_vote(self):
        b=fake_bank(elder=1);b.nodes[123].update(eff=1,t=60000*120-1)
        self.assertEqual(b.entry(124,'VOTE_2OF3'),0)
    def test_newest_nonzero_family_vote(self):
        b=fake_bank(elder=-1);b.nodes[122]['eff']=1;b.nodes[123]['eff']=-1
        self.assertEqual(b.recent(124,'eff'),-1)
    def test_same_family_not_multiple_votes(self):
        b=fake_bank(eff=1);b.nodes[123]['eff']=1;b.nodes[122]['eff']=1
        self.assertEqual(b.entry(124,'VOTE_2OF3'),0)
    def test_persistent_consensus_not_reentered(self):
        b=fake_bank();b.nodes[123].update(eff=1,elder=1)
        self.assertEqual(b.entry(123,'VOTE_2OF3'),1)
        self.assertEqual(b.entry(124,'VOTE_2OF3'),0)
    def test_weighted_elder_alone_not_enough(self):self.assertEqual(fake_bank(elder=1).entry(124,'WEIGHTED_VOTE'),0)
    def test_weighted_elder_plus_one(self):self.assertEqual(fake_bank(elder=1,rsi=1).entry(124,'WEIGHTED_VOTE'),1)
    def test_weighted_opposing_vote_veto(self):self.assertEqual(fake_bank(elder=1,rsi=1,eff=-1).entry(124,'WEIGHTED_VOTE'),0)
    def test_regime_trending(self):self.assertEqual(fake_bank(er=D('.35'),breakout=1,rsi=-1).entry(124,'REGIME_SWITCH'),1)
    def test_regime_range(self):self.assertEqual(fake_bank(er=D('.20'),breakout=1,rsi=-1).entry(124,'REGIME_SWITCH'),-1)
    def test_regime_middle(self):self.assertEqual(fake_bank(er=D('.25'),breakout=-1,elder=1).entry(124,'REGIME_SWITCH'),1)
    def test_sequence_setup_and_trigger(self):
        self.assertEqual(fake_bank(seq_up=True).entry(124,'SEQUENCE'),1)
        self.assertEqual(fake_bank(seq_up=False).entry(124,'SEQUENCE'),0)
    def test_sequence_short(self):self.assertEqual(fake_bank(seq_down=True,trend=-1).entry(124,'SEQUENCE'),-1)
    def test_union_dual_entry_unchanged(self):
        for k in ('eff','breakout','elder','rsi'):
            b=fake_bank(**{k:-1});self.assertEqual(b.entry(124,'UNION_DUAL_EXIT'),b.entry(124,'UNION_VETO'))
    def test_dual_exit_strength(self):
        b=fake_bank(k=D(90))
        self.assertTrue(b.exit(124,'UNION_DUAL_EXIT',1))
        self.assertFalse(b.exit(124,'UNION_VETO',1))
    def test_dual_exit_reverse_trend(self):self.assertTrue(fake_bank(trend=-1).exit(124,'UNION_DUAL_EXIT',1))
    def test_opposite_signal_exit(self):self.assertTrue(fake_bank(eff=-1).exit(124,'UNION_VETO',1))
    def test_unknown_mode(self):
        with self.assertRaises(ValueError):fake_bank().entry(124,'live')
    def test_warmup_length(self):
        with self.assertRaises(ValueError):h.facts(data()['candles'][:119])


class CausalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=data();cls.bank=h.Bank(cls.data);cls.ref=v.Reference(cls.data)
    def test_independent_all_features_and_decisions(self):
        self.assertEqual(v.check_bank(self.bank,self.ref,h.KINDS)['status'],'PASS_INDEPENDENT_CAUSAL_COMPONENTS_AND_COMBINATIONS')
    def test_future_data_cannot_change_prefix(self):
        d=copy.deepcopy(self.data)
        for b in d['candles'][440:]:
            for k in 'ohlc':b[k]=str(D(b[k])*2)
        changed=h.Bank(d)
        for i in range(120,441):
            self.assertEqual(changed.nodes[i],self.bank.nodes[i])
            for mode in h.KINDS:self.assertEqual(changed.entry(i,mode),self.bank.entry(i,mode))
    def test_incomplete_five_minute_not_used(self):
        bars=copy.deepcopy(self.data['candles'][1:121])
        a=h.facts(bars);bars[-1].update(c='9999',h='9999')
        b=h.facts(bars)
        self.assertEqual(a['trend'],b['trend'])
        self.assertEqual(a['rsi'],0);self.assertEqual(b['rsi'],0)
    def test_feature_mutation_detected(self):
        b=copy.copy(self.bank);b.nodes=copy.deepcopy(self.bank.nodes);b.nodes[200]['eff']=7
        with self.assertRaises(ValueError):v.check_bank(b,self.ref,h.KINDS)
    def test_hook_restored_on_error(self):
        eng,sig=bt.DirectionEngine,core.strategy_signal
        with self.assertRaises(RuntimeError):
            with h.installed(h.candidates()[0],self.bank,__import__('collections').Counter()):raise RuntimeError('stop')
        self.assertIs(bt.DirectionEngine,eng);self.assertIs(core.strategy_signal,sig)
    def test_unknown_spec_refused(self):
        s=h.candidates()[0];s['extra']='changed'
        with self.assertRaises(ValueError):h.replay(self.data,120,680,s,'base_assumptions','OHLC',self.bank)
    def test_wrong_bank_refused(self):
        with self.assertRaises(ValueError):h.replay(copy.deepcopy(self.data),120,680,h.candidates()[0],'base_assumptions','OHLC',self.bank)
    def test_single_shared_account_and_audit(self):
        for s in h.candidates():
            r=runner.evaluate(self.data,120,680,s,'base_assumptions','OHLC','SYNTHETIC_TEST',self.bank,self.ref)
            self.assertEqual(D(r['initial_usdc']),D(10))
            self.assertFalse(r['open_position_remaining'])
            self.assertEqual(r['closed_trades'],len(r['trades']))
            for a,b in zip(r['trades'],r['trades'][1:]):self.assertLessEqual(a['closed_ms'],b['opened_ms'])
            self.assertLessEqual(max((D(t['initial_margin_model']) for t in r['trades']),default=D(0)),D('6.5'))
    def test_single_strategy_controls_exact(self):
        for s in h.controls():
            actual=h.replay(self.data,120,680,s,'base_assumptions','OHLC',self.bank)
            expected=h.books.replay(self.data,120,680,s,'base_assumptions','OHLC') if s['family'].startswith('book_') else h.old.replay(self.data,120,680,s,'base_assumptions','OHLC')
            self.assertEqual(actual['trades'],expected['trades'])
    def test_trade_mutations_rejected(self):
        spec=next(s for s in h.candidates() if s['kind']=='UNION_VETO')
        base=runner.evaluate(self.data,120,680,spec,'base_assumptions','OHLC','SYNTHETIC_TEST',self.bank,self.ref)
        self.assertTrue(base['trades'])
        for field in ('entry_fee','gross_pnl'):
            row=copy.deepcopy(base);row['trades'][0][field]=str(D(row['trades'][0][field])+1)
            with self.assertRaises(ValueError):v.audit(row,self.data,spec,self.ref)
    def test_component_trace_mutation_rejected(self):
        spec=next(s for s in h.candidates() if s['kind']=='UNION_VETO')
        row=runner.evaluate(self.data,120,680,spec,'base_assumptions','OHLC','SYNTHETIC_TEST',self.bank,self.ref)
        row['trades'][0]['mix_trace']['components']['rsi']=7
        with self.assertRaises(ValueError):v.audit(row,self.data,spec,self.ref)
    def test_future_timestamp_cannot_vote(self):
        bank=fake_bank(elder=1);bank.nodes[123].update(eff=1,t=bank.nodes[124]['t']+60000)
        self.assertEqual(bank.entry(124,'VOTE_2OF3'),0)
    def test_incomplete_aggregate_refused(self):
        with self.assertRaises(ValueError):runner.aggregate([])


class PipelineTests(unittest.TestCase):
    def test_full_112_synthetic_cases_freeze_before_fetch(self):
        initial,fresh=data(620),data(680)
        cutoff=initial['candles'][-1]['T']+1
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'experiment'
            def fetch(client):
                frozen=json.loads((out/'frozen.json').read_text())
                self.assertEqual(len(frozen['all_specs']),14)
                self.assertEqual(frozen['primary'],h.PRIMARY)
                self.assertEqual(len(json.loads((out/'development.json').read_text())),56)
                return fresh
            with patch.object(runner,'KNOWN_CUTOFF',cutoff),patch.object(runner,'known_data',return_value=(initial,{})),patch.object(runner,'fetch_dataset',side_effect=fetch),patch.object(runner.v,'raw_check',return_value={'status':'SYNTHETIC_TEST_ONLY'}),redirect_stdout(io.StringIO()):
                code=runner.run(Path(tmp),Path(tmp),out)
            self.assertEqual(code,0,(out/'status.json').read_text())
            results=json.loads((out/'all-results.json').read_text());summary=json.loads((out/'SUMMARY.json').read_text())
            self.assertEqual(len(results),112)
            self.assertEqual(len({(r['phase'],r['candidate'],r['cost'],r['path']) for r in results}),112)
            self.assertEqual(summary['new_bars'],60)
            self.assertEqual(summary['primary_id'],h.PRIMARY)
            self.assertEqual(summary['qualified_55'],[])
            self.assertEqual(summary['real_orders'],0)
            self.assertTrue((out/'all_scenarios.csv').is_file())
            self.assertEqual(len(summary['evaluation_candidates']),14)
    def test_failure_keeps_evidence_no_positive_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'experiment'
            with patch.object(runner,'known_data',side_effect=ValueError('corrupt evidence')),redirect_stdout(io.StringIO()):
                self.assertEqual(runner.run(Path(tmp),Path(tmp),out),2)
            self.assertFalse((out/'SUMMARY.json').exists())
            self.assertEqual(json.loads((out/'status.json').read_text())['status'],'FAILED_NO_VALIDATED_RESULT')
            self.assertTrue((out/'evidence-manifest.json').exists())
    def test_output_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):runner.run(Path(tmp),Path(tmp),Path(tmp))


if __name__=='__main__':unittest.main()
