"""Nine fixed BB15 short admission gates. No networking, orders or exit changes."""
from decimal import Decimal as D
from itertools import product
from inputs44 import need

BASELINE = 'V030_R30'
PRIMARY = 'V035_R30'


def specs():
    return [dict(id='V'+vtag+'_R'+rtag, min_vol=v, room_multiple=r)
            for (vtag, v), (rtag, r) in product(
                [('025', '0.0025'), ('030', '0.0030'), ('035', '0.0035')],
                [('25', '2.5'), ('30', '3.0'), ('35', '3.5')])]


def gate(f, atr, close, open_price, spec, cost):
    need(spec in specs(), 'Unknown frozen threshold definition')
    fee, half, slip = D(cost['taker_fee']), D(cost['spread_bps'])/20000, D(cost['adverse_slippage_bps'])/10000
    values = [atr, close, D(open_price), f['mean'], f['high'], f['low'], f['close']]
    need(all(v.is_finite() and v > 0 for v in values), 'Invalid threshold market value')
    need(f['high'] >= f['close'] >= f['low'], 'Invalid signal candle bounds')
    need(all(v.is_finite() and 0 <= v < 1 for v in (fee, half, slip)), 'Invalid cost')
    fill = D(open_price)*(1-half)*(1-slip)
    vol = D('1.5')*atr/close
    room = (fill-f['mean'])/fill
    required = D(spec['room_multiple'])*2*(fee+half+slip)
    failures = []
    if not(f['high'] > f['low'] and f['close'] <= (f['high']+f['low'])/2):
        failures.append('WEAK_CLOSE')
    if vol < D(spec['min_vol']):
        failures.append('LOW_VOL')
    if room < required:
        failures.append('ROOM')
    return failures, dict(vol_fraction=str(vol), room_fraction=str(room), required_room=str(required), entry_fill=str(fill))


def choices(block, spec, cost, raw, extras):
    """Start with ALL causal BB short events, not a parent filtered at 3x cost."""
    need(spec in specs(), 'Unknown frozen threshold definition')
    out, reports = {}, []
    for j, row in raw.items():
        direction = 0
        if row['direction'] < 0:
            need(j % 15 == 0 and j in extras and block['candles'][j-1]['T'] < block['candles'][j]['t'], 'Noncausal setup')
            failures, values = gate(extras[j], row['atr'], row['close'], block['candles'][j]['o'], spec, cost)
            reports.append(dict(index=j, rejected_by=failures, **values))
            if not failures:
                direction = -1
        out[j] = dict(row, direction=direction)
    return out, reports
