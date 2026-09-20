"""All fixtures are artificial software tests, not trading performance."""
import base64
import copy
from dataclasses import replace
import gzip
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_backtest import fixture
from paperlab import backtest as bt, engine as core
from paperlab.common import Config
import hybrids
from verify_mix import Reference,check_bank
import grid
import pipeline as p
import replay_grid as replay


def fixture_data(n=620):
    d=bt.validate_dataset(fixture(n));d['network']='mainnet';d['downloaded_ms']=d['candles'][-1]['T']+5000
    return d


class EnumerationTests(unittest.TestCase):
    def test_every_expression_and_truth_table(self):
        raw,canonical=grid.rules()
        self.assertEqual(len(raw),324);self.assertEqual(len(canonical),178)
        self.assertEqual(sum(len(x['aliases']) for x in canonical),324)
        self.assertEqual(len({tuple(x['table']) for x in canonical}),178)
        for x in canonical:
            for r in x['aliases']:
                self.assertEqual([grid.vote(s,r) for s in grid.STATES],x['table'])
    def test_all_combinations_counts(self):
        specs=grid.all_specs()
        self.assertEqual(len(specs),2424);self.assertEqual(len(p.candidates()),2434)
        self.assertEqual(sum(s['kind']=='aggregate' for s in specs),2136)
        self.assertEqual(sum(s['kind']=='sequence' for s in specs),144)
        self.assertEqual(sum(s['kind']=='regime' for s in specs),144)
        self.assertEqual(sum(s['previously_tested_as'] is not None for s in specs),4)
        self.assertEqual(sum(s['kind']!='control' for s in p.candidates()),2420)
    def test_all_rules_genuinely_use_two_or_more_families(self):
        _,rs=grid.rules();states=grid.STATES;idx={st:i for i,st in enumerate(states)}
        for r in rs:
            supported=[]
            for k in range(4):
                if any(r['table'][i]!=r['table'][idx[st[:k]+(v,)+st[k+1:]]]
                       for i,st in enumerate(states) for v in (-1,0,1)):
                    supported.append(k)
            self.assertGreaterEqual(len(supported),2)
    def test_all_ordered_pairs_present(self):
        self.assertEqual(len({tuple(s['order']) for s in grid.all_specs() if s['kind']=='sequence'}),12)
    def test_all_ordered_regime_assignments_present(self):
        self.assertEqual(len({tuple(s['order']) for s in grid.all_specs() if s['kind']=='regime'}),24)
    def test_identifier_repeatable_and_unique(self):
        a=grid.all_specs();b=grid.all_specs();self.assertEqual(a,b)
        self.assertEqual(len({s['id'] for s in a}),2424)
    def test_none_votes_never_opens(self):
        self.assertTrue(all(grid.vote((0,0,0,0),x)==0 for x in grid.rules()[0]))
    def test_conflict_policy_difference(self):
        s=dict(kind='vote',keys=[0,1,2],weights=[1,1,1],threshold=1,policy='veto')
        self.assertEqual(grid.vote((1,1,-1,0),s),0)
        s['policy']='net';self.assertEqual(grid.vote((1,1,-1,0),s),1)
    def test_priority_order_matters(self):
        a=dict(kind='priority',keys=[0,1]);b=dict(kind='priority',keys=[1,0])
        self.assertEqual(grid.vote((1,-1,0,0),a),1);self.assertEqual(grid.vote((1,-1,0,0),b),-1)
    def test_gates_do_not_change_account_risk(self):
        self.assertEqual(p.protocol()['account'],__import__('dataclasses').asdict(Config()))
        self.assertEqual(p.protocol()['real_orders'],0)
        self.assertEqual(p.protocol()['expected_scenario_rows'],19472)


class PoliciesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=fixture_data();cls.bank=hybrids.Bank(cls.data);cls.ref=Reference(cls.data)
        cls.cache=replay.Cache(cls.data)
    def test_every_policy_independent_reference(self):
        check_bank(self.bank,self.ref,hybrids.KINDS)
        for spec in grid.all_specs():
            self.assertEqual(grid.streams(self.bank.nodes,spec,620),grid.reference_streams(self.ref.nodes,spec,620),spec['id'])
    def test_prior_four_mixtures_exact_policies(self):
        for s in grid.all_specs():
            if s['previously_tested_as']:
                kind=s['previously_tested_as'][4:]
                e,x=grid.streams(self.bank.nodes,s,620)
                for i in range(120,621):
                    self.assertEqual(e[i]-1,self.bank.entry(i,kind))
                    self.assertEqual(bool(x[i]&1),self.bank.exit(i,kind,1))
                    self.assertEqual(bool(x[i]&2),self.bank.exit(i,kind,-1))
    def test_cached_atr_matches_original_every_bar(self):
        for i,s in self.cache.base.items():
            old=core.strategy_signal(self.data['candles'][i-120:i],Config())
            self.assertEqual((s.atr,s.close,s.bar_ms),(old.atr,old.close,old.bar_ms))
    def test_future_changes_cannot_change_any_policy_prefix(self):
        # All grid forms depend on causal nodes; mutate only future nodes.
        changed=copy.deepcopy(self.bank.nodes)
        for i,f in changed.items():
            if i>300:f.update(eff=1,breakout=-1,elder=1,rsi=-1,trend=-1)
        for spec in grid.all_specs():
            e,x=grid.streams(self.bank.nodes,spec,620);a,b=grid.streams(changed,spec,620)
            self.assertEqual(e[:301],a[:301]);self.assertEqual(x[:301],b[:301])
    def test_original_mixture_ledger_equivalence_all_eight_assumptions(self):
        # Four grid rules equal old mixtures. Every cost/path uses identical original ledger.
        for s in grid.all_specs():
            if s['previously_tested_as']:
                old=next(z for z in hybrids.candidates() if z['id']==s['previously_tested_as'])
                e,x=grid.streams(self.bank.nodes,s,620)
                for cost,path in product(bt.COSTS,bt.PATHS):
                    actual=replay.replay(self.cache,120,620,e[120:620],x[120:620],cost,path)
                    expected=hybrids.replay(self.data,120,620,old,cost,path,self.bank)
                    for k in ('model_net_pnl_usdc','model_end_usdc','model_max_drawdown_pct','closed_trades',
                              'fees_usdc','estimated_funding_usdc','halt_ms','halt_reason','pending_funding','integrity_warnings'):
                        self.assertEqual(actual[k],expected[k],k)
                    for a,b in zip(actual['trades'],expected['trades']):
                        for k in set(a)-{'open_reason','close_reason'}:self.assertEqual(a[k],b[k],k)
    def test_invalid_stream_refused(self):
        with self.assertRaises(ValueError):replay.replay(self.cache,120,620,b'\x09'*500,b'\x00'*500,'base_assumptions','OHLC')
    def test_hook_restored_after_exception(self):
        values=bt.DirectionEngine,core.strategy_signal,bt.assumed_quote
        with self.assertRaises(RuntimeError):
            with replay.installed(self.cache,b'\x01'*500,b'\x00'*500,120):raise RuntimeError('stop')
        self.assertEqual(values,(bt.DirectionEngine,core.strategy_signal,bt.assumed_quote))
    def test_cached_quote_immutable_and_matching(self):
        t=self.data['candles'][125]['t']+2000
        a=self.cache.quotes(t,'3000','1');b=bt.assumed_quote(t,'3000','1',self.data['metadata'])
        self.assertEqual(a,b)
        with self.assertRaises(Exception):a.mark=0
    def test_ledger_mutation_detected(self):
        s=next(z for z in grid.all_specs() if z['previously_tested_as']=='MIX_UNION_VETO')
        e,x=grid.streams(self.bank.nodes,s,620);e,x=e[120:620],x[120:620]
        r=replay.replay(self.cache,120,620,e,x,'base_assumptions','OHLC')
        self.assertTrue(r['trades']);replay.audit(r,self.data,120,e,x)
        r['trades'][0]['entry_fee']='1'
        with self.assertRaises(ValueError):replay.audit(r,self.data,120,e,x)
    def test_entry_mutation_detected(self):
        s=next(z for z in grid.all_specs() if z['previously_tested_as']=='MIX_UNION_VETO')
        e,x=grid.streams(self.bank.nodes,s,620);e,x=e[120:620],x[120:620]
        r=replay.replay(self.cache,120,620,e,x,'base_assumptions','OHLC')
        with self.assertRaises(ValueError):replay.audit(r,self.data,120,b'\x01'*500,x)
    def test_sequence_requires_strict_prior_setup(self):
        from decimal import Decimal as D
        nodes={i:dict(t=i*60000-1,eff=0,breakout=0,elder=0,rsi=0,trend=0,er=D('.25'),k=D(50)) for i in range(120,126)}
        s=dict(kind='sequence',order=[0,1],horizon=1,gate='none',exit='opposite')
        nodes[124].update(eff=1,breakout=1)
        e,_=grid.streams(nodes,s,125);self.assertEqual(e[124],1)
        nodes[125]['breakout']=1;e,_=grid.streams(nodes,s,125);self.assertEqual(e[125],2)
    def test_regime_decimal_boundaries(self):
        from decimal import Decimal as D
        s=dict(kind='regime',order=[0,1,2],horizon=1,gate='none',exit='opposite')
        nodes={120:dict(t=7199999,eff=1,breakout=-1,elder=0,rsi=0,trend=0,er=D('.35'),k=D(50))}
        e,_=grid.streams(nodes,s,120);self.assertEqual(e[120],2)
        nodes[120]['er']=D('.20');e,_=grid.streams(nodes,s,120);self.assertEqual(e[120],1)
    def test_stream_alias_reuses_only_exact_inputs(self):
        specs=[s for s in grid.all_specs() if s['kind']=='aggregate'][:30]
        groups={}
        for spec in specs:
            e,x=grid.streams(self.bank.nodes,spec,620)
            key=e[120:]+x[120:];groups.setdefault(key,[]).append(spec)
        for same in groups.values():
            a=grid.streams(self.bank.nodes,same[0],620)
            for s in same[1:]:self.assertEqual(a,grid.streams(self.bank.nodes,s,620))


