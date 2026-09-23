"""Recovered extremes plus dynamic exits. Original paper-research definitions."""
from decimal import Decimal as D
from itertools import product
from inputs44 import aggregate

BASELINE='RETEST_0.5_20_50'
PRIMARY='BB_REENTRY_15_RANGE_SMA5'
EPS=D('1e-12')


def specs():
    out=[dict(id=BASELINE,family='BASELINE',minutes=5,filter='NONE',exit='HARD')]
    for family,minutes,filt,exit in product(('BB_REENTRY','RSI_RECOVERY'),(5,15),('NONE','RANGE'),('HARD','SMA5','RSI70')):
        out.append(dict(id=f'{family}_{minutes}_{filt}_{exit}',family=family,minutes=minutes,filter=filt,exit=exit))
    return out


def relative(xs):
    changes=[b-a for a,b in zip(xs,xs[1:])]
    up=sum(max(x,D(0)) for x in changes[:2])/2
    dn=sum(max(-x,D(0)) for x in changes[:2])/2
    def score():return D(50) if up==dn==0 else D(100) if dn==0 else 100*up/(up+dn)
    out=[score()]
    for x in changes[2:]:
        up=(up+max(x,D(0)))/2;dn=(dn+max(-x,D(0)))/2;out.append(score())
    return out


def band(xs):
    part=xs[-20:];mean=sum(part)/20
    sd=(sum((x-mean)**2 for x in part)/20).sqrt()
    return mean-2*sd,mean+2*sd


def features(block,minutes):
    if minutes not in (5,15):raise ValueError('Unsupported timeframe')
    bars=aggregate(block['candles'],minutes);history=600//minutes;out={}
    for i in range(history,len(bars)):
        h=bars[i-history:i];c=[D(b['c']) for b in h]
        rs=relative(c);low,high=band(c);plow,phigh=band(c[:-1])
        tr=[max(D(b['h'])-D(b['l']),abs(D(b['h'])-c[k-1]),abs(D(b['l'])-c[k-1])) for k,b in enumerate(h) if k]
        movement=sum(abs(b-a) for a,b in zip(c[-21:-1],c[-20:]))
        out[i*minutes]=dict(bar_ms=h[-1]['T'],close=c[-1],prior_close=c[-2],open=D(h[-1]['o']),
            atr=sum(tr[-14:])/14,lower=low,upper=high,prior_lower=plow,prior_upper=phigh,
            rsi=rs[-1],prior_rsi=rs[-2],sma5=sum(c[-5:])/5,
            er=abs(c[-1]-c[-21])/movement if movement else D(0))
    return out


def entry(f,s):
    d=0
    if s['family']=='BB_REENTRY':
        if not f['lower']+EPS <= f['close'] <= f['upper']-EPS:return 0
        if f['prior_close']<f['prior_lower']-EPS and f['close']>=f['lower']+EPS:d=1
        if f['prior_close']>f['prior_upper']+EPS and f['close']<=f['upper']-EPS:d=-1
    elif s['family']=='RSI_RECOVERY':
        if f['prior_rsi']<=10 and f['rsi']>10+EPS:d=1
        if f['prior_rsi']>=90 and f['rsi']<90-EPS:d=-1
    else:raise ValueError('Unknown entry')
    if d*(f['close']-f['open'])<=EPS:d=0
    if s['filter']=='RANGE' and f['er']>D('.30'):d=0
    return d


def exits(f,style,d):
    if style=='HARD':return False
    if style=='SMA5':return d*(f['close']-f['sma5'])>EPS
    if style=='RSI70':return f['rsi']>70+EPS if d==1 else f['rsi']<30-EPS
    raise ValueError('Unknown exit')


def choices(block,fv,s):
    out={}
    for j in range(600,len(block['candles'])):
        f=fv.get(j)
        trace={} if f is None else {k:str(f[k]) for k in ('rsi','prior_rsi','sma5','er','lower','upper')}
        out[j]=dict(direction=entry(f,s) if f else 0,atr=f['atr'] if f else D(0),
            close=D(block['candles'][j-1]['c']),trace=trace,
            exit_long=bool(f and exits(f,s['exit'],1)),exit_short=bool(f and exits(f,s['exit'],-1)))
    return out
