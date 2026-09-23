"""Provenance label normalization must not weaken any baseline ledger check."""
import copy
import unittest
from run_fixed import check_baseline

SOURCE='RETROSPECTIVE_DISCONNECTED_A44_MODEL_ONLY'
SEPTEMBER='SAVED_SEPTEMBER_CROSS_PERIOD_MODEL_ONLY'


class BaselineLabelTests(unittest.TestCase):
    def rows(self):
        a=dict(candidate='engine',evidence=SOURCE,ending_usdc='10.1',
               trades=[dict(id=1,qty='0.01',opened_ms=2,closed_ms=21000)],
               halt_reason=None,sampled_max_drawdown_pct='1.2')
        b=dict(copy.deepcopy(a),candidate='V030_R30',evidence=SEPTEMBER)
        return a,b
    def test_september_label_and_all_values_match(self):
        a,b=self.rows();c=check_baseline(a,b,'SEPTEMBER')
        self.assertEqual(c['evidence'],SEPTEMBER)
        self.assertEqual(a['evidence'],SOURCE)
        self.assertEqual(c['trades'],b['trades'])
    def test_a44_label_unchanged(self):
        a,b=self.rows();b['evidence']=SOURCE
        self.assertEqual(check_baseline(a,b,'A44'),a)
    def test_unknown_new_label_refused(self):
        a,b=self.rows();a['evidence']='NEW'
        with self.assertRaises(ValueError):check_baseline(a,b,'SEPTEMBER')
    def test_unknown_saved_label_refused(self):
        a,b=self.rows();b['evidence']='NEW'
        with self.assertRaises(ValueError):check_baseline(a,b,'SEPTEMBER')
    def test_unknown_dataset_refused(self):
        a,b=self.rows()
        with self.assertRaises(ValueError):check_baseline(a,b,'OTHER')
    def test_changed_balance_refused(self):
        a,b=self.rows();a['ending_usdc']='11'
        with self.assertRaises(ValueError):check_baseline(a,b,'SEPTEMBER')
    def test_changed_trade_or_time_refused(self):
        for k,v in (('qty','0.02'),('closed_ms',40000)):
            a,b=self.rows();a['trades'][0][k]=v
            with self.assertRaises(ValueError):check_baseline(a,b,'SEPTEMBER')
    def test_changed_risk_refused(self):
        a,b=self.rows();a['sampled_max_drawdown_pct']='0.1'
        with self.assertRaises(ValueError):check_baseline(a,b,'SEPTEMBER')

if __name__=='__main__':unittest.main()
