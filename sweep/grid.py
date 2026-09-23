"""Finite exhaustive hybrid grammar. No network, trading, or fitted parameters."""
from itertools import combinations, permutations, product
from hashlib import sha256
import json

KEYS = ('eff', 'breakout', 'elder', 'rsi')
STATES = tuple(product((-1, 0, 1), repeat=4))
GATES = ('none', 'trend', 'eff20')
EXITS = ('opposite', 'dual')


def vote(st, rule):
    vals = [st[k] for k in rule['keys']]
    if rule['kind'] == 'priority':
        return next((v for v in vals if v), 0)
    p = sum(w for v, w in zip(vals, rule['weights']) if v == 1)
    m = sum(w for v, w in zip(vals, rule['weights']) if v == -1)
    t = rule['threshold']
    if rule['policy'] == 'veto':
        return 0 if p and m else 1 if p >= t else -1 if m >= t else 0
    return 1 if p-m >= t else -1 if m-p >= t else 0


def rules():
    """Return every raw expression and canonical 81-state truth table."""
    expressions = []
    for n in (2, 3, 4):
        for ks in combinations(range(4), n):
            for ws in [(1,)*n] + [tuple(2 if j == x else 1 for j in range(n)) for x in range(n)]:
                for t in range(1, sum(ws)+1):
                    for policy in ('veto', 'net'):
                        expressions.append(dict(kind='vote', keys=list(ks), weights=list(ws), threshold=t, policy=policy))
        expressions.extend(dict(kind='priority', keys=list(ks)) for ks in permutations(range(4), n))
    canonical = {}
    for r in expressions:
        table = tuple(vote(s, r) for s in STATES)
        record = canonical.setdefault(table, dict(rule=r, table=list(table), aliases=[]))
        record['aliases'].append(r)
    return expressions, list(canonical.values())


def stable_id(spec):
    return 'GRID_' + sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]


def previous_table(rule):
    return list(vote(st, rule) for st in STATES)


def all_specs():
    _, rs = rules()
    specs = []
    old_union = previous_table(dict(kind='vote', keys=[0,1,2,3], weights=[1]*4, threshold=1, policy='veto'))
    old_vote = previous_table(dict(kind='vote', keys=[0,1,2], weights=[1]*3, threshold=2, policy='veto'))
    old_weighted = previous_table(dict(kind='vote', keys=[0,1,2,3], weights=[1,1,2,1], threshold=3, policy='veto'))
    for record in rs:
        for horizon, gate, exit_ in product((1,3), GATES, EXITS):
            spec = dict(kind='aggregate', table=record['table'], representative=record['rule'],
                        horizon=horizon, gate=gate, exit=exit_)
            # IDs depend on behaviour, not the chosen representative spelling.
            spec['id'] = stable_id({k:v for k,v in spec.items() if k!='representative'})
            old = None
            if gate=='none':
                if horizon==1 and spec['table']==old_union:
                    old = 'MIX_UNION_VETO' if exit_=='opposite' else 'MIX_UNION_DUAL_EXIT'
                if horizon==3 and exit_=='opposite':
                    if spec['table']==old_vote: old='MIX_VOTE_2OF3'
                    if spec['table']==old_weighted: old='MIX_WEIGHTED_VOTE'
            spec['previously_tested_as'] = old
            specs.append(spec)
    for a,b in permutations(range(4),2):
        for horizon,gate,exit_ in product((1,3),GATES,EXITS):
            spec=dict(kind='sequence',order=[a,b],horizon=horizon,gate=gate,exit=exit_)
            spec['id']=stable_id(spec);spec['previously_tested_as']=None;specs.append(spec)
    for order in permutations(range(4),3):
        for gate,exit_ in product(GATES,EXITS):
            spec=dict(kind='regime',order=list(order),horizon=1,gate=gate,exit=exit_)
            spec['id']=stable_id(spec);spec['previously_tested_as']=None;specs.append(spec)
    if len(specs)!=2424 or len({s['id'] for s in specs})!=2424:
        raise ValueError('Unexpected enumeration coverage')
    return specs


