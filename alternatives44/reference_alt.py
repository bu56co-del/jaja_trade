"""Independent arithmetic/event scan. Imports no strategy or execution engine."""
from decimal import Decimal as D, ROUND_CEILING
from bisect import bisect_right
from collections import Counter


def require(ok,msg):
    if not ok:raise ValueError(msg)


def eq(a,b,msg):
    a,b=D(str(a)),D(str(b))
    require(abs(a-b)<=max(D('1e-17'),abs(b)*D('1e-17')),msg)


def close_feature(a,b,msg):
    if isinstance(a,dict):
        require(a.keys()==b.keys(),msg)
        for k in a:close_feature(a[k],b[k],msg+'.'+str(k))
    elif isinstance(a,list):
        require(len(a)==len(b),msg)
        for x,y in zip(a,b):close_feature(x,y,msg)
    else:
        x,y=D(str(a)),D(str(b))
        require(abs(x-y)<=max(D('1e-12'),abs(y)*D('1e-12')),msg)


def feature_reference(bars):
    out={}
    def exponential(xs,n):
        answer=[];m=xs[0];a=D(2)/D(n+1)
        for x in xs:
            if not answer:answer.append(x)
            else:m=(1-a)*m+a*x;answer.append(m)
        return answer
    def relative(xs,n):
        up=[max(D(0),xs[k]-xs[k-1]) for k in range(1,len(xs))]
        dn=[max(D(0),xs[k-1]-xs[k]) for k in range(1,len(xs))]
        avgup=sum(up[:n])/n;avgdown=sum(dn[:n])/n;values=[]
        for k in range(n-1,len(up)):
            if k>=n:avgup=(avgup*(n-1)+up[k])/n;avgdown=(avgdown*(n-1)+dn[k])/n
            values.append(D(50) if avgup==avgdown==0 else D(100) if avgdown==0 else 100-100/(1+avgup/avgdown))
        return values
    def bands(xs,n):
        part=xs[len(xs)-n:];mu=sum(part)/n
        variance=max(D(0),sum(x*x for x in part)/n-mu*mu)
        sig=variance.sqrt()
        return {'mean':mu,'lower':mu-2*sig,'upper':mu+2*sig,'width':4*sig/mu}
    for i in range(120,len(bars)):
        history=bars[i-120:i];xs=[D(x['c']) for x in history]
        typical=[sum(D(b[k]) for k in ('h','l','c'))/3 for b in history]
        ranges=[max(D(history[j]['h']),xs[j-1])-min(D(history[j]['l']),xs[j-1]) for j in range(106,120)]
        total=sum(abs(xs[j]-xs[j-1]) for j in range(100,120))
        out[i]={'bar_ms':history[-1]['T'],'close':xs[-1],'prev':xs[-2],'atr':sum(ranges)/14,
                'er':abs(xs[-1]-xs[-21])/total if total else D(0),
                'rsi14':relative(xs,14)[-1],'rsi2':relative(xs,2)[-1],'rsi2prev':relative(xs,2)[-2],
                'e20':exponential(xs,20)[-2:],'e50':exponential(xs,50)[-2:],
                'volume_ratio':D(history[-1]['v'])*20/sum(D(b['v']) for b in history[-21:-1]),
                'bands':{str(n):bands(typical,n) for n in (20,40)},
                'prior_bands':{str(n):bands(typical[:-1],n) for n in (20,40)}}
    return out


def direction_reference(f,s):
    long=short=False;family=s['family'];n=s['param']
    if family=='BB_FADE':
        long=f['close']<f['bands'][str(n)]['lower'] and f['rsi14']<30
        short=f['close']>f['bands'][str(n)]['upper'] and f['rsi14']>70
    elif family=='BB_EXPAND':
        current,prior=f['bands'][str(n)],f['prior_bands'][str(n)]
        enough=current['width']-prior['width']>D('1e-12') and f['volume_ratio']>=D('1.2')
        long=enough and f['prev']<=prior['upper'] and f['close']>current['upper'] and f['rsi14']>=55
        short=enough and f['prev']>=prior['lower'] and f['close']<current['lower'] and f['rsi14']<=45
    elif family=='RSI_DIP':
        long=f['rsi2prev']>=n>f['rsi2'];short=f['rsi2prev']<=100-n<f['rsi2']
    else:raise ValueError('Unknown family')
    if s['filter']=='EMA20_50':
        long=long and f['e20'][1]>f['e50'][1] and f['e20'][1]>f['e20'][0]
        short=short and f['e20'][1]<f['e50'][1] and f['e20'][1]<f['e20'][0]
    elif s['filter']=='EMA50':
        long=long and f['close']>f['e50'][1]>f['e50'][0]
        short=short and f['close']<f['e50'][1]<f['e50'][0]
    return 1 if long else -1 if short else 0


