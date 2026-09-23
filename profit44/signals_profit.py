"""Predeclared retest entry, stop and headroom ablations. No IO or orders."""
from decimal import Decimal as D
import signals as prior

PRIMARY = 'EARLY_STRUCTURE_R2.5_ROOM'
BASELINE = 'BOUNDARY_ATR_R1.8_NONE'
OLD_ID = 'RETEST_0.5_20_50'


def specs():
    return [dict(id=f'{entry}_{stop}_R{reward}_{room}', entry=entry, stop=stop,
                 reward=reward, room=room, family='RETEST', ratio='0.5', trend='20_50',
                 max_minutes=360, exit='risk_time_only')
            for entry in ('BOUNDARY','EARLY') for stop in ('ATR','STRUCTURE')
            for reward in ('1.8','2.5','3.0') for room in ('NONE','ROOM')]


def decisions(fv, spec):
    if spec not in specs():
        raise ValueError('Not a frozen profit hypothesis')
    state = None
    result = {}
    for i, f in sorted(fv.items()):
        direction = 0
        detail = None
        rejected = False
        if state and (i-state['arm_i'] > 12 or
                      (state['touch_i'] is not None and i-state['touch_i'] > 2)):
            state = None
        if state is None:
            state = prior.make_event(i, f, 'RETEST')
        if state and i > state['arm_i']:
            d = state['broken_direction']
            if d*(f['close']-state['anchor']) < 0:
                state = None
            else:
                level = state['extreme']-d*D('.5')*abs(state['extreme']-state['anchor'])
                state['level'] = level
                if state['touch_i'] is not None:
                    state['structure_low'] = min(state['structure_low'], f['l'])
                    state['structure_high'] = max(state['structure_high'], f['h'])
                    confirmed = f['close'] > state['touch_high'] if d == 1 else f['close'] < state['touch_low']
                    boundary = state['boundary'] if spec['entry']=='BOUNDARY' else level
                    if i > state['touch_i'] and confirmed and d*(f['close']-f['o']) > 0 and d*(f['close']-boundary) >= 0:
                        direction = d if prior.aligned(f, '20_50', d) else 0
                        rejected = not bool(direction)
                        if direction:
                            detail = prior.trace(state, i, f, spec, d)
                            detail.update(structure_low=str(state['structure_low']),
                                          structure_high=str(state['structure_high']),entry_mode=spec['entry'])
                        state = None
                elif f['l'] <= level+D('.15')*state['event_atr'] and f['h'] >= level-D('.15')*state['event_atr'] and d*(f['prev']-level)>0:
                    state.update(touch_i=i, touch_high=f['h'], touch_low=f['l'],
                                 structure_low=f['l'], structure_high=f['h'])
        result[i] = dict(direction=direction,trace=detail,ema_rejected=rejected)
    return result


def risk_plan(fill_price, signal, trace, cfg, friction, spec):
    """Distances use the actual model fill, not a replaced/falsified ATR."""
    p = D(fill_price)
    d = signal.direction
    atr_stop = max(D(cfg.stop_floor_fraction),signal.atr/signal.close*D(cfg.atr_multiplier))
    extreme = D(trace['structure_low'] if d==1 else trace['structure_high'])
    structural_price = extreme-d*D('.15')*D(trace['event_atr'])
    structure_fraction = d*(p-structural_price)/p
    stop = atr_stop if spec['stop']=='ATR' else max(atr_stop,structure_fraction)
    room = d*(D(trace['extreme'])-p)/p
    return dict(atr_stop_fraction=atr_stop,structural_price=structural_price,
                structure_fraction=structure_fraction,stop_fraction=stop,
                target_fraction=stop*D(spec['reward']),headroom_fraction=room,
                required_headroom_fraction=stop+friction,
                room_ok=spec['room']=='NONE' or room>=stop+friction)
