"""Independent forward-search signals and ledger verification. No engine import."""
from decimal import Decimal as D, ROUND_CEILING
from bisect import bisect_right
from collections import Counter
from reference44 import features_ref, check_features, require, eq, permit, COST


def reference_decisions(fv, spec):
    out = {i:dict(direction=0,arm_i=None,touch_i=None) for i in fv}
    if not fv:
        return out
    cursor, last = min(fv), max(fv)
    while cursor <= last:
        event = fv[cursor]
        up = event['h']>event['upper']+event['atr']/10
        down = event['l']<event['lower']-event['atr']/10
        if up==down:
            cursor+=1
            continue
        d = 1 if up else -1
        boundary = event['upper'] if up else event['lower']
        peak = event['h'] if up else event['l']
        origin = event['swing_low'] if up else event['swing_high']
        if d*(event['close']-boundary)<=event['atr']/10 or d*(peak-origin)<=0:
            cursor+=1
            continue
        armed = cursor
        level = (peak+origin)/2
        touch = None
        end = min(last,armed+12)
        cursor = end+1
        for j in range(armed+1,end+1):
            bar = fv[j]
            if touch is not None and j>touch+2:
                cursor=j
                break
            if d*(bar['close']-origin)<0:
                cursor=j+1
                break
            if touch is None:
                if (bar['l']<=level+event['atr']*D('.15') and
                    bar['h']>=level-event['atr']*D('.15') and d*(bar['prev']-level)>0):
                    touch=j
            else:
                threshold = fv[touch]['h'] if d==1 else fv[touch]['l']
                required = boundary if spec['entry']=='BOUNDARY' else level
                if (d*(bar['close']-threshold)>0 and d*(bar['close']-bar['o'])>0 and
                    d*(bar['close']-required)>=0):
                    if permit(bar,'20_50',d):
                        out[j].update(direction=d,arm_i=armed,touch_i=touch,atr=bar['atr'],close=bar['close'],
                                      arm_ms=event['bar_ms'],signal_bar_ms=bar['bar_ms'],
                                      structure_low=min(fv[k]['l'] for k in range(touch,j+1)),
                                      structure_high=max(fv[k]['h'] for k in range(touch,j+1)),
                                      boundary=boundary,anchor=origin,extreme=peak,event_atr=event['atr'],
                                      touch_high=fv[touch]['h'],touch_low=fv[touch]['l'],level=level,
                                      depth=d*(peak-bar['close'])/abs(peak-origin))
                    cursor=j+1
                    break
    return out


def check_decisions(actual, ref):
    require(actual.keys()==ref.keys(),'Signal grid')
    for i,r in ref.items():
        require(actual[i]['direction']==r['direction'],'Independent entry direction')
        if r['direction']:
            trace=actual[i]['trace']
            for k in ('arm_i','touch_i','arm_ms','signal_bar_ms','direction'):
                require(trace[k]==r[k],'Signal trace '+k)
            for k in ('structure_low','structure_high','boundary','anchor','extreme','event_atr','touch_low','touch_high','level','depth'):
                eq(trace[k],r[k],'Signal trace '+k)


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
        atr,c=f['atr'],f['close'];eq(t['signal_atr'],atr,'Independent ATR');eq(t['signal_close'],c,'Independent close')
        spec=row['strategy_spec'];reward=D(spec['reward']);friction=2*(fee+half+slip)
        astop=max(D('.003'),D('1.5')*atr/c)
        structural=(f['structure_low'] if d==1 else f['structure_high'])-d*D('.15')*f['event_atr']
        fraction=d*(ep-structural)/ep
        stop=astop if spec['stop']=='ATR' else max(astop,fraction)
        require(spec['stop']!='STRUCTURE' or fraction>0,'Invalid structure side')
        room=d*(f['extreme']-ep)/ep
        require(spec['room']=='NONE' or room>=stop+friction,'Room gate')
        expected_plan=dict(atr_stop_fraction=astop,structural_price=structural,structure_fraction=fraction,
                           stop_fraction=stop,target_fraction=stop*reward,headroom_fraction=room,
                           required_headroom_fraction=stop+friction)
        for name,value in expected_plan.items():eq(t['risk_plan'][name],value,'Risk plan '+name)
        require(t['risk_plan']['room_ok'] is True,'Admitted room')
        trace=t['pattern_trace']
        for name in ('structure_low','structure_high','extreme','anchor','boundary','level','event_atr','touch_high','touch_low','depth'):
            eq(trace[name],f[name],'Trade trace '+name)
        require(trace['arm_i']==f['arm_i'] and trace['touch_i']==f['touch_i'] and trace['candidate']==spec['id'],'Trade setup')
        require(stop<=D('.01') and q*ep>=10 and q*ep<=cash*D('1.15'),'Stop/notional risk')
        require(q*p0/2+q*ep*fee+D('3.5')<=cash and q*ep*(stop+friction)<=cash*D('.0125'),'Margin/risk reserve')
        require(stop*reward>=3*friction,'Cost gate')
        eq(t['stop'],ep*(1-d*stop),'Stop');eq(t['target'],ep*(1+d*stop*reward),'Target')
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
