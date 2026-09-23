"""Artificial tests for side ablation; no market performance claims."""
from pathlib import Path
import sys,copy,tempfile,unittest
sys.path.append(str(Path(__file__).parents[2]/'reversal44/tests'))
from test_patterns import artificial
import run_direction as d
import signals_mean as s
import verify_mean as v
import run_mean as m

class Tests(unittest.TestCase):
 def test_grid(self):self.assertEqual(len(d.specs()),6)
 def test_unknown_side(self):
  with self.assertRaises(ValueError):d.gated({},'bad')
 def test_long_filter(self):self.assertEqual([x['direction'] for x in d.gated({i:{'direction':x} for i,x in enumerate([-1,0,1])},'LONG_ONLY').values()],[0,0,1])
 def test_short_filter(self):self.assertEqual([x['direction'] for x in d.gated({i:{'direction':x} for i,x in enumerate([-1,0,1])},'SHORT_ONLY').values()],[-1,0,0])
 def test_both_identity(self):
  x={0:dict(direction=1,exit_long=False),1:dict(direction=-1,exit_short=False)};self.assertEqual(d.gated(x,'BOTH'),x)
 def test_reference_filter_agrees(self):
  x={i:{'direction':x} for i,x in enumerate([-1,0,1])}
  for side in d.SIDES:self.assertEqual(d.gated(x,side),d.reference_gate(x,side))
 def test_no_input_mutation(self):
  x={0:dict(direction=-1)};d.gated(x,'LONG_ONLY');self.assertEqual(x,{0:dict(direction=-1)})
 def test_empty_combine_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(ValueError):d.combine(Path(t),Path(t)/'out')
 def test_replay_each_variant_with_independent_ledger(self):
  b=artificial(1200);fv=s.features(b,15)
  for base in d.BASES:
   bs=next(x for x in s.specs() if x['id']==base);cs=s.choices(b,fv,bs);ref=v.reference(b,bs);original=m.replay(b,bs,'base_assumptions','OHLC',cs,ref)
   for sp in [x for x in d.specs() if x['base_id']==base]:
    row=d.replay(b,sp,'base_assumptions','OHLC',cs,ref,original)
    self.assertEqual(row['initial_usdc'],'10');self.assertFalse(row['open_position'])
    self.assertTrue(all(sp['side']=='BOTH' or t['direction']==(1 if sp['side']=='LONG_ONLY' else -1) for t in row['trades']))
 def test_both_requires_exact_parent(self):
  b=artificial(1200);sp=d.specs()[0];bs=d.base_spec(sp);cs=s.choices(b,s.features(b,15),bs);ref=v.reference(b,bs)
  with self.assertRaises(ValueError):d.replay(b,sp,'base_assumptions','OHLC',cs,ref,{})

if __name__=='__main__':unittest.main()
