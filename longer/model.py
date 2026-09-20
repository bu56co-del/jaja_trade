"""Native-bar historical adapter around the unchanged original accounting engine."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, replace
from decimal import Decimal as D
from statistics import mean
import math

from paperlab import engine as core
from paperlab.backtest import assumed_quote, FundingPrices, COSTS, PATHS
from paperlab.common import Config
from market_data import INTERVALS, MINUTE, HOUR, DAY, ms, END, STARTS, need

SPECS=(
 {'id':'EMA_CROSS_90','entry':'ema','exit':'cross','max_minutes':90,'space':False},
 {'id':'EMA_CONFIRM_90','entry':'ema','exit':'confirm','max_minutes':90,'space':False},
 {'id':'BREAK_TREND_CROSS_90','entry':'break','exit':'cross','max_minutes':90,'space':False},
 {'id':'BREAK_TREND_CONFIRM_90','entry':'break','exit':'confirm','max_minutes':90,'space':False},
 {'id':'BREAK_TREND_CONFIRM_360','entry':'break','exit':'confirm','max_minutes':360,'space':False},
 {'id':'BREAK_TREND_CONFIRM_360_SPACE','entry':'break','exit':'confirm','max_minutes':360,'space':True},
)
PRIMARY=('5m','BREAK_TREND_CONFIRM_360','full')

def windows(tf):
    end=ms(END);start=ms(STARTS[tf]);common=ms(STARTS['1m'])
    if tf=='1m':return [('common_2d',start,end)]
    bounds=([start,ms('2026-09-10T00:00:00+00:00'),ms('2026-09-15T00:00:00+00:00'),end] if tf=='5m' else
            [start,ms('2026-08-18T00:00:00+00:00'),ms('2026-09-03T00:00:00+00:00'),end])
    return [('full',start,end),('common_2d',common,end)]+[(f'calendar_{i+1}',a,b) for i,(a,b) in enumerate(zip(bounds,bounds[1:]))]

def nodes(dt,path):
    fields='ohlc' if path=='OHLC' else 'olhc'
    return tuple(zip(fields,(2000,dt*7//20,dt*2//3,dt-2)))

def features(bars):
    """One feature vector per next-open index; rolling 120-bar seed matches original EMA."""
    result={}
    for i in range(120,len(bars)):
        bs=bars[i-120:i];c=[D(x['c']) for x in bs]
        f,s=core.ema(c,9),core.ema(c,21)
        cross=1 if f[-2]<=s[-2] and f[-1]>s[-1] else -1 if f[-2]>=s[-2] and f[-1]<s[-1] else 0
        tr=[max(D(x['h'])-D(x['l']),abs(D(x['h'])-c[j-1]),abs(D(x['l'])-c[j-1])) for j,x in enumerate(bs) if j]
        atr=sum(tr[-14:],D(0))/14;prior_atr=sum(tr[-15:-1],D(0))/14
        travel=sum((abs(c[j]-c[j-1]) for j in range(100,120)),D(0))
        er=abs(c[-1]-c[-21])/travel if travel else D(0)
        upper=max(D(x['h']) for x in bs[-21:-1]);lower=min(D(x['l']) for x in bs[-21:-1])
        br=1 if c[-1]>upper and c[-2]<=max(D(x['h']) for x in bs[-22:-2]) else -1 if c[-1]<lower and c[-2]>=min(D(x['l']) for x in bs[-22:-2]) else 0
        trend=1 if f[-1]>s[-1] and f[-1]>f[-2] else -1 if f[-1]<s[-1] and f[-1]<f[-2] else 0
        result[i]={'bar_ms':bs[-1]['T'],'close':c[-1],'atr':atr,'ema':cross,
            'break':br if br==trend and er>=D('.2') else 0,'er':er,
            'confirm_long':f[-1]-s[-1]<-D('.25')*atr and f[-2]-s[-2]<-D('.25')*prior_atr,
            'confirm_short':f[-1]-s[-1]>D('.25')*atr and f[-2]-s[-2]>D('.25')*prior_atr,
            'range_fraction':(max(D(x['h']) for x in bs[-20:])-min(D(x['l']) for x in bs[-20:]))/c[-1]}
    return result

def wants_exit(f,spec,d):
    return f['ema']==-d if spec['exit']=='cross' else f['confirm_long'] if d==1 else f['confirm_short']

def stop_distance(f):return max(D('.003'),D('1.5')*f['atr']/f['close'])

def entry_signal(f,spec):
    direction=f[spec['entry']]
    if spec['space'] and f['range_fraction']<stop_distance(f)*D('1.8')/D('.8'):return 0
    return direction

@contextmanager
def installed(fv,spec,holder):
    old=core.strategy_signal
    def signal(bars,cfg):
        f=fv[holder['i']];pos=holder['engine'].position
        direction=-pos['direction'] if pos and wants_exit(f,spec,pos['direction']) else 0 if pos else entry_signal(f,spec)
        description=old(bars,cfg).description if spec['id']=='EMA_CROSS_90' and holder['interval']=='1m' else spec['id']
        return core.Signal(direction,f['bar_ms'],f['atr'],f['close'],description)
    try:
        core.strategy_signal=signal;yield
    finally:core.strategy_signal=old

class DiagnosticEngine(core.Engine):
    def __init__(self,cfg,state,spec):
        super().__init__(cfg,state);self.counts=Counter();self.spec=spec
    def decision(self,at,code,message):
        self.counts[code]+=1;return super().decision(at,code,message)
    def close_position(self,quote,reason):
        if reason=='OPPOSITE_EMA_CROSS' and self.spec['exit']=='confirm':reason='CONFIRMED_TREND_REVERSAL'
        return super().close_position(quote,reason)

def wilson(w,n):
    if not n:return None
    z=1.959963984540054;p=w/n;den=1+z*z/n;mid=(p+z*z/(2*n))/den
    radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-radius),min(1,mid+radius)]

def metrics(trades,cost_name):
    fee,half,slip=[D(COSTS[cost_name][k])/den for k,den in (('taker_fee',1),('spread_bps',20000),('adverse_slippage_bps',10000))]
    net=[];pure=[];stress=[];funds=[];fees=[];friction=[]
    for t in trades:
        d,q=t['direction'],D(t['qty']);ep,xp=D(t['entry']),D(t['exit'])
        e=ep/((1+d*half)*(1+d*slip));x=xp/((1-d*half)*(1-d*slip))
        funding=sum((D(f['amount']) for f in t['funding_events']),D(0));f=D(t['entry_fee'])+D(t['exit_fee'])
        gross=d*q*(xp-ep);pure.append(d*q*(x-e));funds.append(funding);fees.append(f)
        friction.append(pure[-1]-gross);net.append(gross-f+funding)
        se=e*(1+d*D('.00015'))*(1+d*D('.0003'));sx=x*(1-d*D('.00015'))*(1-d*D('.0003'))
        stress.append(d*q*(sx-se)-q*(se+sx)*D('.0009')+funding)
    def sumd(xs):return sum(xs,D(0))
    n=len(net);w=sum(v>0 for v in net);losses=[v for v in net if v<0];wins=[v for v in net if v>0]
    return {'trades':n,'wins':w,'losses':len(losses),'ties':n-w-len(losses),'net_win_rate':w/n if n else None,
        'wilson95_iid':wilson(w,n),'net_usdc':str(sumd(net)),'return_pct':str(sumd(net)*10),
        'mean_net_usdc':str(sumd(net)/n) if n else None,
        'mean_win_usdc':str(sumd(wins)/len(wins)) if wins else None,
        'mean_loss_usdc':str(sumd(losses)/len(losses)) if losses else None,
        'profit_factor':str(sumd(wins)/-sumd(losses)) if losses else None,
        'price_only_usdc':str(sumd(pure)),'spread_slippage_usdc':str(sumd(friction)),
        'fees_usdc':str(sumd(fees)),'funding_usdc':str(sumd(funds)),
        'fixed_trades_stress_net_usdc':str(sumd(stress)),
        'fixed_trades_stress_win_rate':sum(v>0 for v in stress)/n if n else None,
        'positive_price_but_net_loss':sum(a>0 and b<0 for a,b in zip(pure,net)),
        'forced_end_trades':sum(t['close_reason']=='BACKTEST_WINDOW_END_ASSUMED_FILL' for t in trades),
        'exit_reasons':dict(Counter(t['close_reason'] for t in trades)),
        'mean_hold_minutes':sum((t['closed_ms']-t['opened_ms'])/MINUTE for t in trades)/n if n else None,
        'max_hold_minutes':max(((t['closed_ms']-t['opened_ms'])/MINUTE for t in trades),default=None),
        'exposure_ms':sum(t['closed_ms']-t['opened_ms'] for t in trades),
        'active_utc_days':len({t['opened_ms']//DAY for t in trades}),
        'observed55_and_positive':bool(n and w/n>=.55 and sumd(net)>0),
        'observed60_and_positive':bool(n and w/n>=.60 and sumd(net)>0),
        'return5':sumd(net)>=D('.5')}

def replay(data,lo,hi,spec,cost_name,path,fv=None):
    need(spec in SPECS and cost_name in COSTS and path in PATHS,'Unknown frozen scenario')
    bars=data['candles'];dt=INTERVALS[data['interval']]
    need(120<=lo<hi<=len(bars) and hi-lo>=2,'Replay window')
    fv=fv or features(bars);cost=COSTS[cost_name]
    # Historical sampling cadence differs from live polling; missing bars were rejected at admission.
    cfg=replace(Config(),taker_fee=cost['taker_fee'],adverse_slippage_bps=cost['adverse_slippage_bps'],
                max_hold_seconds=spec['max_minutes']*60,gap_halt_seconds=45 if dt==MINUTE else dt//1000+45).validate()
    start,end=bars[lo]['t']+2000,bars[hi-1]['T']-1
    eng=DiagnosticEngine(cfg,core.new_state(cfg,start,'NATIVE_BAR_EXPLORATORY_PAPER'),spec)
    eng.ingest_funding([r for r in data['funding'] if bars[lo]['t']-HOUR<=r['time']<=end])
    prices=FundingPrices(data);holder={'engine':eng,'interval':data['interval']};halt_ms=None;quote=None
    with installed(fv,spec,holder):
        for i in range(lo,hi):
            holder['i']=i
            for field,offset in nodes(dt,path):
                quote=assumed_quote(bars[i]['t']+offset,bars[i][field],cost['spread_bps'],data['metadata'])
                eng.reconcile_funding(prices,quote.observed_ms)
                eng.tick(quote,bars[i-120:i] if field=='o' else None)
                eng.update_drawdown(quote)
                if eng.state['halt_reason'] and halt_ms is None:halt_ms=quote.observed_ms
            if halt_ms and not eng.position:break
        if eng.position:
            eng.close_position(quote,'BACKTEST_WINDOW_END_ASSUMED_FILL');eng.update_drawdown(quote)
        eng.reconcile_funding(prices,quote.observed_ms)
    trades=eng.state['trades'];m=metrics(trades,cost_name)
    friction=D(cost['taker_fee'])*2+D(cost['adverse_slippage_bps'])/10000*2+D(cost['spread_bps'])/10000
    signal_count=0;cost_eligible=0;space_rejected=0;signals_after_halt=0;stops=[]
    for i in range(lo,hi):
        f=fv[i];stops.append(stop_distance(f))
        if f[spec['entry']]:
            signal_count+=1
            if spec['space'] and not entry_signal(f,spec):space_rejected+=1
            if stop_distance(f)*D('1.8')>=3*friction:cost_eligible+=1
            if halt_ms and bars[i]['t']+2000>halt_ms:signals_after_halt+=1
    return {'candidate':spec['id'],'strategy_spec':spec,'interval':data['interval'],'cost':cost_name,'path':path,
        'start_ms':start,'end_ms':end,'bars':hi-lo,'initial_usdc':'10','ending_usdc':str(eng.cash),
        'sampled_max_drawdown_pct':str(D(eng.state['max_drawdown_fraction'])*100),
        'halt_reason':eng.state['halt_reason'],'halt_ms':halt_ms,
        'halted_fraction':(end-halt_ms)/(end-start) if halt_ms else 0,
        'open_position':eng.position is not None,'pending_funding':eng.state['pending_funding'],
        'integrity_warnings':eng.state['integrity_warnings'],'trades':trades,'metrics':m,
        'decisions':dict(eng.counts),'potential_entry_signals':signal_count,'space_filter_rejections':space_rejected,
        'potential_signals_cost_eligible':cost_eligible,'potential_signals_after_halt':signals_after_halt,
        'stop_floor_fraction_of_bars':sum(x==D('.003') for x in stops)/len(stops),
        'target_to_cost_min':str(min(stops)*D('1.8')/friction),'target_to_cost_max':str(max(stops)*D('1.8')/friction),
        'account_config':asdict(cfg),'evidence':'INSUFFICIENT_SAMPLE' if len(trades)<100 else 'RETROSPECTIVE_MULTIPLE_COMPARISON_ONLY'}
