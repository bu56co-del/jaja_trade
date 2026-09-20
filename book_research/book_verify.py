"""Independent book-entry/exit calculation; no import of book_strategies/engine."""
from decimal import Decimal as D
import verify as v


def reference(history, spec, direction=None):
    close = [D(b['c']) for b in history]
    grouped = {}
    for b in history:
        grouped.setdefault(b['t']//300000, []).append(b)
    complete = []
    for key in sorted(grouped):
        g = grouped[key]
        if len(g)==5 and all(b['t']==key*300000+i*60000 and b['T']==b['t']+59999 for i,b in enumerate(g)):
            complete.append(g)
    vals = [D(g[-1]['c']) for g in complete]
    if spec['family']=='book_rsi':
        if len(vals)<5 or complete[-1][-1]['T'] != history[-1]['T']:
            return False if direction else 0
        up, down = D(0), D(0)
        for i in range(1,len(vals)):
            change=vals[i]-vals[i-1]
            if i<=2:
                up+=max(change,D(0))/2; down+=max(-change,D(0))/2
            else:
                up=up/2+max(change,D(0))/2; down=down/2+max(-change,D(0))/2
        rsi=D(50) if up+down==0 else D(100)-D(100)*down/(up+down)
        avg5=sum(vals[-5:])/5
        if direction:
            return (rsi>70 or vals[-1]>avg5) if direction==1 else (rsi<30 or vals[-1]<avg5)
        n=spec['trend_length']; avg=sum(close[-n:])/n
        return (1 if close[-1]>avg and vals[-1]<avg5 and rsi<=spec['threshold'] else
                -1 if close[-1]<avg and vals[-1]>avg5 and rsi>=100-spec['threshold'] else 0)
    trend=0
    if len(vals)>=spec['higher_slow']+2:
        a,b=v.mean_line(vals,spec['higher_fast']),v.mean_line(vals,spec['higher_slow'])
        if a[-1]>b[-1] and a[-1]>a[-2]:trend=1
        if a[-1]<b[-1] and a[-1]<a[-2]:trend=-1
    def k_at(j):
        group=history[j-4:j+1]
        hi=max(D(b['h']) for b in group);lo=min(D(b['l']) for b in group)
        return D(50) if hi==lo else (D(history[j]['c'])-lo)*100/(hi-lo)
    last=len(history)-1
    if direction:
        return trend==-direction or (k_at(last)>=80 if direction==1 else k_at(last)<=20)
    ks=[k_at(last-i) for i in range(1,4)]
    return (1 if trend==1 and close[-1]>D(history[-2]['h']) and min(ks)<=spec['threshold'] else
            -1 if trend==-1 and close[-1]<D(history[-2]['l']) and max(ks)>=100-spec['threshold'] else 0)


def check_signal(history, trade, spec):
    v.require(reference(history,spec)==trade['direction'],'Independent book entry mismatch')
    closes=[D(b['c']) for b in history]
    ranges=[max(D(b['h'])-D(b['l']), abs(D(b['h'])-closes[i-1]), abs(D(b['l'])-closes[i-1]))
            for i,b in enumerate(history) if i]
    atr=sum(ranges[-14:])/14
    v.equal(trade['signal_atr'],atr,'Book ATR mismatch')
    v.equal(trade['signal_close'],closes[-1],'Book signal close mismatch')
    return max(D('.003'),atr/closes[-1]*D('1.5'))


def audit(row,data,spec):
    if not spec['family'].startswith('book_'):
        return v.audit_row(row,data,spec)
    old=v.signal_check
    try:
        v.signal_check=check_signal
        result=v.audit_row(row,data,spec)
    finally:
        v.signal_check=old
    for t in row['trades']:
        if t['close_reason']=='BOOK_SIGNAL_EXIT':
            v.require(t['closed_ms']%60000==2000,'Book exit outside next-open observation')
            index=(t['closed_ms']//60000*60000-data['candles'][0]['t'])//60000
            v.require(reference(data['candles'][index-120:index],spec,t['direction']), 'Independent book exit mismatch')
    result['book_entry_and_signal_exits']='PASS'
    return result
