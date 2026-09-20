"""Policy streams plugged into the byte-identical original replay/ledger engine."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
from functools import lru_cache
from paperlab import backtest as bt, engine as core
from paperlab.common import Config
import verify as v


class Cache:
    def __init__(self,data):
        self.data=data
        self.index={b['T']:i+1 for i,b in enumerate(data['candles'])}
        self.base={}
        for i in range(120,len(data['candles'])):
            bs=data['candles'][i-120:i]
            c=[D(x['c']) for x in bs]
            tr=[max(D(x['h'])-D(x['l']),abs(D(x['h'])-c[j-1]),abs(D(x['l'])-c[j-1]))
                for j,x in enumerate(bs) if j]
            # Same as core.strategy_signal ATR; EMA components are frozen streams.
            self.base[i]=core.Signal(0,bs[-1]['T'],sum(tr[-14:],D(0))/14,c[-1],'ENUMERATED_POLICY')
        original=bt.assumed_quote
        self.quotes=lru_cache(maxsize=60000)(lambda at,p,spread:original(at,p,spread,data['metadata']))


@contextmanager
def installed(cache,entry,exits,lo):
    original_engine,original_signal,original_quote=bt.DirectionEngine,core.strategy_signal,bt.assumed_quote
    holder={}
    class PolicyEngine(original_engine):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);holder['engine']=self
    def signal(bars,cfg):
        i=cache.index[bars[-1]['T']];j=i-lo
        pos=holder['engine'].position
        if pos:
            bit=1 if pos['direction']==1 else 2
            direction=-pos['direction'] if exits[j]&bit else 0
        else:direction=entry[j]-1
        return replace(cache.base[i],direction=direction)
    def quote(at,p,spread,meta):
        if meta!=cache.data['metadata']:raise ValueError('Wrong cache metadata')
        return cache.quotes(at,p,spread)
    try:
        bt.DirectionEngine,core.strategy_signal,bt.assumed_quote=PolicyEngine,signal,quote
        yield
    finally:
        bt.DirectionEngine,core.strategy_signal,bt.assumed_quote=original_engine,original_signal,original_quote


def replay(cache,lo,hi,entry,exits,cost,path):
    if not (len(entry)==len(exits)==hi-lo) or any(v>2 for v in entry) or any(v>3 for v in exits):
        raise ValueError('Bad decision stream')
    with installed(cache,entry,exits,lo):
        return bt.replay(cache.data,lo,hi,9,21,'both',cost,path)


def audit(row,data,lo,entry,exits):
    """Independent ledger computation; policy separately checked before replay."""
    def check(history,trade,spec):
        idx=(history[-1]['t']-data['candles'][0]['t'])//60000+1
        v.require(entry[idx-lo]-1==trade['direction'],'Incorrect policy entry')
        c=[D(b['c']) for b in history]
        tr=[max(D(b['h'])-D(b['l']),abs(D(b['h'])-c[i-1]),abs(D(b['l'])-c[i-1]))
            for i,b in enumerate(history) if i]
        atr=sum(tr[-14:])/14
        v.equal(atr,trade['signal_atr'],'ATR')
        v.equal(c[-1],trade['signal_close'],'Signal close')
        return max(D('.003'),D('1.5')*atr/c[-1])
    previous=v.signal_check
    try:
        v.signal_check=check
        result=v.audit_row(row,data,{'family':'sweep','fast':9,'slow':21})
    finally:v.signal_check=previous
    for trade in row['trades']:
        if trade['close_reason']=='OPPOSITE_EMA_CROSS':
            at=trade['closed_ms']
            v.require(at%60000==2000,'Policy exit timing')
            i=(at//60000*60000-data['candles'][0]['t'])//60000
            bit=1 if trade['direction']==1 else 2
            v.require(bool(exits[i-lo]&bit),'Invalid policy exit')
    return result
