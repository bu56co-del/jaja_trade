"""Independent signal, first-exit and monetary checks; no import of paired or engine."""
from bisect import bisect_right
from decimal import Decimal as D, ROUND_CEILING
from collections import Counter
import math
from verify_long import fact, require, eq, COST

MINUTE=60000
QUARTER=900000


def reference_features(bars, actual):
    require(set(actual)==set(range(120,len(bars))), 'Feature coverage')
    result={}
    for i in actual:
        r=fact(bars[i-120:i])
        r.update(upper=max(D(b['h']) for b in bars[i-21:i-1]),
                 lower=min(D(b['l']) for b in bars[i-21:i-1]),
                 last_two_closes=[D(b['c']) for b in bars[i-2:i]],
                 last_two_ends=[b['T'] for b in bars[i-2:i]])
        require(set(r)==set(actual[i]),'Feature schema')
        for k,v in r.items():
            if isinstance(v,(list,bool,int)):require(v==actual[i][k],'Causal '+k)
            else:eq(v,actual[i][k],'Causal '+k)
        result[i]=r
    return result


def policy_exit(f,t,spec):
    if spec['exit']=='confirm':return f['confirm_long'] if t['direction']==1 else f['confirm_short']
    if any(x<=t['opened_ms'] for x in f['last_two_ends']):return False
    boundary=D(t['breakout_boundary']);margin=D(t['signal_atr'])/4
    if t['direction']==1:return max(f['last_two_closes'])<boundary-margin
    return min(f['last_two_closes'])>boundary+margin


