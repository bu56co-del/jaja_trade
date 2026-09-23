"""Frozen signal combinations. Public-data replay only; no networking here."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D

from paperlab import backtest as bt, engine as core
import strategies as old
import book_strategies as books

PRIMARY = 'MIX_VOTE_2OF3'
KINDS = ('BREAK_TREND', 'PULLBACK_EFF', 'UNION_VETO', 'VOTE_2OF3',
         'WEIGHTED_VOTE', 'REGIME_SWITCH', 'SEQUENCE', 'UNION_DUAL_EXIT')
ELDER = next(s for s in books.candidates() if s['id']=='ELDER_ADAPT_EMA3_8_K5_30')
RSI = next(s for s in books.candidates() if s['id']=='CONNORS_ADAPT_SMA60_RSI2_10')


def candidates():
    return [dict(id='MIX_'+k, family='mix', kind=k, fast=9, slow=21) for k in KINDS]


def controls():
    names = ('CONTROL_EMA9_21', 'POST_SELECTED_EMA16_36',
             'EFF_16_36_20_0.20', 'BREAK_20_0.20')
    return [next(s for s in old.candidates() if s['id']==n) for n in names]+[ELDER.copy(), RSI.copy()]


def facts(bars):
    if len(bars)!=120:
        raise ValueError('Exactly 120 completed candles required')
    er = old.efficiency(bars, 20)
    c = [D(b['c']) for b in bars]
    fast, slow = core.ema(c,16), core.ema(c,36)
    cross = (1 if fast[-2]<=slow[-2] and fast[-1]>slow[-1] else
             -1 if fast[-2]>=slow[-2] and fast[-1]<slow[-1] else 0)
    ef, rf = books.features(bars,ELDER), books.features(bars,RSI)
    return dict(t=bars[-1]['T'], er=er, trend=ef['trend'], k=ef['k'],
        eff=cross if er>=D('.20') else 0,
        breakout=old.breakout_direction(bars,20) if er>=D('.20') else 0,
        elder=books.entry(ef,ELDER), rsi=books.entry(rf,RSI),
        seq_up=bool(any(x is not None and x<=30 for x in ef['prior_k']) and
                    c[-1]>max(D(b['h']) for b in bars[-4:-1])),
        seq_down=bool(any(x is not None and x>=70 for x in ef['prior_k']) and
                      c[-1]<min(D(b['l']) for b in bars[-4:-1])))


def consensus(values, weights, threshold):
    """Opposing nonzero votes veto, irrespective of majority/weight."""
    signs={x for x in values if x}
    if len(signs)!=1:
        return 0
    direction=next(iter(signs))
    return direction if sum(w for x,w in zip(values,weights) if x==direction)>=threshold else 0


class Bank:
    """Immutable-in-use causal features; index i only depends on bars[:i]."""
    def __init__(self, data):
        self.bars=data['candles']
        self.by_end={b['T']:i+1 for i,b in enumerate(self.bars)}
        self.nodes={i:facts(self.bars[i-120:i]) for i in range(120,len(self.bars)+1)}

    def recent(self,i,key):
        # Latest nonzero signal from this family, not several votes for one family.
        for j in range(i,max(119,i-3),-1):
            f=self.nodes.get(j)
            if f and 0<=self.nodes[i]['t']-f['t']<=120000 and f[key]:
                return f[key]
        return 0

    def raw(self,i,kind):
        f=self.nodes.get(i)
        if f is None:
            return 0
        if kind=='BREAK_TREND':
            return f['breakout'] if f['breakout']==f['trend'] else 0
        if kind=='PULLBACK_EFF':
            return f['elder'] if f['er']>=D('.20') else 0
        if kind in ('UNION_VETO','UNION_DUAL_EXIT'):
            return consensus([f[k] for k in ('eff','breakout','elder','rsi')],[1]*4,1)
        if kind in ('VOTE_2OF3','WEIGHTED_VOTE'):
            keys=('eff','breakout','elder') if kind=='VOTE_2OF3' else ('eff','breakout','elder','rsi')
            return consensus([self.recent(i,k) for k in keys],
                             [1,1,1] if kind=='VOTE_2OF3' else [1,1,2,1],
                             2 if kind=='VOTE_2OF3' else 3)
        if kind=='REGIME_SWITCH':
            if f['er']>=D('.35'):
                return f['breakout'] if f['breakout']==f['trend'] else 0
            if f['er']<=D('.20'):
                return f['rsi']
            return f['elder']
        if kind=='SEQUENCE':
            if f['er']<D('.20'):
                return 0
            return 1 if f['trend']==1 and f['seq_up'] else -1 if f['trend']==-1 and f['seq_down'] else 0
        raise ValueError('Unknown combination')

    def entry(self,i,kind):
        value=self.raw(i,kind)
        if kind in ('VOTE_2OF3','WEIGHTED_VOTE') and value==self.raw(i-1,kind):
            return 0  # no repeated entry from a persistent consensus
        return value

    def exit(self,i,kind,direction):
        if self.entry(i,kind)==-direction:
            return True
        f=self.nodes[i]
        if kind in ('PULLBACK_EFF','SEQUENCE','UNION_DUAL_EXIT'):
            return f['trend']==-direction or (f['k']>=80 if direction==1 else f['k']<=20)
        return False

    def trace(self,i,kind):
        f=self.nodes[i]
        return dict(signal_bar_ms=f['t'], combination=kind,
            components={k:f[k] for k in ('eff','breakout','elder','rsi')},
            recent_votes={k:self.recent(i,k) for k in ('eff','breakout','elder','rsi')},
            efficiency=str(f['er']),higher_trend=f['trend'],
            route=('breakout' if f['er']>=D('.35') else 'rsi' if f['er']<=D('.20') else 'elder')
                  if kind=='REGIME_SWITCH' else kind)


@contextmanager
def installed(spec,bank,diagnostic):
    original_engine,original_signal=bt.DirectionEngine,core.strategy_signal
    holder={}
    class MixedEngine(original_engine):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            holder['engine']=self
        def decision(self,at,code,message):
            diagnostic[code]+=1
            return super().decision(at,code,message)
        def open_position(self,quote,signal):
            opened=super().open_position(quote,signal)
            if opened:
                self.position['mix_trace']=bank.trace(bank.by_end[signal.bar_ms],spec['kind'])
            return opened
        def close_position(self,quote,reason):
            return super().close_position(quote,'MIX_SIGNAL_EXIT' if reason=='OPPOSITE_EMA_CROSS' else reason)
    def signal(bars,cfg):
        base=original_signal(bars,cfg)
        i=bank.by_end[base.bar_ms]
        position=holder['engine'].position
        if position:
            d=-position['direction'] if bank.exit(i,spec['kind'],position['direction']) else 0
        else:
            d=bank.entry(i,spec['kind'])
        return replace(base,direction=d,description=spec['id'])
    try:
        bt.DirectionEngine,core.strategy_signal=MixedEngine,signal
        yield
    finally:
        bt.DirectionEngine,core.strategy_signal=original_engine,original_signal


def replay(data,lo,hi,spec,cost,path,bank):
    if spec in controls():
        return books.replay(data,lo,hi,spec,cost,path) if spec['family'].startswith('book_') else old.replay(data,lo,hi,spec,cost,path)
    if spec not in candidates():
        raise ValueError('Candidate changed after protocol freeze')
    if bank.bars is not data['candles']:
        raise ValueError('Feature bank belongs to a different dataset')
    diagnostic=Counter()
    with installed(spec,bank,diagnostic):
        result=bt.replay(data,lo,hi,9,21,'both',cost,path)
    result.update(candidate=spec['id'],strategy_spec=spec,decision_counts=dict(diagnostic))
    return result