def decision_reference(block,fv,s):
    bs=block['candles'];end=len(bs);out={j:{'direction':0,'atr':D(0),'close':D(bs[j-1]['c']),'trace':{}} for j in range(600,end)}
    raw={i:direction_reference(f,s) for i,f in fv.items()}
    release=-1
    for i,f in fv.items():
        j=i*5;d=raw[i]
        if j<=release or not d or raw.get(i-1,0)==d:continue
        chosen=None
        if s['trigger']=='DIRECT':chosen=j
        else:
            for at in range(j+1,min(j+6,end)):
                x,y=bs[at-2],bs[at-1]
                forward=(D(y['c'])>D(x['h']) and D(y['c'])>D(y['o'])) if d>0 else (D(y['c'])<D(x['l']) and D(y['c'])<D(y['o']))
                if forward:chosen=at;break
        release=chosen if chosen is not None else j+6
        if chosen is not None:
            mean=f['bands'][str(s['param'])]['mean'] if s['family']=='BB_FADE' else f['close']
            out[chosen]={'direction':d,'atr':f['atr'],'close':D(bs[chosen-1]['c']),
                'trace':{'setup_i':i,'setup_minute':j,'setup_end':f['bar_ms'],'trigger_minute':chosen,
                         'trigger_end':bs[chosen-1]['T'],'family':s['family'],'trigger':s['trigger'],'filter':s['filter'],
                         'mean':str(mean),'rsi14':str(f['rsi14']),'rsi2':str(f['rsi2']),'er':str(f['er']),'volume_ratio':str(f['volume_ratio'])}}
    return out


def check_choices(a,b):
    require(a.keys()==b.keys(),'Decision grid')
    for i in a:
        require(a[i]['direction']==b[i]['direction'],'Decision '+str(i))
        if a[i]['direction']:
            close_feature(a[i]['atr'],b[i]['atr'],'Decision ATR');eq(a[i]['close'],b[i]['close'],'Decision close')
            for key,x in a[i]['trace'].items():
                if key in ('mean','rsi14','rsi2','er','volume_ratio'):close_feature(x,b[i]['trace'][key],'Trace '+key)
                else:require(x==b[i]['trace'][key],'Trace '+key)


COST={'base_assumptions':(D('.00045'),D('.00005'),D('.0001')),
      'cost_stress':(D('.0009'),D('.00015'),D('.0003'))}


