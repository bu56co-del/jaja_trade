"""Independent calculations. Does not import model, paperlab.engine, or the replay."""
from bisect import bisect_right
from decimal import Decimal as D, ROUND_CEILING
from collections import Counter

DT={'1m':60000,'5m':300000,'15m':900000}
COST={'base_assumptions':(D('.00045'),D('.00005'),D('.0001')),
      'cost_stress':(D('.0009'),D('.00015'),D('.0003'))}

def require(ok,text):
    if not ok:raise ValueError(text)
def eq(a,b,text):
    x,y=D(str(a)),D(str(b));require(x.is_finite() and y.is_finite() and abs(x-y)<=D('1e-17')*max(D(1),abs(x),abs(y)),text)

def average_line(prices,period):
    coefficient=D(2)/D(period+1);series=[];value=prices[0]
    for index,p in enumerate(prices):
        if index:value=(D(1)-coefficient)*value+coefficient*p
        series.append(value)
    return series

def fact(history):
    require(len(history)==120,'Reference warmup')
    closes=[D(b['c']) for b in history]
    nine,twentyone=average_line(closes,9),average_line(closes,21)
    differences=[a-b for a,b in zip(nine,twentyone)]
    direction=0
    if differences[-1]>0 and differences[-2]<=0:direction=1
    if differences[-1]<0 and differences[-2]>=0:direction=-1
    ranges=[max(D(history[i]['h']),closes[i-1])-min(D(history[i]['l']),closes[i-1]) for i in range(1,120)]
    atr=sum(ranges[105:119])/14;oldatr=sum(ranges[104:118])/14
    changes=[abs(a-b) for a,b in zip(closes[-20:],closes[-21:-1])]
    efficiency=abs(closes[119]-closes[99])/sum(changes) if sum(changes) else D(0)
    top=max(D(b['h']) for b in history[99:119]);bottom=min(D(b['l']) for b in history[99:119])
    earlier_top=max(D(b['h']) for b in history[98:118]);earlier_bottom=min(D(b['l']) for b in history[98:118])
    br=0
    if closes[119]>top and closes[118]<=earlier_top:br=1
    if closes[119]<bottom and closes[118]>=earlier_bottom:br=-1
    valid=br and br*differences[-1]>0 and br*(nine[-1]-nine[-2])>0 and efficiency>=D('.2')
    return {'bar_ms':history[-1]['T'],'close':closes[-1],'atr':atr,'ema':direction,'break':br if valid else 0,
        'er':efficiency,'confirm_long':all(x < -D('.25')*a for x,a in zip(differences[-2:],(oldatr,atr))),
        'confirm_short':all(x > D('.25')*a for x,a in zip(differences[-2:],(oldatr,atr))),
        'range_fraction':(max(D(b['h']) for b in history[100:])-min(D(b['l']) for b in history[100:]))/closes[-1]}

def check_features(data,actual):
    reference={}
    for i,f in actual.items():
        r=fact(data['candles'][i-120:i]);reference[i]=r
        require(set(r)==set(f),'Feature fields')
        for key,value in r.items():
            if type(value) in (int,bool):require(f[key]==value,'Feature '+key)
            else:eq(f[key],value,'Feature '+key)
    require(set(actual)==set(range(120,len(data['candles']))),'Feature index coverage')
    return reference

def entry(f,spec):
    d=f[spec['entry']];stop=max(D('.003'),D('1.5')*f['atr']/f['close'])
    if spec['space'] and D('.8')*f['range_fraction']<stop*D('1.8'):return 0
    return d

def exit_signal(f,spec,d):
    return f['ema']==-d if spec['exit']=='cross' else f['confirm_long'] if d==1 else f['confirm_short']

