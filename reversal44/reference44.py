"""Independent feature/forward-event scan and monetary checks. No engine import."""
from decimal import Decimal as D, ROUND_CEILING
from bisect import bisect_right
from collections import Counter


def require(ok, msg):
    if not ok:raise ValueError(msg)


def eq(a,b,msg):
    a,b=D(str(a)),D(str(b))
    require(abs(a-b)<=max(D('1e-17'),abs(b)*D('1e-17')),msg)


def line(values,n):
    a=D(2)/(n+1);out=[values[0]]
    for x in values[1:]:out.append(out[-1]*(1-a)+a*x)
    return out


def features_ref(bars):
    out={}
    for end in range(120,len(bars)):
        h=bars[end-120:end];prices=[D(x['c']) for x in h]
        trs=[max(D(h[j]['h'])-D(h[j]['l']),abs(D(h[j]['h'])-prices[j-1]),abs(D(h[j]['l'])-prices[j-1])) for j in range(1,120)]
        a=bars[end-21:end-1];old=bars[end-22:end-2];impulse=bars[end-7:end-1]
        out[end]=dict(bar_ms=h[-1]['T'],close=prices[-1],atr=sum(trs[-14:])/14,
             o=D(h[-1]['o']),h=D(h[-1]['h']),l=D(h[-1]['l']),prev=prices[-2],
             upper=max(D(x['h']) for x in a),lower=min(D(x['l']) for x in a),
             previous_upper=max(D(x['h']) for x in old),previous_lower=min(D(x['l']) for x in old),
             swing_low=min(D(x['l']) for x in impulse),swing_high=max(D(x['h']) for x in impulse),
             lines={str(n):line(prices,n)[-2:] for n in (9,21,20,50)})
    return out


def check_features(actual,ref):
    require(actual.keys()==ref.keys(),'Feature key coverage')
    for i in actual:
        for k in actual[i]:
            if k=='lines':
                for n in ref[i][k]:
                    for a,b in zip(actual[i][k][n],ref[i][k][n]):eq(a,b,'EMA feature')
            else:eq(actual[i][k],ref[i][k],'Feature '+k)


def permit(f,trend,d):
    if trend=='none':return True
    x,y=trend.split('_');fast=f['lines'][x];slow=f['lines'][y]
    if d>0:return fast[1]>slow[1] and fast[1]>fast[0]
    return fast[1]<slow[1] and fast[1]<fast[0]


def reference_decisions(fv,spec):
    """Scan forward from each frozen event, rather than importing the production state machine."""
    out={i:dict(direction=0,arm_i=None,touch_i=None) for i in fv}
    if not fv:return out
    cursor=min(fv);last=max(fv);family=spec['family']
    while cursor<=last:
        f=fv[cursor]
        if family=='EMA':
            a,b=f['lines']['9'],f['lines']['21'];d=0
            if a[0]<=b[0] and a[1]>b[1]:d=1
            if a[0]>=b[0] and a[1]<b[1]:d=-1
            out[cursor]['direction']=d;cursor+=1;continue
        over=f['h']>f['upper']+f['atr']/10;under=f['l']<f['lower']-f['atr']/10
        if over==under:cursor+=1;continue
        d=1 if over else -1;boundary=f['upper'] if over else f['lower']
        extreme=f['h'] if over else f['l'];anchor=f['swing_low'] if over else f['swing_high']
        if (family!='FAKE' and d*(f['close']-boundary)<=f['atr']/10) or d*(extreme-anchor)<=0:
            cursor+=1;continue
        armed=cursor
        if family=='BREAK':
            crossed=f['prev']<=f['previous_upper'] if d==1 else f['prev']>=f['previous_lower']
            if crossed and permit(f,spec['trend'],d):out[cursor].update(direction=d,arm_i=cursor)
            cursor+=1;continue
        if family=='FAKE':
            end=min(last,armed+3);emitted=False
            for j in range(armed,end+1):
                c=fv[j]
                recovery=d*(extreme-c['close'])/abs(extreme-anchor)
                inside=d*(c['close']-boundary)<=-f['atr']/10
                body=d*(c['close']-c['o'])<0
                enough=spec['ratio']=='none' or recovery>=D(spec['ratio'])
                if inside and body and enough:
                    if permit(c,spec['trend'],-d):out[j].update(direction=-d,arm_i=armed)
                    cursor=j+1;emitted=True;break
            if not emitted:cursor=end+1
            continue
        level=boundary if spec['ratio']=='none' else extreme-d*D(spec['ratio'])*abs(extreme-anchor)
        touch=None;end=min(last,armed+12);cursor=end+1
        for j in range(armed+1,end+1):
            c=fv[j]
            if touch is not None and j>touch+2:
                cursor=j;break # expiry bar may arm a new event
            if d*(c['close']-anchor)<0:
                cursor=j+1;break
            if touch is None:
                if c['l']<=level+f['atr']*D('.15') and c['h']>=level-f['atr']*D('.15') and d*(c['prev']-level)>0:
                    touch=j
            else:
                br=c['close']>fv[touch]['h'] if d==1 else c['close']<fv[touch]['l']
                if br and d*(c['close']-c['o'])>0 and d*(c['close']-boundary)>=0:
                    if permit(c,spec['trend'],d):out[j].update(direction=d,arm_i=armed,touch_i=touch)
                    cursor=j+1;break
    return out


def check_decisions(actual,ref):
    require(actual.keys()==ref.keys(),'Signal key coverage')
    for i in actual:
        require(actual[i]['direction']==ref[i]['direction'],'Signal direction differs at '+str(i))
        if ref[i]['direction'] and 'arm_i' in actual[i]['trace']:
            require(actual[i]['trace']['arm_i']==ref[i]['arm_i'] and actual[i]['trace']['touch_i']==ref[i]['touch_i'],'Setup chronology')


COST={'base_assumptions':(D('.00045'),D('.00005'),D('.0001')),
      'cost_stress':(D('.0009'),D('.00015'),D('.0003'))}


def audit(row,block,ref):
    # Price and financing assumptions are explicitly models, not exchange fills.
    require(not row['open_position'] and not row['pending_funding'] and not row['integrity_warnings'],'Incomplete account')
    fee,half,slip=COST[row['cost']];minute=60000;five=300000
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