def ref_price(data,at,mode,path):
    dt=QUARTER if mode=='COARSE_15M' else MINUTE
    bs=data['15m' if dt==QUARTER else '1m']['candles']
    i=(at-bs[0]['t'])//dt;off=(at-bs[0]['t'])%dt
    offsets=[2000,dt*7//20,dt*2//3,dt-2]
    require(0<=i<len(bs) and off in offsets,'Not an execution observation')
    fields='ohlc' if path=='OHLC' else 'olhc'
    return D(bs[i][fields[offsets.index(off)]])


def exit_px(raw,t,mode,fee,half,slip,cap_valuation=False):
    p=raw*(1-t['direction']*half)*(1-t['direction']*slip)
    if mode=='FINE_1M_TP_CAP' and (cap_valuation or t['close_reason']=='TARGET_OBSERVED_PRICE'):
        p=min(p,D(t['target'])) if t['direction']==1 else max(p,D(t['target']))
    return p


def audit(row,data,reference):
    require(not row['open_position'] and not row['pending_funding'] and not row['integrity_warnings'],'Incomplete ledger')
    mode,spec=row['mode'],row['strategy_spec'];fee,half,slip=COST[row['cost']]
    require(mode in ('COARSE_15M','FINE_1M','FINE_1M_TP_CAP'),'Mode')
    require(row['initial_usdc']=='10' and row['candidate']==spec['id'],'Account identity')
    require(row['account_config']['max_consecutive_losses']==3 and row['account_config']['risk_fraction_per_trade']=='0.0125'
            and row['account_config']['account_halt_drawdown']=='0.05','Risk rules changed')
    bs=data['15m']['candles'];ends=[b['T'] for b in bs];base=bs[0]['t']
    funding={r['time']:D(r['rate']) for r in data['15m']['funding']}
    trades=row['trades'];net=[];pure=[];friction=[];fees=[];fund=[];caps=[];stress=[]
    last=None;opens=[];cash=D(10)
    for idx,t in enumerate(trades,1):
        at,closed=t['opened_ms'],t['closed_ms'];q=D(t['qty']);d=t['direction']
        require(t['id']==idx and d in (-1,1) and row['start_ms']<=at<closed<=row['end_ms'],'Trade identity')
        require(at%QUARTER==2000 and (last is None or at-last>=900000),'Entry/cooldown')
        require(row['halt_ms'] is None or at<=row['halt_ms'],'Entry after permanent halt')
        require(sum(at-o<86400000 for o in opens)<6,'Entry limit');opens.append(at)
        i=(at-base)//QUARTER;f=reference[i]
        require(t['entry_15m_index']==i and f['break']==d and t['signal_bar_ms']==f['bar_ms']<at,'Signal join')
        eq(f['atr'],t['signal_atr'],'ATR');eq(f['close'],t['signal_close'],'Signal close')
        eq(t['breakout_boundary'],f['upper'] if d==1 else f['lower'],'Frozen breakout boundary')
        p0=ref_price(data,at,mode,row['path']);p1=ref_price(data,closed,mode,row['path'])
        eq(t['entry_reference_mid'],p0,'Entry raw ref');eq(t['exit_reference_mid'],p1,'Exit raw ref')
        ep=p0*(1+d*half)*(1+d*slip);xp=exit_px(p1,t,mode,fee,half,slip)
        observed=p1*(1-d*half)*(1-d*slip);haircut=d*q*(observed-xp)
        eq(t['entry'],ep,'Entry fill');eq(t['exit'],xp,'Exit fill');eq(t['observed_exit_price'],observed,'Observed exit')
        eq(t['target_cap_haircut_usdc'],haircut,'Target cap');require(haircut>=0,'Beneficial cap')
        step=D(1).scaleb(-data['15m']['metadata']['sz_decimals'])
        expected=(D('10.10')/(p0*(1+d*half))/step).to_integral_value(rounding=ROUND_CEILING)*step
        eq(q,expected,'Lot rounding')
        stop=max(D('.003'),D('1.5')*f['atr']/f['close']);cost=2*(fee+half+slip)
        require(stop<=D('.01') and 10<=q*ep<=cash*D('1.15'),'Notional stop cap')
        require(q*p0/2+q*ep*fee+D('3.5')<=cash and q*ep*(stop+cost)<=cash*D('.0125'),'Margin and risk')
        require(stop*D('1.8')>=3*cost,'Cost gate')
        eq(t['stop'],ep*(1-d*stop),'Stop');eq(t['target'],ep*(1+d*stop*D('1.8')),'Target')
        eq(t['planned_loss'],ep*q*(stop+cost),'Planned loss');eq(t['initial_margin_model'],q*p0/2,'Margin')
        eq(t['entry_raw_vwap'],p0*(1+d*half),'Entry VWAP');eq(t['exit_raw_vwap'],xp/(1-d*slip),'Exit VWAP')
        eq(t['entry_adverse_cost'],abs(ep-p0*(1+d*half))*q,'Entry adverse')
        eq(t['exit_adverse_cost'],abs(xp-xp/(1-d*slip))*q,'Exit adverse')
        ef,xf=q*ep*fee,q*xp*fee;gross=d*q*(xp-ep)
        eq(t['entry_fee'],ef,'Entry fee');eq(t['exit_fee'],xf,'Exit fee');eq(t['gross_pnl'],gross,'Gross')
        expected_events={ts for ts in funding if at<ts<=closed}
        require(len(t['funding_events'])==len(expected_events) and {r['time'] for r in t['funding_events']}==expected_events,'Funding coverage')
        ft=D(0)
        for r in t['funding_events']:
            ts=r['time'];j=bisect_right(ends,ts)-1;oracle=D(bs[j]['c'])
            require(j>=0 and 0<=ts-ends[j]<=90000 and r['oracle_sample_ms']==ends[j],'Funding sample')
            amount=-d*q*oracle*funding[ts]
            eq(r['oracle'],oracle,'Proxy');eq(r['rate'],funding[ts],'Rate');eq(r['amount'],amount,'Funding cash')
            require(r['quality']=='PRECEDING_CANDLE_CLOSE_PROXY_NOT_ORACLE','Oracle label');ft+=amount
        n=gross-ef-xf+ft;net.append(n);pure.append(d*q*(p1-p0));fees.append(ef+xf);fund.append(ft);caps.append(haircut)
        friction.append(pure[-1]-d*q*(observed-ep));cash+=n;last=closed
        se=p0*(1+d*D('.00015'))*(1+d*D('.0003'))
        sx=exit_px(p1,t,mode,D('.0009'),D('.00015'),D('.0003'))
        stress.append(d*q*(sx-se)-q*(se+sx)*D('.0009')+ft)
    m=row['metrics'];total=sum(net,D(0));n=len(net);w=sum(v>0 for v in net)
    for k,v in [('net_usdc',total),('price_only_usdc',sum(pure)),('spread_slippage_usdc',sum(friction)),
                ('fees_usdc',sum(fees)),('funding_usdc',sum(fund)),('target_cap_haircut_usdc',sum(caps)),
                ('fixed_trades_stress_net_usdc',sum(stress)),('return_pct',total*10)]:eq(m[k],v,k)
    eq(row['ending_usdc'],10+total,'Final balance')
    wins=[v for v in net if v>0];losses=[v for v in net if v<0]
    for key,seq in (('mean_win_usdc',wins),('mean_loss_usdc',losses)):
        if seq:eq(m[key],sum(seq)/len(seq),key)
        else:require(m[key] is None,'Undefined mean')
    if losses:eq(m['profit_factor'],sum(wins)/-sum(losses),'Profit factor')
    else:require(m['profit_factor'] is None,'Undefined profit factor')
    require(m['trades']==n and m['wins']==w and m['losses']==sum(v<0 for v in net) and m['ties']==sum(v==0 for v in net),'Counts')
    require(m['exit_reasons']==dict(Counter(t['close_reason'] for t in trades)),'Exit count')
    if n:
        eq(m['net_win_rate'],w/n,'Win rate');eq(m['mean_net_usdc'],total/n,'Average')
        eq(m['fixed_trades_stress_win_rate'],sum(v>0 for v in stress)/n,'Fixed stress rate')
        z=1.959963984540054;p=w/n;den=1+z*z/n;mid=(p+z*z/(2*n))/den
        rad=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        for got,expected in zip(m['wilson95_iid'],(max(0,mid-rad),min(1,mid+rad))):eq(got,expected,'Wilson')
    else:require(m['net_win_rate'] is None and m['wilson95_iid'] is None,'No trades is not 0 win rate')
    require(m['observed55_and_positive']==bool(n and w/n>=.55 and total>0),'55 flag')
    require(m['observed60_and_positive']==bool(n and w/n>=.6 and total>0),'60 flag')
    require(m['return5']==(total>=D('.5')),'Return flag')
    audit_chronology(row,data,reference)
    return dict(status='PASS_INDEPENDENT_CAUSAL_FIRST_EXIT_AND_LEDGER',trades=n,
                funding_events=sum(len(t['funding_events']) for t in trades),exact_market_fills='NOT_VERIFIED')


def audit_chronology(row,data,reference):
    """Check every held-position observation, including no earlier missed exit."""
    dt=QUARTER if row['mode']=='COARSE_15M' else MINUTE
    bs=data['15m' if dt==QUARTER else '1m']['candles'];base15=data['15m']['candles'][0]['t']
    fee,half,slip=COST[row['cost']];trades=row['trades'];peak=D(10);dd=D(0)
    cash=D(10);fund=[]
    for t in trades:
        fund.extend((r['time'],D(r['amount'])) for r in t['funding_events'])
    fund.sort();pointer=0;open_by={t['opened_ms']:t for t in trades};close_by={t['closed_ms']:t for t in trades}
    held=None;first_halt=None;closed_nets=[];halt_reason=''
    for b in bs:
        if b['T']<row['start_ms'] or b['t']>row['last_observation_ms']:continue
        for offset in (2000,dt*7//20,dt*2//3,dt-2):
            at=b['t']+offset
            if not row['start_ms']<=at<=row['last_observation_ms']:continue
            while pointer<len(fund) and fund[pointer][0]<=at:
                cash+=fund[pointer][1];pointer+=1
            price=ref_price(data,at,row['mode'],row['path'])
            pre=cash
            if held:
                liquid=exit_px(price,held,row['mode'],fee,half,slip,True)
                pre+=held['direction']*D(held['qty'])*(liquid-D(held['entry']))-D(held['qty'])*liquid*fee
            peak=max(peak,pre);dd=max(dd,(peak-pre)/peak)
            acct=(peak-pre)/peak>=D('.05') or pre<=D('9.5')
            if acct and first_halt is None:first_halt=at;halt_reason='ACCOUNT_DRAWDOWN_TRIGGER'
            expected=None
            if held:
                d=held['direction'];obs=price*(1-d*half)*(1-d*slip)
                if acct:expected='ACCOUNT_DRAWDOWN_TRIGGER'
                elif d*(obs-D(held['stop']))<=0:expected='STOP_OBSERVED_PRICE'
                elif d*(obs-D(held['target']))>=0:expected='TARGET_OBSERVED_PRICE'
                elif at-held['opened_ms']>=row['strategy_spec']['max_minutes']*MINUTE:expected='MAX_HOLD_TIME'
                elif at%QUARTER==2000 and policy_exit(reference[(at-base15)//QUARTER],held,row['strategy_spec']):
                    expected='BREAKOUT_FAILURE_CONFIRMED' if row['strategy_spec']['exit']=='failure' else 'CONFIRMED_TREND_REVERSAL'
                elif at==row['end_ms']:expected='BACKTEST_WINDOW_END_ASSUMED_FILL'
                require((at in close_by)==(expected is not None),'Missed/early exit at '+str(at))
                if expected:
                    require(close_by[at] is held and held['close_reason']==expected,'First exit priority')
                    cash+=D(held['gross_pnl'])-D(held['exit_fee'])
                    n=D(held['gross_pnl'])-D(held['entry_fee'])-D(held['exit_fee'])+sum((D(r['amount']) for r in held['funding_events']),D(0))
                    closed_nets.append(n)
                    if len(closed_nets)>=3 and all(x<0 for x in closed_nets[-3:]) and first_halt is None:
                        first_halt=at;halt_reason='CONSECUTIVE_LOSSES'
                    held=None
            if at in open_by:
                require(held is None and first_halt is None,'Overlap or entry after halt')
                held=open_by[at];cash-=D(held['entry_fee'])
            post=cash
            if held:
                liquid=exit_px(price,held,row['mode'],fee,half,slip,True)
                post+=held['direction']*D(held['qty'])*(liquid-D(held['entry']))-D(held['qty'])*liquid*fee
            peak=max(peak,post);dd=max(dd,(peak-post)/peak)
    require(held is None,'Unclosed trade')
    eq(row['sampled_max_drawdown_pct'],dd*100,'Chronological drawdown')
    require(row['halt_ms']==first_halt and row['halt_reason']==halt_reason,'Halt time/reason mismatch')
    eq(row['ending_usdc'],cash,'Chronological cash')


def audit_shadow(shadow,data,start,end,reference,rows):
    require(shadow['mode']=='SHADOW_SIGNAL_DIAGNOSTIC_NOT_TRADES' and shadow['win_rate'] is None
            and shadow['account_return'] is None,'Shadow mislabelled as performance')
    raw={b['t']:b for b in data['1m']['candles']};fifteen=data['15m']['candles']
    expected={fifteen[i]['t']+2000:f for i,f in reference.items() if start<=fifteen[i]['t']<end and f['break']}
    got={r['signal_open_ms']:r for r in shadow['signals']}
    require(len(got)==len(shadow['signals']) and set(got)==set(expected),'Shadow signal coverage')
    for at,s in got.items():
        f=expected[at];t=at-2000;d=f['break'];p=D(raw[t]['o'])
        require(s['direction']==d and s['signal_bar_ms']==f['bar_ms'],'Shadow causal signal')
        eq(s['reference_open'],p,'Shadow open')
        for h in (90,360):
            value=s['labels'][str(h)];finish=t+h*MINUTE
            if finish>=end:require(value=={'status':'RIGHT_CENSORED'},'Censored future not missing');continue
            require(value['status']=='OBSERVED_REFERENCE_ONLY','Shadow label')
            eq(value['signed_reference_return'],d*(D(raw[finish]['o'])/p-1),'Shadow horizon')
            changes=[d*(D(raw[x][k])/p-1) for x in range(t,finish,MINUTE) for k in 'hl']
            eq(value['favorable_excursion_fraction'],max([D(0)]+changes),'Shadow MFE')
            eq(value['adverse_excursion_fraction'],min([D(0)]+changes),'Shadow MAE')
    assigned=shadow['after_halt_by_case'];require(len(assigned)==len(rows),'Shadow account coverage')
    for a,r in zip(assigned,rows):
        require(all(a[k]==r[k] for k in ('candidate','mode','cost','path','halt_ms')),'Shadow identity')
        wanted=[at for at in expected if r['halt_ms'] is not None and at>r['halt_ms']]
        require(a['signal_open_ms']==wanted,'Shadow after-halt label')
    return {'status':'PASS_INDEPENDENT_SHADOW_LABELS_NOT_TRADES','signals':len(got)}
