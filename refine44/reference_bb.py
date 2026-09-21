"""Independent EMA algebra and rule predicates. No production filter/engine import."""
from decimal import Decimal as D
from reference_alt import require,close_feature
from verify_mean import reference as base_reference,check as base_check,audit
COST={'base_assumptions':(D('.00045'),D('.00005'),D('.0001')),
      'cost_stress':(D('.0009'),D('.00015'),D('.0003'))}

def ma_last(xs,n):
    decay=1-D(2)/D(n+1)
    return decay**(len(xs)-1)*xs[0]+(1-decay)*sum((decay**(len(xs)-1-k)*xs[k] for k in range(1,len(xs))),D(0))

def reference_extra(block,j):
    bars=block['candles'];xs=[D(bars[k-1]['c']) for k in range(j-600+15,j+1,15)]
    f=ma_last(xs,9);slow=ma_last(xs,21);last=bars[j-15:j]
    return dict(mean=sum(xs[-20:])/20,fast=f,slow=slow,rise1=f-ma_last(xs[:-1],9),rise4=f-ma_last(xs[:-4],9),
                high=max(D(b['h']) for b in last),low=min(D(b['l']) for b in last),close=xs[-1])

def reference(block,s,cost_name,base):
    fee,spread,slip=COST[cost_name];out={};traces={}
    for j,r in base.items():
        d=0
        if r['direction']<0:
            q=reference_extra(block,j);atr=r['atr'];p=D(block['candles'][j]['o'])*(1-spread)*(1-slip)
            reasons=[];trend=s['trend']
            if trend=='UP_VETO' and q['fast']-q['slow']>0 and q['rise4']/atr>D('.5'):reasons.append('STRONG_UPTREND')
            elif trend=='DOWN_ONLY' and (q['fast']>q['slow'] or q['rise1']>0):reasons.append('NOT_DOWNTREND')
            if s['body']=='LOWER_HALF' and (q['high']==q['low'] or 2*q['close']>q['low']+q['high']):reasons.append('WEAK_CLOSE')
            room=1-q['mean']/p;required=6*(fee+spread+slip)
            if s['room']=='ROOM' and room<required:reasons.append('INSUFFICIENT_ROOM')
            traces[j]=dict(rejected_by=reasons,metrics=dict(entry_fill=str(p),room_fraction=str(room),required_room=str(required),**{k:str(v) for k,v in q.items()}))
            if not reasons:d=-1
        out[j]=dict(r,direction=d)
    return out,traces

def check(a,b,trace,reftrace):
    base_check(a,b);require(trace.keys()==reftrace.keys(),'Filter trace coverage')
    for j,t in trace.items():
        require(t['rejected_by']==reftrace[j]['rejected_by'],'Rejection reason mismatch')
        close_feature(t['metrics'],reftrace[j]['metrics'],'Independent filter metrics')
