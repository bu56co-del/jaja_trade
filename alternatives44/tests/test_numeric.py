"""Regression tests for indicator arithmetic ties, not market outcomes."""
import unittest
from decimal import Decimal as D
from test_alt import node,spec
import signals_alt as s
import reference_alt as r


class NumericTests(unittest.TestCase):
    def test_numerically_equal_bandwidth_is_not_expansion(self):
        n=node(close=D(102),rsi14=D(60))
        n['bands']['20']['width']=D('.02')+D('1e-24')
        self.assertEqual(s.raw_direction(n,spec('BB_EXPAND')),0)
        self.assertEqual(r.direction_reference(n,spec('BB_EXPAND')),0)
    def test_material_bandwidth_increase_remains_expansion(self):
        n=node(close=D(102),rsi14=D(60))
        n['bands']['20']['width']=D('.02')+D('1e-10')
        self.assertEqual(s.raw_direction(n,spec('BB_EXPAND')),1)
        self.assertEqual(r.direction_reference(n,spec('BB_EXPAND')),1)


if __name__=="__main__":unittest.main()