def at_price(data,at,path):
    dt=DT[data['interval']];base=data['candles'][0]['t']
    i=(at-base)//dt;offset=(at-base)%dt
    require(0<=i<len(data['candles']),'Price time outside data')
    mapping=dict(zip((2000,dt*7//20,dt*2//3,dt-2),'ohlc' if path=='OHLC' else 'olhc'))
    require(offset in mapping,'Not a model observation')
    return D(data['candles'][i][mapping[offset]])

def audit(row,data,reference):
    require(not row['open_position'] and not row['pending_funding'] and not row['integrity_warnings'],'Model/accounting incomplete')
    require(row['interval']==data['interval'] and row['candidate']==row['strategy_spec']['id'],'Scenario identity')
    dt=DT[data['interval']];fee,half,slip=COST[row['cost']];spec=row['strategy_spec']
    trades=row['trades'];vals=[];pure=[];fees=[];fund=[];stress=[];events=[];last_end=None
    ends=[b['T'] for b in data['candles']];funding={r['time']:D(r['rate']) for r in data['funding']}
    cash=D(10);open_times=[]
    for tid,t in enumerate(trades,1):
        start,end=t['opened_ms'],t['closed_ms'];d,q=t['direction'],D(t['qty'])
        require(t['id']==tid and d in (-1,1) and q>0 and row['start_ms']<=start<end<=row['end_ms'],'Trade identity')
        require(start%dt==2000 and (last_end is None or start-last_end>=900000),'Entry timing/cooldown')
        require(sum(start-o<24*3600000 for o in open_times)<6,'Entry frequency');open_times.append(start)
        i=(start-data['candles'][0]['t'])//dt;f=reference[i]
        require(f['bar_ms']==t['signal_bar_ms'] and f['bar_ms']<start and entry(f,spec)==d,'Entry cause/causality')
        eq(t['signal_atr'],f['atr'],'ATR');eq(t['signal_close'],f['close'],'Signal close')
        p0,p1=at_price(data,start,row['path']),at_price(data,end,row['path'])
        ep=p0*(1+d*half)*(1+d*slip);xp=p1*(1-d*half)*(1-d*slip)
        step=D(1).scaleb(-data['metadata']['sz_decimals'])
        expected=(D('10.10')/(p0*(1+d*half))/step).to_integral_value(rounding=ROUND_CEILING)*step
        eq(q,expected,'Quantity');eq(t['entry'],ep,'Entry');eq(t['exit'],xp,'Exit')
        stop=max(D('.003'),D('1.5')*f['atr']/f['close']);friction=2*fee+2*slip+2*half
        require(stop<=D('.01') and ep*q>=10 and ep*q<=cash*D('1.15'),'Position/stop gate')
        require(q*p0/2+q*ep*fee+D('3.5')<=cash,'Margin reserve')
        require(q*ep*(stop+friction)<=cash*D('.0125') and stop*D('1.8')>=3*friction,'Risk or cost gate')
        eq(t['stop'],ep*(1-d*stop),'Stop price');eq(t['target'],ep*(1+d*stop*D('1.8')),'Target price')
        eq(t['planned_loss'],ep*q*(stop+friction),'Planned loss');eq(t['initial_margin_model'],q*p0/2,'Margin')
        eq(t['entry_raw_vwap'],p0*(1+d*half),'Entry contra quote');eq(t['exit_raw_vwap'],p1*(1-d*half),'Exit contra quote')
        ef,xf=q*ep*fee,q*xp*fee;gross=d*q*(xp-ep)
        eq(t['entry_fee'],ef,'Entry fee');eq(t['exit_fee'],xf,'Exit fee');eq(t['gross_pnl'],gross,'Gross')
        expected_events={when for when in funding if start<when<=end}
        got=[event['time'] for event in t['funding_events']]
        require(set(got)==expected_events and len(got)==len(expected_events),'Funding event coverage')
        ft=D(0)
        for event in t['funding_events']:
            when=event['time'];j=bisect_right(ends,when)-1
            require(j>=0 and 0<=when-ends[j]<=90000,'Funding oracle proxy outside tolerance')
            oracle=D(data['candles'][j]['c']);amount=-d*q*oracle*funding[when]
            eq(event['oracle'],oracle,'Prior close proxy');eq(event['rate'],funding[when],'Rate');eq(event['amount'],amount,'Funding amount')
            require(event['quality']=='PRECEDING_CANDLE_CLOSE_PROXY_NOT_ORACLE','Incorrect oracle provenance')
            ft+=amount;events.append((when,amount))
        reason=t['close_reason']
        if reason in ('OPPOSITE_EMA_CROSS','CONFIRMED_TREND_REVERSAL'):
            require(end%dt==2000 and exit_signal(reference[(end-data['candles'][0]['t'])//dt],spec,d),'Strategy exit cause')
        elif reason=='STOP_OBSERVED_PRICE':require(d*(xp-D(t['stop']))<=0,'Stop not touched')
        elif reason=='TARGET_OBSERVED_PRICE':require(d*(xp-D(t['target']))>=0,'Target not touched')
        elif reason=='MAX_HOLD_TIME':require(end-start>=spec['max_minutes']*60000,'Premature time exit')
        elif reason=='BACKTEST_WINDOW_END_ASSUMED_FILL':require(end==row['end_ms'],'Premature forced final exit')
        elif reason!='ACCOUNT_DRAWDOWN_TRIGGER':raise ValueError('Unknown exit cause '+reason)
        net=gross-ef-xf+ft;vals.append(net);pure.append(d*q*(p1-p0));fees.append(ef+xf);fund.append(ft)
        se=p0*(1+d*D('.00015'))*(1+d*D('.0003'));sx=p1*(1-d*D('.00015'))*(1-d*D('.0003'))
        stress.append(d*q*(sx-se)-q*(se+sx)*D('.0009')+ft)
        events.extend(((start,-ef),(end,gross-xf)));cash+=net;last_end=end
    m=row['metrics'];total=sum(vals,D(0));n=len(vals);w=sum(x>0 for x in vals)
    require(m['trades']==n and m['wins']==w and m['losses']==sum(x<0 for x in vals) and m['ties']==sum(x==0 for x in vals),'Win/loss counts')
    eq(row['ending_usdc'],10+total,'Ending balance');eq(m['net_usdc'],total,'Net');eq(m['return_pct'],total*10,'Return')
    eq(m['price_only_usdc'],sum(pure),'Pure price');eq(m['fees_usdc'],sum(fees),'Fees');eq(m['funding_usdc'],sum(fund),'Funding sum')
    eq(m['fixed_trades_stress_net_usdc'],sum(stress),'Fixed stress')
    eq(D(m['price_only_usdc'])-D(m['spread_slippage_usdc'])-D(m['fees_usdc'])+D(m['funding_usdc']),total,'Cost decomposition')
    require(m['return5']==(total>=D('.5')),'5pct flag')
    if n:
        eq(m['net_win_rate'],w/n,'Net win rate');eq(m['mean_net_usdc'],total/n,'Expectancy')
        eq(m['fixed_trades_stress_win_rate'],sum(x>0 for x in stress)/n,'Stress win rate')
    else:require(m['net_win_rate'] is None and m['wilson95_iid'] is None,'No-trade rate not undefined')
    require(m['observed55_and_positive']==bool(n and w/n>=.55 and total>0),'55 flag')
    require(m['observed60_and_positive']==bool(n and w/n>=.60 and total>0),'60 flag')
    # Independent chronological cash flows and liquidation-value equity, including fees.
    events.sort();cash=D(10);peak=D(10);dd=D(0);pointer=0
    limit=max((t['closed_ms'] for t in trades),default=row['start_ms']-1)
    opening=[t['opened_ms'] for t in trades]
    for b in data['candles']:
        if b['T']<row['start_ms'] or b['t']>limit:continue
        for offset in (2000,dt*7//20,dt*2//3,dt-2):
            at=b['t']+offset
            if not row['start_ms']<=at<=limit:continue
            while pointer<len(events) and events[pointer][0]<=at:
                cash+=events[pointer][1];pointer+=1
            equity=cash;j=bisect_right(opening,at)-1
            if j>=0 and trades[j]['closed_ms']>at:
                t=trades[j];d,q=t['direction'],D(t['qty'])
                liquid=at_price(data,at,row['path'])*(1-d*half)*(1-d*slip)
                equity+=d*q*(liquid-D(t['entry']))-q*liquid*fee
            peak=max(peak,equity);dd=max(dd,(peak-equity)/peak)
    eq(row['sampled_max_drawdown_pct'],dd*100,'Sampled drawdown')
    return {'status':'PASS_INDEPENDENT_LEDGER_AND_CAUSAL_CHECK','trades':n,'funding_events':len([e for t in trades for e in t['funding_events']]),
            'market_facts_or_exact_exchange_fills':'NOT_VERIFIED'}
