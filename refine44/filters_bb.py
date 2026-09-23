"""Closed-15m BB short filters; read-only paper research, no network or orders."""
from decimal import Decimal as D
from itertools import product
import signals_mean as parent
from inputs44 import aggregate,need

BASE_ID='BB_REENTRY_15_NONE_HARD_SHORT_ONLY'
BASE_SPEC=dict(id='BB_REENTRY_15_NONE_HARD',family='BB_REENTRY',minutes=15,filter='NONE',exit='HARD')
PRIMARY='BB15_UP_VETO_ROOM_LOWER_HALF'

def specs():
    return [dict(id=f'BB15_{trend}_{room}_{body}',trend=trend,room=room,body=body)
            for trend,room,body in product(('NONE','UP_VETO','DOWN_ONLY'),('NONE','ROOM'),('NONE','LOWER_HALF'))]

def ema(xs,n):
    a=D(2)/D(n+1);out=[xs[0]]
    for x in xs[1:]:out.append(a*x+(1-a)*out[-1])
    return out

def extra(block):
    bars=aggregate(block['candles'],15);out={}
    for i in range(40,len(bars)):
        h=bars[i-40:i];c=[D(x['c']) for x in h];fast=ema(c,9);slow=ema(c,21)
        out[i*15]=dict(mean=sum(c[-20:])/20,fast=fast[-1],slow=slow[-1],
                      rise1=fast[-1]-fast[-2],rise4=fast[-1]-fast[-5],
                      high=D(h[-1]['h']),low=D(h[-1]['l']),close=c[-1])
    return out

def gate(f,atr,s,open_price,cost):
    fee=D(cost['taker_fee']);half=D(cost['spread_bps'])/20000;slip=D(cost['adverse_slippage_bps'])/10000
    entry=D(open_price)*(1-half)*(1-slip);friction=2*(fee+half+slip)
    need(entry>0 and atr>0,'Nonpositive entry/ATR')
    failures=[]
    if s['trend']=='UP_VETO' and f['fast']>f['slow'] and f['rise4']>D('.5')*atr:failures.append('STRONG_UPTREND')
    if s['trend']=='DOWN_ONLY' and not(f['fast']<=f['slow'] and f['rise1']<=0):failures.append('NOT_DOWNTREND')
    if s['body']=='LOWER_HALF' and not(f['high']>f['low'] and f['close']<=f['low']+(f['high']-f['low'])/2):failures.append('WEAK_CLOSE')
    room=(entry-f['mean'])/entry
    if s['room']=='ROOM' and room<3*friction:failures.append('INSUFFICIENT_ROOM')
    return failures,dict(entry_fill=str(entry),room_fraction=str(room),required_room=str(3*friction),
                         **{k:str(v) for k,v in f.items()})

def choices(block,s,cost,base,extras):
    need(s in specs(),'Unknown frozen candidate');out={};traces={}
    for j,c in base.items():
        d=-1 if c['direction']==-1 else 0
        if d:
            reasons,trace=gate(extras[j],c['atr'],s,block['candles'][j]['o'],cost)
            traces[j]=dict(rejected_by=reasons,metrics=trace)
            if reasons:d=0
        out[j]=dict(c,direction=d)
    return out,traces
