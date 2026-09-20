"""Causal 5m false-break and confirmed-retest research. No IO or orders."""
from decimal import Decimal as D
from paperlab.engine import ema

RATIOS = ('none', '0.5', '0.618', '0.66')
FILTERS = ('none', '9_21', '20_50')


def specs():
    result = [dict(id=f'{family}_{ratio}_{trend}', family=family, ratio=ratio, trend=trend,
                   max_minutes=360, exit='risk_time_only')
              for family in ('FAKE', 'RETEST') for ratio in RATIOS for trend in FILTERS]
    return result + [dict(id=name, family=family, ratio='none', trend=trend,
                          max_minutes=360, exit='risk_time_only') for name, family, trend in
                     (('CONTROL_BREAK', 'BREAK', 'none'), ('CONTROL_BREAK_EMA', 'BREAK', '9_21'),
                      ('CONTROL_EMA_CROSS', 'EMA', 'none'))]


def features(bars):
    """Key i uses only bars[i-120:i], available before next 5m open."""
    out = {}
    for i in range(120, len(bars)):
        history = bars[i-120:i]
        c = [D(x['c']) for x in history]
        tr = [max(D(x['h'])-D(x['l']), abs(D(x['h'])-c[j-1]), abs(D(x['l'])-c[j-1]))
              for j, x in enumerate(history) if j]
        lines = {p: ema(c, p) for p in (9, 21, 20, 50)}
        out[i] = dict(bar_ms=history[-1]['T'], close=c[-1], atr=sum(tr[-14:], D(0))/14,
                      o=D(history[-1]['o']), h=D(history[-1]['h']), l=D(history[-1]['l']), prev=c[-2],
                      upper=max(D(b['h']) for b in bars[i-21:i-1]),
                      lower=min(D(b['l']) for b in bars[i-21:i-1]),
                      previous_upper=max(D(b['h']) for b in bars[i-22:i-2]),
                      previous_lower=min(D(b['l']) for b in bars[i-22:i-2]),
                      swing_low=min(D(b['l']) for b in bars[i-7:i-1]),
                      swing_high=max(D(b['h']) for b in bars[i-7:i-1]),
                      lines={str(p): [v[-2], v[-1]] for p, v in lines.items()})
    return out


def aligned(f, name, direction):
    if name == 'none':
        return True
    fast, slow = name.split('_')
    a, b = f['lines'][fast], f['lines'][slow]
    return direction*(a[-1]-b[-1]) > 0 and direction*(a[-1]-a[-2]) > 0


def make_event(i, f, family):
    margin = D('.10')*f['atr']
    # A bar sweeping both sides is ambiguous even when it closes outside one side.
    up, down = f['h'] > f['upper']+margin, f['l'] < f['lower']-margin
    if up == down:
        return None
    d = 1 if up else -1
    boundary = f['upper'] if up else f['lower']
    if family != 'FAKE' and d*(f['close']-boundary) <= margin:
        return None
    a = f['swing_low'] if up else f['swing_high']
    e = f['h'] if up else f['l']
    if d*(e-a) <= 0:
        return None
    return dict(arm_i=i, arm_ms=f['bar_ms'], broken_direction=d, boundary=boundary,
                anchor=a, extreme=e, event_atr=f['atr'], touch_i=None)


def trace(event, i, f, spec, direction):
    return dict(candidate=spec['id'], signal_i=i, signal_bar_ms=f['bar_ms'], direction=direction,
                arm_i=event['arm_i'], arm_ms=event['arm_ms'], broken_direction=event['broken_direction'],
                boundary=str(event['boundary']), anchor=str(event['anchor']), extreme=str(event['extreme']),
                event_atr=str(event['event_atr']), touch_i=event.get('touch_i'),
                touch_high=str(event['touch_high']) if 'touch_high' in event else None,
                touch_low=str(event['touch_low']) if 'touch_low' in event else None,
                level=str(event['level']) if 'level' in event else None,
                depth=str(event['broken_direction']*(event['extreme']-f['close'])/abs(event['extreme']-event['anchor'])),
                ratio=spec['ratio'], ema_filter=spec['trend'])


def decisions(fv, spec):
    if spec not in specs():
        raise ValueError('Not a frozen candidate')
    state = None
    out = {}
    for i, f in sorted(fv.items()):
        direction = 0
        detail = None
        rejected = False
        family = spec['family']
        if family == 'EMA':
            a, b = f['lines']['9'], f['lines']['21']
            direction = 1 if a[0] <= b[0] and a[1] > b[1] else -1 if a[0] >= b[0] and a[1] < b[1] else 0
            if direction:
                detail = dict(candidate=spec['id'], signal_i=i, signal_bar_ms=f['bar_ms'], direction=direction)
        elif family == 'BREAK':
            state = make_event(i, f, family)
            if state:
                d = state['broken_direction']
                crossed = f['prev'] <= f['previous_upper'] if d == 1 else f['prev'] >= f['previous_lower']
                direction = d if crossed and aligned(f, spec['trend'], d) else 0
                if direction:
                    detail = trace(state, i, f, spec, d)
            state = None
        else:
            expired = bool(state and (i-state['arm_i'] > (3 if family == 'FAKE' else 12) or
                          (family == 'RETEST' and state['touch_i'] is not None and i-state['touch_i'] > 2)))
            if expired:
                state = None
            if state is None:
                state = make_event(i, f, family)
            if state:
                d = state['broken_direction']
                margin = D('.10')*state['event_atr']
                if family == 'FAKE':
                    depth = d*(state['extreme']-f['close'])/abs(state['extreme']-state['anchor'])
                    reclaimed = d*(f['close']-state['boundary']) <= -margin and -d*(f['close']-f['o']) > 0
                    enough = spec['ratio'] == 'none' or depth >= D(spec['ratio'])
                    if reclaimed and enough:
                        direction = -d if aligned(f, spec['trend'], -d) else 0
                        rejected = not bool(direction)
                        detail = trace(state, i, f, spec, direction) if direction else None
                        state = None
                elif i > state['arm_i']:
                    if d*(f['close']-state['anchor']) < 0:
                        state = None
                    else:
                        level = state['boundary'] if spec['ratio'] == 'none' else state['extreme']-d*D(spec['ratio'])*abs(state['extreme']-state['anchor'])
                        state['level'] = level
                        if state['touch_i'] is not None:
                            beyond_touch = f['close'] > state['touch_high'] if d == 1 else f['close'] < state['touch_low']
                            if i > state['touch_i'] and beyond_touch and d*(f['close']-f['o']) > 0 and d*(f['close']-state['boundary']) >= 0:
                                direction = d if aligned(f, spec['trend'], d) else 0
                                rejected = not bool(direction)
                                detail = trace(state, i, f, spec, direction) if direction else None
                                state = None
                        elif f['l'] <= level+D('.15')*state['event_atr'] and f['h'] >= level-D('.15')*state['event_atr'] and d*(f['prev']-level) > 0:
                            state.update(touch_i=i, touch_high=f['h'], touch_low=f['l'])
        out[i] = dict(direction=direction, trace=detail, ema_rejected=rejected)
    return out