class EvidenceTests(unittest.TestCase):
    def test_manifest_detects_changed_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'data.json').write_text('{}');p.manifest(r);p.check_manifest(r)
            (r/'data.json').write_text('{"x":1}')
            with self.assertRaises(ValueError):p.check_manifest(r)
    def test_manifest_path_escape_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);(r/'manifest.json').write_text('{"../outside":"bad"}')
            with self.assertRaises(ValueError):p.check_manifest(r)
    def test_invalid_shard_refused(self):
        with self.assertRaises(ValueError):p.worker(Path('.'),Path('DO_NOT_CREATE'),8)
    def test_missing_shard_no_positive_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);out=r/'out'
            with patch.object(p,'check_prepared'),patch.object(p,'load',return_value=[]):
                with self.assertRaises(Exception):p.aggregate(r,r,out)
            self.assertFalse((out/'SUMMARY.json').exists());self.assertTrue((out/'status.json').exists())
    def test_output_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):p.aggregate(Path(tmp),Path(tmp),Path(tmp))
    def test_base64_strict(self):
        with self.assertRaises(Exception):p.unb64('??')
    def test_compressed_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'test.json.gz';p.compressed(f,{'kind':'SYNTHETIC_FIXTURE'});self.assertEqual(p.load(f),{'kind':'SYNTHETIC_FIXTURE'})




