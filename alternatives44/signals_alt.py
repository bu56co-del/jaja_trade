"""Original, bounded public-method hypotheses. No network, orders or external bot code."""
from decimal import Decimal as D
from itertools import product

BASELINE = 'RETEST_0.5_20_50'
PRIMARY = 'BB_FADE_20_MICRO_ROOM'


def specs():
    out = [dict(id=BASELINE, family='BASELINE', param=0, trigger='DIRECT', filter='NONE')]
    for family, params, filters in (('BB_FADE', (20, 40), ('NONE', 'ROOM')),
                                    ('BB_EXPAND', (20, 40), ('NONE', 'EMA20_50')),
                                    ('RSI_DIP', (5, 10), ('NONE', 'EMA50'))):
        for n, trigger, filt in product(params, ('DIRECT', 'MICRO'), filters):
            out.append(dict(id=f'{family}_{n}_{trigger}_{filt}', family=family, param=n, trigger=trigger, filter=filt))
    return out


def ema(values, n):
    a = D(2)/(n+1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + a*(v-out[-1]))
    return out


def rsi(values, n):
    changes = [b-a for a,b in zip(values, values[1:])]
    gain = sum((max(v,D(0)) for v in changes[:n]),D(0))/n
    loss = sum((max(-v,D(0)) for v in changes[:n]),D(0))/n
    def score(g,l):
        return D(50) if not g and not l else D(100) if not l else D(100)*g/(g+l)
    out = [score(gain,loss)]
    for v in changes[n:]:
        gain = (gain*(n-1)+max(v,D(0)))/n
        loss = (loss*(n-1)+max(-v,D(0)))/n
        out.append(score(gain,loss))
    return out


def band(values,n):
    vs = values[-n:];m = sum(vs)/n
    sd = (sum((v-m)**2 for v in vs)/n).sqrt()
    return dict(mean=m,lower=m-2*sd,upper=m+2*sd,width=4*sd/m)


def features(bars):
    out = {}
    for i in range(120,len(bars)):
        h=bars[i-120:i];c=[D(b['c']) for b in h]
        tp=[(D(b['h'])+D(b['l'])+D(b['c']))/3 for b in h]
        tr=[max(D(b['h'])-D(b['l']),abs(D(b['h'])-c[j-1]),abs(D(b['l'])-c[j-1])) for j,b in enumerate(h) if j]
        erden=sum(abs(b-a) for a,b in zip(c[-21:-1],c[-20:]))
        out[i]=dict(bar_ms=h[-1]['T'],close=c[-1],prev=c[-2],atr=sum(tr[-14:])/14,
                    er=abs(c[-1]-c[-21])/erden if erden else D(0),
                    rsi14=rsi(c,14)[-1],rsi2=rsi(c,2)[-1],rsi2prev=rsi(c,2)[-2],
                    e20=ema(c,20)[-2:],e50=ema(c,50)[-2:],
                    volume_ratio=D(h[-1]['v'])/(sum(D(b['v']) for b in h[-21:-1])/20),
                    bands={str(n):band(tp,n) for n in (20,40)},
                    prior_bands={str(n):band(tp[:-1],n) for n in (20,40)})
    return out


def raw_direction(f,s):
    family=s['family'];n=s['param'];c=f['close'];d=0
    if family=='BB_FADE':
        b=f['bands'][str(n)]
        d=1 if c<b['lower'] and f['rsi14']<30 else -1 if c>b['upper'] and f['rsi14']>70 else 0
    elif family=='BB_EXPAND':
        b,p=f['bands'][str(n)],f['prior_bands'][str(n)]
        if b['width']>p['width'] and f['volume_ratio']>=D('1.2'):
            if c>b['upper'] and f['prev']<=p['upper'] and f['rsi14']>=55:d=1
            if c<b['lower'] and f['prev']>=p['lower'] and f['rsi14']<=45:d=-1
    elif family=='RSI_DIP':
        if f['rsi2']<n and f['rsi2prev']>=n:d=1
        if f['rsi2']>100-n and f['rsi2prev']<=100-n:d=-1
    else:raise ValueError('Unknown family')
    if s['filter']=='EMA20_50' and d:
        if not(d*(f['e20'][-1]-f['e50'][-1])>0 and d*(f['e20'][-1]-f['e20'][-2])>0):d=0
    if s['filter']=='EMA50' and d:
        if not(d*(c-f['e50'][-1])>0 and d*(f['e50'][-1]-f['e50'][-2])>0):d=0
    return d


def decisions(block,fv,s):
    if s not in specs() or s['family']=='BASELINE':raise ValueError('Unknown new hypothesis')
    bars=block['candles'];out={};pending=None;last_raw=0
    for j in range(600,len(bars)):
        result=dict(direction=0,atr=D(0),close=D(bars[j-1]['c']),trace={})
        if j%5==0:
            i=j//5;f=fv[i];raw=raw_direction(f,s)
            fresh=raw if raw and raw!=last_raw else 0
            last_raw=raw
            if pending is None and fresh:
                pending=dict(i=i,j=j,d=fresh)
        if pending is not None:
            age=j-pending['j']
            if age>5:
                pending=None
            elif s['trigger']=='DIRECT' or age>=1:
                d=pending['d'];a,b=bars[j-2],bars[j-1]
                micro=(D(b['c'])>D(a['h']) if d==1 else D(b['c'])<D(a['l'])) and d*(D(b['c'])-D(b['o']))>0
                if s['trigger']=='DIRECT' or micro:
                    i=pending['i'];f=fv[i];n=s['param']
                    mean=f['bands'][str(n)]['mean'] if s['family']=='BB_FADE' else f['close']
                    result=dict(direction=d,atr=f['atr'],close=D(b['c']),
                        trace=dict(setup_i=i,setup_minute=pending['j'],setup_end=f['bar_ms'],trigger_minute=j,
                                   trigger_end=b['T'],family=s['family'],trigger=s['trigger'],filter=s['filter'],
                                   mean=str(mean),rsi14=str(f['rsi14']),rsi2=str(f['rsi2']),er=str(f['er']),
                                   volume_ratio=str(f['volume_ratio'])))
                    pending=None
        out[j]=result
    return out
