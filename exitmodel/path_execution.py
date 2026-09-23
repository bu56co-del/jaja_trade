"""Hypothetical executable-price crossings, not historical ticks or native mark triggers.

Only SL/TP triggering and fill scheduling change. Emergency risk checks retain
original sampling. No interpolation bridges two different minute candles.
"""
from decimal import Decimal as D, ROUND_CEILING
from inputs44 import need
from run_fixed import mean, FixedRiskEngine

MODELS = {'SAMPLED': None, 'CROSS_0MS': 0, 'CROSS_1000MS': 1000, 'CROSS_5000MS': 5000}
EXIT_REASONS = ('STOP_OBSERVED_PRICE', 'TARGET_OBSERVED_PRICE')
OFFSETS = (2000, 21000, 40000, 59998)


def interpolate(t, a, pa, b, pb):
    need(isinstance(t,int) and a <= t <= b and a < b, 'Interpolation time')
    need(a//60000 == b//60000, 'No interpolation across candles')
    pa,pb=D(pa),D(pb)
    need(pa.is_finite() and pb.is_finite() and min(pa,pb)>0,'Interpolation price')
    return pa + (pb-pa)*D(t-a)/D(b-a)


def first_cross(a, pa, b, pb, trade, factor):
    """First integer millisecond AFTER a with executable cover reaching a level."""
    need(a < b and a//60000 == b//60000, 'Invalid crossing segment')
    need(trade['direction']==-1,'Frozen experiment is short only')
    x,y=D(pa)*factor,D(pb)*factor
    need(min(x,y)>0 and x.is_finite() and y.is_finite(),'Invalid crossing prices')
    candidates=[]
    for reason,level,upper in ((EXIT_REASONS[0],D(trade['stop']),True),
                               (EXIT_REASONS[1],D(trade['target']),False)):
        hit=lambda v: v >= level if upper else v <= level
        if hit(x): t=a+1
        elif x==y or not hit(y): continue
        else:
            delta=(level-x)/(y-x)*D(b-a)
            t=max(a+1,a+int(delta.to_integral_value(rounding=ROUND_CEILING)))
            # Decimal arithmetic can land a few ulps either side of equality.
            while t<=b and not hit(interpolate(t,a,pa,b,pb)*factor): t+=1
        if t<=b: candidates.append((t,reason))
    return min(candidates,key=lambda item:(item[0],EXIT_REASONS.index(item[1]))) if candidates else None


class PendingMixin:
    def __init__(self,*args,execution_model,**kwargs):
        need(execution_model in MODELS and execution_model!='SAMPLED','Unknown crossing model')
        self.execution_model=execution_model
        self.latency=MODELS[execution_model]
        self.pending_exit=None
        self.trigger_log=[]
        self._executing=False
        super().__init__(*args,**kwargs)

    def arm(self,at,reason,mid,origin):
        need(self.position is not None and reason in EXIT_REASONS,'Cannot arm without a position')
        if self.pending_exit is not None: return
        item=dict(trade_id=self.position['id'],trigger_ms=at,due_ms=at+self.latency,
                  reason=reason,trigger_mid=str(mid),origin=origin,status='PENDING')
        self.pending_exit=item
        self.trigger_log.append(item)
        self.position['exit_trigger']=dict(item)

    def close_position(self,quote,reason):
        if reason in EXIT_REASONS and not self._executing:
            self.arm(quote.observed_ms,reason,quote.mid,'ORIGINAL_OBSERVATION')
            return False
        old=self.pending_exit
        ok=super().close_position(quote,reason)
        if ok and old is not None:
            old.update(status='FILLED' if self._executing else 'PREEMPTED',
                       executed_ms=quote.observed_ms,actual_reason=reason)
            self.state['trades'][-1]['exit_trigger']=dict(old)
            self.pending_exit=None
        return ok

    def execute_pending(self,quote):
        if self.pending_exit is None or quote.observed_ms<self.pending_exit['due_ms']:return False
        need(self.position is not None and self.position['id']==self.pending_exit['trade_id'],'Stale pending exit')
        self._executing=True
        try:return self.close_position(quote,self.pending_exit['reason'])
        finally:self._executing=False


class CrossingMean(PendingMixin,mean.MeanEngine): pass
class CrossingFixed(PendingMixin,FixedRiskEngine): pass