class AggregateCoverageTests(unittest.TestCase):
    def create_fixture(self,root):
        specs=p.candidates();members=[s['id'] for s in specs]
        prepared=root/'prepared';prepared.mkdir()
        results=root/'results';results.mkdir()
        tasks=[];d=fixture_data();cache=replay.Cache(d)
        for index,(phase,cost,path) in enumerate(product(('DEVELOPMENT_SEEN','NEW_PERIOD_EVALUATION'),bt.COSTS,bt.PATHS)):
            t=dict(task_id=str(index),key='SYNTHETIC_IDENTICAL_ZERO_POLICIES',phase=phase,cost=cost,path=path,
                   shard=index,members=members,data_sha=p.digest(d))
            tasks.append(t)
            r=replay.replay(cache,120,620,b'\x01'*500,b'\x00'*500,cost,path)
            r['independent_audit']=replay.audit(r,d,120,b'\x01'*500,b'\x00'*500)
            p.stats.add_statistics(r,d,120,620)
            r.update(task_id=str(index),stream_key=t['key'],phase=phase,members=members,data_sha=t['data_sha'],**p.identities())
            folder=results/f'shard-{index}';folder.mkdir()
            with gzip.open(folder/'results.jsonl.gz','wt',encoding='utf-8') as f:f.write(json.dumps(r)+'\n')
            p.write_json(folder/'status.json',dict(status='COMPLETE',shard=index,task_ids=[str(index)]))
            p.manifest(folder)
        p.compressed(prepared/'tasks.json.gz',tasks)
        p.write_json(prepared/'candidates.json',specs)
        p.write_json(prepared/'preparation.json',dict(new_bars=500,window_start='SYNTHETIC START',window_end='SYNTHETIC END',data_hashes={}))
        p.write_json(prepared/'frozen.json',dict(source='SYNTHETIC SOFTWARE TEST ONLY'))
        return prepared,results
    def test_full_19472_mappings_end_to_end_synthetic(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared,results=self.create_fixture(root)
            with patch.object(p,'check_prepared'),redirect_stdout(io.StringIO()):p.aggregate(prepared,results,root/'out')
            s=json.loads((root/'out/SUMMARY.json').read_text())
            self.assertEqual(s['candidate_scenario_results'],19472)
            self.assertEqual(s['actual_core_replays'],8)
            self.assertEqual(s['qualified55_ids'],[])
            self.assertEqual(s['counts']['NEW_PERIOD_EVALUATION']['by_sign'],{'NO_TRADES':2420})
            self.assertFalse(s['statistically_validated_strategy'])
    def test_duplicate_task_rejected_even_with_new_manifest(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared,results=self.create_fixture(root)
            folder=results/'shard-1';rows=list(p.read_rows(folder/'results.jsonl.gz'));rows[0]['task_id']='0'
            with gzip.open(folder/'results.jsonl.gz','wt',encoding='utf-8') as f:f.write(json.dumps(rows[0])+'\n')
            p.manifest(folder)
            with patch.object(p,'check_prepared'),redirect_stdout(io.StringIO()),self.assertRaises(ValueError):p.aggregate(prepared,results,root/'out')
            self.assertFalse((root/'out/SUMMARY.json').exists())
    def test_wrong_members_rejected_even_with_new_manifest(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared,results=self.create_fixture(root)
            folder=results/'shard-0';r=next(p.read_rows(folder/'results.jsonl.gz'));r['members']=r['members'][:-1]
            with gzip.open(folder/'results.jsonl.gz','wt',encoding='utf-8') as f:f.write(json.dumps(r)+'\n')
            p.manifest(folder)
            with patch.object(p,'check_prepared'),redirect_stdout(io.StringIO()),self.assertRaises(ValueError):p.aggregate(prepared,results,root/'out')
            self.assertFalse((root/'out/SUMMARY.json').exists())
    def test_failed_shard_is_not_zero_profit(self):
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);prepared,results=self.create_fixture(root)
            folder=results/'shard-0';status=p.load(folder/'status.json');status['status']='FAILED';p.write_json(folder/'status.json',status);p.manifest(folder)
            with patch.object(p,'check_prepared'),redirect_stdout(io.StringIO()),self.assertRaises(ValueError):p.aggregate(prepared,results,root/'out')
            self.assertFalse((root/'out/SUMMARY.json').exists())

if __name__=='__main__':unittest.main()
