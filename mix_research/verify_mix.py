"""Independent hybrid entry/exit and ledger checks; never imports hybrids/engine."""
from decimal import Decimal as D
import verify as v
from book_verify import reference as book_reference, audit as book_audit


def reference_node(history):
    v.require(len(history)==120,'Reference warmup')
    c=[D(b['c']) for b in history]
    travel=sum((abs(c[j]-c[j-1]) for j in range(len(c)-20,len(c))),D(0))
    er=abs(c[-1]-c[-21])/travel if travel else D(0)
    a,b=v.mean_line(c,16),v.mean_line(c,36)
    ema=1 if a[-1]>b[-1] and a[-2]<=b[-2] else -1 if a[-1]<b[-1] and a[-2]>=b[-2] else 0
    ceiling=max(D(x['h']) for x in history[-21:-1])
    floor=min(D(x['l']) for x in history[-21:-1])
    oldtop=max(D(x['h']) for x in history[-22:-2])
    oldlow=min(D(x['l']) for x in history[-22:-2])
    br=1 if c[-1]>ceiling and c[-2]<=oldtop else -1 if c[-1]<floor and c[-2]>=oldlow else 0
    groups={}
    for bar in history:
        groups.setdefault(bar['t']//300000,[]).append(bar)
    five=[]
    for start,g in sorted(groups.items()):
        if len(g)==5 and all(x['t']==start*300000+j*60000 and x['T']==x['t']+59999 for j,x in enumerate(g)):
            five.append(D(g[-1]['c']))
    trend=0
    if len(five)>=10:
        f,s=v.mean_line(five,3),v.mean_line(five,8)
        if f[-1]>s[-1] and f[-1]>f[-2]:trend=1
        if f[-1]<s[-1] and f[-1]<f[-2]:trend=-1
    def k(j):
        xs=history[j-4:j+1];top=max(D(x['h']) for x in xs);bottom=min(D(x['l']) for x in xs)
        return D(50) if top==bottom else (D(history[j]['c'])-bottom)*100/(top-bottom)
    elder=book_reference(history,dict(family='book_elder',higher_fast=3,higher_slow=8,threshold=30))
    rsi=book_reference(history,dict(family='book_rsi',trend_length=60,threshold=10))
    return dict(t=history[-1]['T'],er=er,trend=trend,k=k(119),eff=ema if er>=D('.2') else 0,
        breakout=br if er>=D('.2') else 0,elder=elder,rsi=rsi,
        seq_up=min(k(j) for j in (116,117,118))<=30 and c[-1]>max(D(x['h']) for x in history[-4:-1]),
        seq_down=max(k(j) for j in (116,117,118))>=70 and c[-1]<min(D(x['l']) for x in history[-4:-1]))


class Reference:
    def __init__(self,data):
        self.nodes={i:reference_node(data['candles'][i-120:i]) for i in range(120,len(data['candles'])+1)}
    def recent(self,i,name):
        now=self.nodes[i]['t']
        valid=[self.nodes[j] for j in range(i-2,i+1) if j in self.nodes and 0<=now-self.nodes[j]['t']<=120000 and self.nodes[j][name]]
        return valid[-1][name] if valid else 0
    def raw(self,i,kind):
        if i not in self.nodes:return 0
        f=self.nodes[i]
        if kind=='BREAK_TREND':return f['trend'] if f['breakout']==f['trend'] else 0
        if kind=='PULLBACK_EFF':return f['elder'] if f['er']>=D('.2') else 0
        if kind=='SEQUENCE':
            if f['er']<D('.2'):return 0
            if f['trend']==1 and f['seq_up']:return 1
            if f['trend']==-1 and f['seq_down']:return -1
            return 0
        if kind=='REGIME_SWITCH':
            if f['er']<=D('.2'):return f['rsi']
            if f['er']<D('.35'):return f['elder']
            return f['breakout'] if f['breakout']==f['trend'] else 0
        keys=('eff','breakout','elder','rsi')
        if kind in ('UNION_VETO','UNION_DUAL_EXIT'):
            votes=[(f[k],1) for k in keys];threshold=1
        elif kind=='VOTE_2OF3':
            votes=[(self.recent(i,k),1) for k in keys[:3]];threshold=2
        elif kind=='WEIGHTED_VOTE':
            votes=[(self.recent(i,k),w) for k,w in zip(keys,(1,1,2,1))];threshold=3
        else:raise ValueError('Unknown reference combination')
        positive=sum(w for d,w in votes if d==1);negative=sum(w for d,w in votes if d==-1)
        if positive and negative:return 0
        return 1 if positive>=threshold else -1 if negative>=threshold else 0
    def entry(self,i,kind):
        result=self.raw(i,kind)
        return 0 if kind in ('VOTE_2OF3','WEIGHTED_VOTE') and self.raw(i-1,kind)==result else result
    def exit(self,i,kind,d):
        f=self.nodes[i]
        trigger=self.entry(i,kind)==-d
        if kind in ('PULLBACK_EFF','SEQUENCE','UNION_DUAL_EXIT'):
            trigger=trigger or f['trend']==-d or (f['k']>=80 if d==1 else f['k']<=20)
        return trigger


def check_bank(bank,reference,kinds):
    v.require(bank.nodes.keys()==reference.nodes.keys(),'Reference feature count')
    for i,f in bank.nodes.items():
        other=reference.nodes[i]
        for k in f:
            if k in ('er','k'):v.equal(f[k],other[k],'Independent '+k)
            else:v.require(f[k]==other[k],'Independent '+k)
        for kind in kinds:
            v.require(bank.entry(i,kind)==reference.entry(i,kind),'Hybrid entry disagreement')
            for d in (-1,1):v.require(bank.exit(i,kind,d)==reference.exit(i,kind,d),'Hybrid exit disagreement')
    return dict(status='PASS_INDEPENDENT_CAUSAL_COMPONENTS_AND_COMBINATIONS',bars=len(bank.nodes),kinds=len(kinds))


def audit(row,data,spec,reference):
    if spec['family']!='mix':return book_audit(row,data,spec)
    def signal_check(history,trade,_):
        i=(history[-1]['t']-data['candles'][0]['t'])//60000+1
        v.require(reference.entry(i,spec['kind'])==trade['direction'],'Independent mixed entry mismatch')
        values=[D(b['c']) for b in history]
        tr=[max(D(b['h'])-D(b['l']),abs(D(b['h'])-values[j-1]),abs(D(b['l'])-values[j-1]))
            for j,b in enumerate(history) if j]
        atr=sum(tr[-14:])/14
        v.equal(trade['signal_atr'],atr,'ATR');v.equal(trade['signal_close'],values[-1],'Signal close')
        trace=trade['mix_trace'];f=reference.nodes[i]
        v.require(trace['signal_bar_ms']==f['t'] and trace['combination']==spec['kind'],'Trace identity')
        v.require(trace['components']=={k:f[k] for k in ('eff','breakout','elder','rsi')},'Trace components')
        v.require(trace['recent_votes']=={k:reference.recent(i,k) for k in ('eff','breakout','elder','rsi')},'Trace votes')
        v.equal(trace['efficiency'],f['er'],'Trace efficiency')
        v.require(trace['higher_trend']==f['trend'],'Trace trend')
        route=('breakout' if f['er']>=D('.35') else 'rsi' if f['er']<=D('.20') else 'elder') if spec['kind']=='REGIME_SWITCH' else spec['kind']
        v.require(trace['route']==route,'Trace route')
        return max(D('.003'),atr/values[-1]*D('1.5'))
    saved=v.signal_check
    try:
        v.signal_check=signal_check
        result=v.audit_row(row,data,spec)
    finally:
        v.signal_check=saved
    for t in row['trades']:
        if t['close_reason']=='MIX_SIGNAL_EXIT':
            v.require(t['closed_ms']%60000==2000,'Mixed exit time')
            i=(t['closed_ms']//60000*60000-data['candles'][0]['t'])//60000
            v.require(reference.exit(i,spec['kind'],t['direction']),'Independent mixed exit mismatch')
    return result
