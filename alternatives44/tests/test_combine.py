"""End-to-end reporting fixtures, not market backtests."""
import gzip
import hashlib
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_alt as run


class CombineTests(unittest.TestCase):
    def fixture(self, root):
        prov={k:os.environ.get(k) for k in ('GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')}
        for group in range(4):
            p=root/str(group);p.mkdir()
            rows=[]
            for spec in run.specs()[group::4]:
                for i in range(27):
                    for cost in run.COSTS:
                        for path in run.PATHS:
                            active=spec['family']=='BASELINE' and i==0
                            m=dict(trades=int(active),wins=0,net_usdc='-0.1' if active else '0',
                                   fixed_trades_stress_net_usdc='-0.2' if active else '0',
                                   price_only_usdc='0',fees_usdc='0.1' if active else '0',
                                   spread_slippage_usdc='0',funding_usdc='0',target_cap_haircut_usdc='0')
                            rows.append(dict(candidate=spec['id'],block_id=f'block_{i:02d}',cost=cost,path=path,
                                             metrics=m,trades=[{}] if active else [],halt_reason=None,
                                             sampled_max_drawdown_pct='1' if active else '0',decisions={}))
            with gzip.open(p/'results.jsonl.gz','wt') as f:
                for row in rows:f.write(json.dumps(row)+'\n')
            (p/'SUMMARY.json').write_text(json.dumps(dict(status='COMPLETED',group=group,provenance=prov,
                data_checks_sha256='fixture-only',protocol_sha256='fixture-only',baseline_cases=108 if group==0 else 0,
                trades=4 if group==0 else 0,funding_events=0,
                result_sha256=hashlib.sha256((p/'results.jsonl.gz').read_bytes()).hexdigest())))

    def test_full_2700_report_retains_trading_loser_not_zero_trade_winner(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'input';root.mkdir();self.fixture(root)
            out=Path(td)/'out'
            with redirect_stdout(io.StringIO()):run.combine(root,out)
            s=json.loads((out/'SUMMARY.json').read_text());a=json.loads((out/'aggregate.json').read_text())
            self.assertEqual((s['cases'],len(a)),(2700,100))
            self.assertEqual(s['best_trading_base']['candidate'],run.BASELINE)
            self.assertEqual(s['best_trading_base']['wins'],0)
            self.assertEqual(s['best_trading_base']['net_sum_usdc'],'-0.1')
            self.assertEqual(s['base_positive'],[])
            self.assertEqual(s['robust_positive'],[])
            self.assertTrue((out/'aggregate.csv').is_file())
            with gzip.open(out/'all-results.jsonl.gz','rt') as f:self.assertEqual(len(f.readlines()),2700)

    def test_corrupted_results_refused_before_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'input';root.mkdir();self.fixture(root);out=Path(td)/'out'
            p=root/'0/results.jsonl.gz';p.write_bytes(p.read_bytes()+b'changed')
            with self.assertRaises(ValueError):run.combine(root,out)
            self.assertFalse((out/'SUMMARY.json').exists())


if __name__=='__main__':unittest.main()