def audit(row,block,ref):
    # Price and financing assumptions are explicitly models, not exchange fills.
    require(not row['open_position'] and not row['pending_funding'] and not row['integrity_warnings'],'Incomplete account')
    fee,half,slip=COST[row['cost']];minute=60000;five=60000
    bs=block['candles'];base=bs[0]['t'];ends=[b['T'] for b in bs]
    funding={r['time']:D(r['rate']) for r in block['funding']}
    def px(at):
        n=(at-base)//minute;off=(at-base)%minute
        offsets=[2000,21000,40000,59998];require(off in offsets and 0<=n<len(bs),'Observation join')
        field=('ohlc' if row['path']=='OHLC' else 'olhc')[offsets.index(off)]
        return D(bs[n][field])
    cash=D(10);last=None;opens=[];nets=[];totalfund=0;stats=Counter();peak=D(10);maxdd=D(0)
    decomposition={k:D(0) for k in ('price_only_usdc','spread_slippage_usdc','fees_usdc','funding_usdc','target_cap_haircut_usdc','fixed_trades_stress_net_usdc')}
    stressed=[]
    # Independent recomputation of every fill, fee, funding and sizing constraint.
    for k,t in enumerate(row['trades'],1):
        at,closed=t['opened_ms'],t['closed_ms'];d=t['direction'];q=D(t['qty'])
        require(t['id']==k and d in (-1,1) and row['start_ms']<=at<closed<=row['end_ms'],'Trade identity/range')
        require(at%five==2000 and (last is None or at-last>=900000),'Cooldown/entry clock')
        require(sum(at-o<86400000 for o in opens)<6,'Entry cap');opens.append(at)
        require(row['halt_ms'] is None or at<row['halt_ms'],'No entry after halt')
        i=(at-base)//five;f=ref[i]
        require(f['direction']==d and t['signal_bar_ms']==base+i*five-1 and t['signal_i']==i,'Causal signal join')
        close_feature(t['signal_atr'],f['atr'],'Independent signal ATR');eq(t['signal_close'],f['close'],'Independent signal close')
        require(t['pattern_trace'].keys()==f['trace'].keys(),'Trace keys')
        for tk,tv in f['trace'].items():
            if tk in ('mean','rsi14','rsi2','er','volume_ratio'):close_feature(t['pattern_trace'][tk],tv,'Independent trace '+tk)
            else:require(t['pattern_trace'][tk]==tv,'Independent trace '+tk)
        p0,p1=px(at),px(closed);ep=p0*(1+d*half)*(1+d*slip);observed=p1*(1-d*half)*(1-d*slip)
        xp=observed
        if t['close_reason']=='TARGET_OBSERVED_PRICE':xp=min(xp,D(t['target'])) if d==1 else max(xp,D(t['target']))
        eq(t['entry'],ep,'Entry fill');eq(t['exit'],xp,'Exit fill');eq(t['observed_exit_price'],observed,'Observed exit')
        eq(t['entry_reference_mid'],p0,'Entry reference');eq(t['exit_reference_mid'],p1,'Exit reference')
        eq(t['target_cap_haircut_usdc'],d*q*(observed-xp),'Target cap')
        expected=(D('10.10')/(p0*(1+d*half))*10000).to_integral_value(rounding=ROUND_CEILING)/10000
        eq(q,expected,'Quantity rounding')
        atr=D(t['signal_atr']);c=D(t['signal_close']);stop=max(D('.003'),D('1.5')*atr/c);friction=2*(fee+half+slip)
        require(stop<=D('.01') and q*ep>=10 and q*ep<=cash*D('1.15'),'Stop/notional risk')
        require(q*p0/2+q*ep*fee+D('3.5')<=cash and q*ep*(stop+friction)<=cash*D('.0125'),'Margin/risk reserve')
        require(stop*D('1.8')>=3*friction,'Cost gate')
        if row['strategy_spec']['filter']=='ROOM':require(d*(D(f['trace']['mean'])-ep)/ep>=3*friction,'Mean room gate')
        eq(t['stop'],ep*(1-d*stop),'Stop');eq(t['target'],ep*(1+d*stop*D('1.8')),'Target')
        eq(t['planned_loss'],q*ep*(stop+friction),'Planned loss');eq(t['initial_margin_model'],q*p0/2,'Margin')
        ef,xf=q*ep*fee,q*xp*fee;gross=d*q*(xp-ep)
        eq(t['entry_fee'],ef,'Entry fee');eq(t['exit_fee'],xf,'Exit fee');eq(t['gross_pnl'],gross,'Gross pnl')
        events={ts for ts in funding if at<ts<=closed}
        require(len(t['funding_events'])==len(events) and {e['time'] for e in t['funding_events']}==events,'Funding events')
        ft=D(0)
        for e in t['funding_events']:
            j=bisect_right(ends,e['time'])-1;oracle=D(bs[j]['c']);amount=-d*q*oracle*funding[e['time']]
            require(j>=0 and 0<=e['time']-ends[j]<=90000 and e['oracle_sample_ms']==ends[j],'Preceding funding price')
            eq(e['oracle'],oracle,'Funding proxy');eq(e['rate'],funding[e['time']],'Funding rate');eq(e['amount'],amount,'Funding cash')
            require(e['quality']=='PRECEDING_CANDLE_CLOSE_PROXY_NOT_ORACLE','Funding label');ft+=amount
        net=gross-ef-xf+ft;nets.append(net);cash+=net;last=closed;totalfund+=len(events)
        stats[t['close_reason']]+=1
        pure=d*q*(p1-p0);se=p0*(1+d*D('.00015'))*(1+d*D('.0003'));sx=p1*(1-d*D('.00015'))*(1-d*D('.0003'))
        if t['close_reason']=='TARGET_OBSERVED_PRICE':sx=min(sx,D(t['target'])) if d==1 else max(sx,D(t['target']))
        stress=d*q*(sx-se)-q*(se+sx)*D('.0009')+ft;stressed.append(stress)
        for key,value in dict(price_only_usdc=pure,spread_slippage_usdc=pure-d*q*(observed-ep),fees_usdc=ef+xf,
                              funding_usdc=ft,target_cap_haircut_usdc=d*q*(observed-xp),fixed_trades_stress_net_usdc=stress).items():
            decomposition[key]+=value
    eq(row['ending_usdc'],cash,'Ending balance');m=row['metrics'];n=len(nets);w=sum(v>0 for v in nets)
    eq(m['net_usdc'],sum(nets,D(0)),'Net');require(m['trades']==n and m['wins']==w and m['losses']==sum(v<0 for v in nets),'Win-loss counts')
    require(m['net_win_rate']==(w/n if n else None),'Win rate');require(m['exit_reasons']==dict(stats),'Exit reasons')
    for key,value in decomposition.items():eq(m[key],value,'Cost decomposition '+key)
    require(m['fixed_trades_stress_win_rate']==(sum(x>0 for x in stressed)/n if n else None),'Same-trade stress wins')
    if n:eq(m['mean_net_usdc'],sum(nets)/n,'Mean net')
    require(m['observed55_and_positive']==bool(n and w/n>=.55 and sum(nets)>0),'55 flag')
    require(m['observed60_and_positive']==bool(n and w/n>=.60 and sum(nets)>0),'60 flag')
    # Scan each held-position observation: no missed earlier risk/target/time exit.
    open_map={t['opened_ms']:t for t in row['trades']};close_map={t['closed_ms']:t for t in row['trades']}
    funds=sorted((e['time'],D(e['amount'])) for t in row['trades'] for e in t['funding_events'])
    pointer=0;cash=D(10);held=None;losses=0;first_halt=None
    for b in bs:
        if b['t']+59998<row['start_ms'] or b['t']>row['last_observation_ms']:continue
        for off in (2000,21000,40000,59998):
            at=b['t']+off
            if at<row['start_ms'] or at>row['last_observation_ms']:continue
            while pointer<len(funds) and funds[pointer][0]<=at:cash+=funds[pointer][1];pointer+=1
            p=px(at);pre=cash;reason=None
            if held:
                qty=D(held['qty']);ddir=held['direction'];entry=D(held['entry']);raw=p*(1-ddir*half)*(1-ddir*slip)
                cap=min(raw,D(held['target'])) if ddir==1 else max(raw,D(held['target']))
                pre+=ddir*qty*(cap-entry)-qty*cap*fee
                peak=max(peak,pre);maxdd=max(maxdd,(peak-pre)/peak)
                if (peak-pre)/peak>=D('.05') or pre<=D('9.5'):reason='ACCOUNT_DRAWDOWN_TRIGGER'
                elif ddir*(raw-D(held['stop']))<=0:reason='STOP_OBSERVED_PRICE'
                elif ddir*(raw-D(held['target']))>=0:reason='TARGET_OBSERVED_PRICE'
                elif at-held['opened_ms']>=360*60000:reason='MAX_HOLD_TIME'
                if reason:require(at==held['closed_ms'] and held['close_reason']==reason,'First exit mismatch')
                if at==held['closed_ms']:
                    require(reason is not None or (at==row['end_ms'] and held['close_reason']=='BACKTEST_SEGMENT_END_ASSUMED_FILL'),'Unexplained exit')
                    cash+=D(held['gross_pnl'])-D(held['exit_fee'])
                    net=D(held['gross_pnl'])-D(held['entry_fee'])-D(held['exit_fee'])+sum((D(e['amount']) for e in held['funding_events']),D(0))
                    losses=losses+1 if net<0 else 0
                    if (reason=='ACCOUNT_DRAWDOWN_TRIGGER' or losses>=3) and first_halt is None:first_halt=at
                    held=None
            if at in open_map:
                require(held is None and first_halt is None,'Overlap/restart')
                held=open_map[at];cash-=D(held['entry_fee'])
            post=cash
            if held:
                ddir=held['direction'];qty=D(held['qty']);raw=p*(1-ddir*half)*(1-ddir*slip)
                cap=min(raw,D(held['target'])) if ddir==1 else max(raw,D(held['target']))
                post+=ddir*qty*(cap-D(held['entry']))-qty*cap*fee
            peak=max(peak,pre,post);maxdd=max(maxdd,(peak-pre)/peak,(peak-post)/peak)
    require(held is None,'Open tail');eq(cash,row['ending_usdc'],'Chronological cash');eq(maxdd*100,row['sampled_max_drawdown_pct'],'Drawdown')
    require(first_halt==row['halt_ms'],'Halt time')
    return dict(status='PASS_INDEPENDENT_SIGNAL_JOIN_FIRST_RISK_EXIT_AND_LEDGER',trades=n,funding_events=totalfund)
