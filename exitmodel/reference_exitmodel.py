"""Independent ledger/event replay: no production engine, strategy or crossing imports.

Binary-search crossing times instead of production's algebraic root. Uses the
existing engine-external signal features and fixed-risk reference size formula.
"""
from decimal import Decimal as D, ROUND_CEILING
from bisect import bisect_right
from collections import Counter
from reference_alt import require,eq,close_feature
from reference_fixed import reference_plan

LATENCIES={'CROSS_0MS':0,'CROSS_1000MS':1000,'CROSS_5000MS':5000}
COSTS={'base_assumptions':(D('.00045'),D('.00005'),D('.0001')),
       'cost_stress':(D('.0009'),D('.00015'),D('.0003'))}
OFF=(2000,21000,40000,59998)


def linear(a,pa,b,pb,t):
    require(a//60000==b//60000 and a<=t<=b,'Reference gap interpolation')
    # Weighted sum rather than production incremental interpolation.
    return (D(b-t)*pa+D(t-a)*pb)/D(b-a)


def crossing(a,pa,b,pb,stop,target,factor):
    found=[]
    for label,level,upper in (('STOP_OBSERVED_PRICE',stop,True),('TARGET_OBSERVED_PRICE',target,False)):
        def hit(t):
            v=linear(a,pa,b,pb,t)*factor
            return v>=level if upper else v<=level
        # On this monotone segment a new hit requires the endpoint to qualify.
        if not hit(b):continue
        lo,hi=a+1,b
        while lo<hi:
            mid=(lo+hi)//2
            if hit(mid):hi=mid
            else:lo=mid+1
        if hit(lo):found.append((lo,label))
    return min(found,key=lambda v:(v[0],v[1]!='STOP_OBSERVED_PRICE')) if found else None


def simulate(row,block,ref):
    profile=row['candidate'];latency=LATENCIES[row['execution_model']]
    fee,half,slip=COSTS[row['cost']];factor=(1+half)*(1+slip);entryfactor=(1-half)*(1-slip)
    bars=block['candles'];ends=[b['T'] for b in bars];rates=sorted((r['time'],D(r['rate'])) for r in block['funding']);fundidx=0
    meta=block['metadata'];decimals=meta.get('szDecimals',meta.get('sz_decimals'));maxlev=meta.get('maxLeverage',meta.get('max_leverage'))
    qstep=D(1).scaleb(-decimals);cash=D(10);peak=D(10);dd=D(0);held=None;pending=None
    predicted=[];events=[];attempts=[];streak=0;halt=None;halt_reason='';lastclose=None;lastobs=None;marginmin=None;markcount=0
    def mark(p):
        nonlocal marginmin,markcount
        if held is not None:
            buffer=cash-held['qty']*(p-held['entry'])-held['qty']*p/(2*D(maxlev))
            require(buffer>0,'Reference maintenance breach')
            marginmin=buffer if marginmin is None else min(marginmin,buffer);markcount+=1
    def update(p):
        nonlocal peak,dd
        value=cash
        if held is not None:
            xp=max(p*factor,held['target'])
            value+=held['qty']*(held['entry']-xp)-held['qty']*xp*fee
        peak=max(peak,value);dd=max(dd,(peak-value)/peak)
        return value
    def halt_at(t,reason):
        nonlocal halt,halt_reason
        if halt is None:halt,halt_reason=t,reason
    def settle(t):
        nonlocal fundidx,cash
        while fundidx<len(rates) and rates[fundidx][0]<=t:
            ts,rate=rates[fundidx];fundidx+=1
            if held is not None and held['opened_ms']<ts:
                j=bisect_right(ends,ts)-1
                require(j>=0 and 0<=ts-ends[j]<=90000,'Reference funding price unavailable')
                amount=held['qty']*D(bars[j]['c'])*rate
                held['funding_events'].append(dict(time=ts,rate=rate,oracle=D(bars[j]['c']),oracle_sample_ms=ends[j],amount=amount));cash+=amount
    def arm(t,reason,p,origin):
        nonlocal pending
        if pending is not None:return
        pending=dict(trade_id=held['id'],trigger_ms=t,due_ms=t+latency,reason=reason,trigger_mid=p,origin=origin,status='PENDING')
        events.append(pending)
    def close(t,p,reason,scheduled=False):
        nonlocal cash,held,pending,lastclose,streak
        require(held is not None,'Reference double close')
        observed=p*factor;xp=max(observed,held['target']) if reason=='TARGET_OBSERVED_PRICE' else observed
        gross=held['qty']*(held['entry']-xp);xf=held['qty']*xp*fee
        held.update(closed_ms=t,exit=xp,observed_exit_price=observed,exit_reference_mid=p,exit_fee=xf,gross_pnl=gross,
            target_cap_haircut_usdc=held['qty']*(xp-observed),close_reason=reason)
        cash+=gross-xf
        net=gross-held['entry_fee']-xf+sum((f['amount'] for f in held['funding_events']),D(0))
        streak=streak+1 if net<0 else 0
        if streak>=3:halt_at(t,'CONSECUTIVE_LOSSES')
        if pending is not None:
            pending.update(status='FILLED' if scheduled else 'PREEMPTED',executed_ms=t,actual_reason=reason)
            held['exit_trigger']=dict(pending);pending=None
        held=None;lastclose=t
    def fill(t,p):
        require(pending is not None and t>=pending['due_ms'],'Reference premature fill')
        settle(t);mark(p);update(p);close(t,p,pending['reason'],True);update(p)
    def opening(t,p,j):
        nonlocal held,cash
        f=ref[j]
        if f['direction']!=-1 or halt is not None or (lastclose is not None and t-lastclose<900000):return
        if sum(t-x['opened_ms']<86400000 for x in predicted)>=6:return
        ep=p*entryfactor;stop=max(D('.003'),D('1.5')*D(f['atr'])/D(f['close']))
        targetdist=stop*(D('.9') if profile=='RISK125_TP_HALF' else D('1.8'));fr=2*(fee+half+slip)
        if profile=='BASE_B':
            qty=(D('10.10')/(p*(1-half))/qstep).to_integral_value(rounding=ROUND_CEILING)*qstep
            reject=(qty*ep<10 or qty*ep>cash*D('1.15') or qty*p/2+qty*ep*fee+D('3.5')>cash or
                    stop>D('.01') or qty*ep*(stop+fr)>cash/D(80) or targetdist<3*fr)
            margin=qty*p/2
        else:
            qty,item=reference_plan(cash,max(cash,peak),p,fee,half,slip,f['atr'],f['close'],decimals,profile)
            item=dict(time=t,**item);attempts.append(item);reject=bool(item['rejected_by']);margin=qty*p/5
        if reject:return
        held=dict(id=len(predicted)+1,direction=-1,opened_ms=t,qty=qty,entry=ep,entry_fee=qty*ep*fee,entry_reference_mid=p,
            stop=ep*(1+stop),target=ep*(1-targetdist),signal_bar_ms=bars[j-1]['T'],signal_i=j,signal_atr=D(f['atr']),signal_close=D(f['close']),
            planned_loss=qty*ep*(stop+fr),initial_margin_model=margin,funding_events=[])
        predicted.append(held);cash-=held['entry_fee'];mark(p)
    for j,b in enumerate(bars):
        if b['t']<block['start']:continue
        prev=None
        for field,off in zip('ohlc' if row['path']=='OHLC' else 'olhc',OFF):
            at=b['t']+off;p=D(b[field])
            if prev is not None and held is not None:
                a,pa=prev
                if pending is None:
                    hit=crossing(a,pa,at,p,held['stop'],held['target'],factor)
                    if hit:arm(hit[0],hit[1],linear(a,pa,at,p,hit[0]),'WITHIN_MINUTE_INTERPOLATION')
                if pending is not None and pending['due_ms']<at:
                    due=pending['due_ms'];require(due>a,'Reference missed pending')
                    fill(due,linear(a,pa,at,p,due))
            settle(at);mark(p);value=update(p)
            if (peak-value)/peak>=D('.05') or value<=D('9.5'):halt_at(at,'ACCOUNT_DRAWDOWN_TRIGGER')
            had=held is not None
            if held is not None:
                raw=p*factor
                if halt is not None:close(at,p,halt_reason)
                elif raw>=held['stop']:arm(at,'STOP_OBSERVED_PRICE',p,'ORIGINAL_OBSERVATION')
                elif raw<=held['target']:arm(at,'TARGET_OBSERVED_PRICE',p,'ORIGINAL_OBSERVATION')
                elif at-held['opened_ms']>=21600000:close(at,p,'MAX_HOLD_TIME')
            if not had and off==2000 and at>row['start_ms']:opening(at,p,j)
            if held is not None and pending is not None:
                if pending['due_ms']<=at:fill(at,p)
                elif at-held['opened_ms']>=21600000:close(at,p,'MAX_HOLD_TIME')
            update(p);lastobs=at;prev=(at,p)
        if halt is not None and held is None:break
    if held is not None:close(lastobs,p,'BACKTEST_SEGMENT_END_ASSUMED_FILL');update(p)
    return dict(trades=predicted,events=events,attempts=attempts,cash=cash,dd=dd,halt=halt,halt_reason=halt_reason,
                lastobs=lastobs,marginmin=marginmin,markcount=markcount)


def audit(row,block,ref):
    require(row['execution_model'] in LATENCIES,'Unknown reference model')
    require(not row['open_position'] and not row['pending_funding'] and not row['integrity_warnings'],'Incomplete production result')
    cfg=row['account_config'];profile=row['candidate']
    require(profile in ('BASE_B','RISK125','RISK125_TP_HALF'),'Unknown reference profile')
    require(cfg['mode']=='paper_only' and cfg['coin']=='ETH','Frozen market mode')
    for k,vv in dict(initial_balance='10',minimum_cash_reserve='3.50',risk_fraction_per_trade='.0125',
                     account_halt_drawdown='.05',stop_floor_fraction='.003',stop_ceiling_fraction='.01',
                     atr_multiplier='1.5',reward_to_risk='.9' if profile=='RISK125_TP_HALF' else '1.8',
                     max_notional_to_equity='1.15' if profile=='BASE_B' else '2',
                     leverage_for_margin='2' if profile=='BASE_B' else '5').items():eq(cfg[k],vv,'Frozen config '+k)
    require(cfg['max_consecutive_losses']==3 and cfg['max_hold_seconds']==21600 and cfg['cooldown_seconds']==900,'Frozen timing policy')
    r=simulate(row,block,ref)
    require(len(r['trades'])==len(row['trades']),'Reference trade count')
    net=[];stress=[];fee,half,slip=COSTS[row['cost']]
    for actual,t in zip(row['trades'],r['trades']):
        for key,v in t.items():
            if key in ('funding_events','exit_trigger'):continue
            if isinstance(v,D):
                if key=='signal_atr':close_feature(actual[key],v,'Independent ATR')
                else:eq(actual[key],v,'Trade '+key)
            else:require(actual[key]==v,'Trade '+key)
        require(actual['pattern_trace'].keys()==ref[t['signal_i']]['trace'].keys(),'Signal trace keys')
        for key,v in ref[t['signal_i']]['trace'].items():
            if key in ('rsi','prior_rsi','sma5','er','lower','upper') or isinstance(v,(D,float)):close_feature(actual['pattern_trace'][key],v,'Signal trace '+key)
            else:require(actual['pattern_trace'][key]==v,'Signal trace '+key)
        require(len(actual['funding_events'])==len(t['funding_events']),'Funding count')
        for a,e in zip(actual['funding_events'],t['funding_events']):
            for k,v in e.items():eq(a[k],v,'Funding '+k)
            require(a['quality']=='PRECEDING_CANDLE_CLOSE_PROXY_NOT_ORACLE','Funding quality')
        funding=sum((e['amount'] for e in t['funding_events']),D(0))
        net.append(t['gross_pnl']-t['entry_fee']-t['exit_fee']+funding)
        p0,p1=t['entry_reference_mid'],t['exit_reference_mid'];se=p0*(1-D('.00015'))*(1-D('.0003'));sx=p1*(1+D('.00015'))*(1+D('.0003'))
        if t['close_reason']=='TARGET_OBSERVED_PRICE':sx=max(sx,t['target'])
        stress.append(t['qty']*(se-sx)-t['qty']*(se+sx)*D('.0009')+funding)
    require(len(row['trigger_log'])==len(r['events']),'Trigger count')
    for a,b in zip(row['trigger_log'],r['events']):
        require(a.keys()==b.keys(),'Trigger keys')
        for k,v in b.items():
            if isinstance(v,D):eq(a[k],v,'Trigger '+k)
            else:require(a[k]==v,'Trigger '+k)
    require(len(row['sizing_attempts'])==len(r['attempts']),'Sizing attempts count')
    for a,b in zip(row['sizing_attempts'],r['attempts']):
        for k,v in b.items():
            if isinstance(v,D):eq(a[k],v,'Sizing '+k)
            elif k=='quantity_caps':
                for kk,vv in v.items():eq(a[k][kk],vv,'Sizing cap '+kk)
            else:require(a[k]==v,'Sizing '+k)
    eq(row['ending_usdc'],r['cash'],'Ending cash');eq(row['sampled_max_drawdown_pct'],r['dd']*100,'Account DD')
    require(row['halt_ms']==r['halt'] and row['halt_reason']==r['halt_reason'] and row['last_observation_ms']==r['lastobs'],'Account halt/time')
    eq(row['metrics']['net_usdc'],sum(net,D(0)),'Total net')
    eq(row['metrics']['fixed_trades_stress_net_usdc'],sum(stress,D(0)),'Fixed trade cost stress')
    require(row['metrics']['wins']==sum(x>0 for x in net) and row['metrics']['trades']==len(net),'Win/count')
    require(row['metrics']['net_win_rate']==(sum(x>0 for x in net)/len(net) if net else None),'Win rate')
    require(row['metrics']['exit_reasons']==dict(Counter(t['close_reason'] for t in r['trades'])),'Exit counts')
    if r['marginmin'] is not None:eq(row['minimum_margin_buffer'],r['marginmin'],'Min maintenance buffer')
    require(row['margin_checks']==r['markcount'],'Margin observations')
    return dict(status='PASS_INDEPENDENT_BINARY_CROSSING_LEDGER_REPLAY',trades=len(net),triggers=len(r['events']),funding_events=sum(len(t['funding_events']) for t in r['trades']))