def streams(nodes, spec, length):
    """Causal entry and position-conditioned exits; no prices after index i."""
    entries, raw = [0]*(length+1), [0]*(length+1)
    def recent(i,k,h,strict=False):
        now=nodes[i]['t']
        indices=range(i-1,i-h-1,-1) if strict else range(i,i-h,-1)
        for j in indices:
            if j in nodes and 0 <= now-nodes[j]['t'] <= (h if strict else h-1)*60000:
                if nodes[j][KEYS[k]]:return nodes[j][KEYS[k]]
        return 0
    for i in range(120,length+1):
        f=nodes[i];kind=spec['kind']
        if kind=='aggregate':
            vals=[recent(i,k,spec['horizon']) for k in range(4)]
            index=sum((v+1)*3**(3-k) for k,v in enumerate(vals))
            d=spec['table'][index]
            raw[i]=d
            if spec['horizon']==3 and i>120 and raw[i-1]==d:d=0
        elif kind=='sequence':
            a,b=spec['order'];d=f[KEYS[b]]
            d=d if d and recent(i,a,spec['horizon'],strict=True)==d else 0
        elif kind=='regime':
            # Decimal comparison to floats is not used for threshold boundaries.
            from decimal import Decimal
            slot=0 if f['er']>=Decimal('.35') else 2 if f['er']<=Decimal('.20') else 1
            d=f[KEYS[spec['order'][slot]]]
        else:raise ValueError('Unknown grammar')
        if spec['gate']=='trend' and d!=f['trend']:d=0
        if spec['gate']=='eff20':
            from decimal import Decimal
            if f['er']<Decimal('.20'):d=0
        entries[i]=d
    encoded_e=bytes(d+1 for d in entries)
    exits=[]
    for i,d in enumerate(entries):
        longexit,shortexit=d==-1,d==1
        if i in nodes and spec['exit']=='dual':
            f=nodes[i]
            longexit |= f['trend']==-1 or f['k']>=80
            shortexit |= f['trend']==1 or f['k']<=20
        exits.append(int(longexit)+2*int(shortexit))
    return encoded_e,bytes(exits)


def reference_streams(nodes,spec,length):
    """Separately formulated policy evaluator (does not use vote/streams/table)."""
    from decimal import Decimal
    out=[0]*(length+1);previous=0
    def events(i,k,age,include):
        candidates=[]
        for j in range(max(120,i-age), i+1 if include else i):
            elapsed=nodes[i]['t']-nodes[j]['t']
            allowed=0<=elapsed<age*60000 if include else 0<elapsed<=age*60000
            if allowed and nodes[j][KEYS[k]]:candidates.append((j,nodes[j][KEYS[k]]))
        return max(candidates)[1] if candidates else 0
    for i in range(120,length+1):
        f=nodes[i]
        if spec['kind']=='aggregate':
            r=spec['representative'];weights=r.get('weights',[1]*len(r['keys']))
            vals=[events(i,k,spec['horizon'],True) for k in r['keys']]
            if r['kind']=='priority':
                nz=[v for v in vals if v];d=nz[0] if nz else 0
            else:
                total=sum(v*w for v,w in zip(vals,weights))
                conflict=min(vals)<0<max(vals)
                d=(0 if r['policy']=='veto' and conflict else
                   (1 if total>=r['threshold'] else -1 if total<=-r['threshold'] else 0))
            trigger=0 if spec['horizon']==3 and i>120 and d==previous else d
            previous=d;d=trigger
        elif spec['kind']=='sequence':
            setup,confirm=spec['order'];last=events(i,setup,spec['horizon'],False)
            d=f[KEYS[confirm]] if last==f[KEYS[confirm]] else 0
        else:
            slot=2
            if f['er']>Decimal('.20'):slot=1
            if f['er']>=Decimal('.35'):slot=0
            d=f[KEYS[spec['order'][slot]]]
        if spec['gate']=='trend':d=d if d*f['trend']>0 else 0
        if spec['gate']=='eff20' and f['er']<Decimal('.20'):d=0
        out[i]=d
    exits=[0]*(length+1)
    for i in range(120,length+1):
        for direction,bit in ((1,1),(-1,2)):
            close=out[i]*direction==-1
            if spec['exit']=='dual':
                f=nodes[i];close=close or f['trend']*direction==-1 or (f['k']>=80 if direction==1 else f['k']<=20)
            if close:exits[i]+=bit
    return bytes(d+1 for d in out),bytes(exits)
