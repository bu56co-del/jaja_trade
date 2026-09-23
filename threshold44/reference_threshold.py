"""Independent inequality formulation; no production threshold or engine import."""
from decimal import Decimal as D
from reference_alt import require, close_feature
from reference_bb import reference_extra, COST
from verify_mean import check as check_base, audit


def reference(block, spec, cost_name, raw):
    valid = {(f'V{v}_R{r}', a, b) for v,a in [('025','0.0025'),('030','0.0030'),('035','0.0035')]
             for r,b in [('25','2.5'),('30','3.0'),('35','3.5')]}
    require(set(spec) == {'id','min_vol','room_multiple'} and (spec['id'],spec['min_vol'],spec['room_multiple']) in valid, 'Unknown reference spec')
    fee, half, slip = COST[cost_name]
    out = {j: dict(row, direction=0) for j,row in raw.items()}
    reports = []
    for j,row in raw.items():
        if row['direction'] >= 0:
            continue
        require(j % 15 == 0, 'Reference setup clock')
        f = reference_extra(block, j)
        price = D(block['candles'][j]['o'])*(1-half)*(1-slip)
        atr, close = row['atr'], row['close']
        require(all(x.is_finite() and x>0 for x in (price,atr,close,f['mean'])), 'Reference market value')
        threshold = D(spec['room_multiple'])*(fee*2+half*2+slip*2)
        reasons = []
        if f['high'] == f['low'] or f['close']*2 > f['high']+f['low']:
            reasons.append('WEAK_CLOSE')
        if atr*D('1.5') < close*D(spec['min_vol']):
            reasons.append('LOW_VOL')
        if price-f['mean'] < threshold*price:
            reasons.append('ROOM')
        if not reasons:
            out[j] = dict(row, direction=-1)
        reports.append(dict(index=j,rejected_by=reasons,vol_fraction=str(atr/(close/D('1.5'))),
                            room_fraction=str(1-f['mean']/price),required_room=str(threshold),entry_fill=str(price)))
    return out, reports


def check(actual, expected, reports, reference_reports):
    check_base(actual, expected)
    require(len(reports) == len(reference_reports), 'Threshold event coverage')
    for a,b in zip(reports,reference_reports):
        require(a.keys() == b.keys() and a['index'] == b['index'] and a['rejected_by'] == b['rejected_by'], 'Gate decision differs')
        for k in ('vol_fraction','room_fraction','required_room','entry_fill'):
            close_feature(a[k],b[k],'Threshold '+k)
