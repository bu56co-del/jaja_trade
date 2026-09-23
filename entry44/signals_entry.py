"""Frozen low-volatility and causal one-minute confirmation ablations. No network."""
from decimal import Decimal as D
from inputs44 import need

BASE_ID = 'BB15_NONE_ROOM_LOWER_HALF'
PRIMARY = 'VOL'


def specs():
    return [dict(id=name, low_vol=vol, micro=micro) for name,vol,micro in
            [('BASE',False,False),('VOL',True,False),('MICRO',False,True),('VOL_MICRO',True,True)]]


def choices(block, spec, cost, base, extras):
    """Forward scan. base is the cost-specific original BB15 short decision stream.

    Only subsequent CLOSED minutes can confirm; cancellation is checked first.
    The account engine decides whether an emitted event can actually be traded.
    """
    need(spec in specs(), 'Unknown frozen entry definition')
    bars = block['candles']
    out = {}; events = []; pending = None
    fee=D(cost['taker_fee']); half=D(cost['spread_bps'])/20000
    slip=D(cost['adverse_slippage_bps'])/10000
    for j, original in base.items():
        need(600 <= j < len(bars) and bars[j]['t']-bars[j-1]['t']==60000,
             'Invalid decision time')
        current = dict(original, direction=0)
        if pending is not None:
            age = j-pending['setup_index']
            need(age >= 1, 'Cannot confirm on the setup bar')
            candle = bars[j-1]
            if D(candle['c']) > pending['high']:
                pending['report'].update(status='CANCELLED_HIGH',end_index=j)
                pending = None
            elif age > 5:
                pending['report'].update(status='EXPIRED',end_index=j)
                pending = None
            elif D(candle['c']) < D(candle['o']) and D(candle['c']) < D(bars[j-2]['l']):
                fill = D(bars[j]['o'])*(1-half)*(1-slip)
                room = (fill-pending['mean'])/fill
                required = 6*(fee+half+slip)
                allowed = room >= required
                trace = dict(pending['source']['trace'],
                    setup_index=str(pending['setup_index']),
                    setup_end_ms=str(bars[pending['setup_index']-1]['T']),
                    confirmation_end_ms=str(candle['T']), waiting_bars=str(age),
                    frozen_signal_high=str(pending['high']),frozen_mean20=str(pending['mean']),
                    frozen_setup_close=str(pending['source']['close']))
                current = dict(original, direction=-1 if allowed else 0,
                               atr=pending['source']['atr'],close=D(candle['c']),trace=trace)
                pending['report'].update(status='CONFIRMED' if allowed else 'REJECT_NEW_ROOM',
                    end_index=j,confirmation_end_ms=candle['T'],entry_fill=str(fill),
                    room_fraction=str(room),required_room=str(required))
                pending = None  # Consumed even if account or room later refuses the order.
            elif age == 5:
                pending['report'].update(status='EXPIRED',end_index=j)
                pending = None
        if original['direction'] == -1:
            need(j%15 == 0 and j in extras,'Original setup must use a closed 15m bar')
            raw = D('1.5')*original['atr']/original['close']
            item = dict(setup_index=j,setup_end_ms=bars[j-1]['T'],
                        raw_stop_fraction=str(raw),status='DIRECT',end_index=j)
            events.append(item)
            if spec['low_vol'] and raw < D('.003'):
                item['status']='REJECT_LOW_VOL'
            elif not spec['micro']:
                current=dict(original)
            elif pending is None:
                item.update(status='PENDING',end_index=None)
                pending=dict(setup_index=j,source=original,high=extras[j]['high'],
                             mean=extras[j]['mean'],report=item)
            else:
                item['status']='IGNORED_OVERLAP'
        out[j]=current
    if pending is not None:
        pending['report'].update(status='END_OF_BLOCK_UNCONFIRMED',end_index=len(bars))
    return out,events
