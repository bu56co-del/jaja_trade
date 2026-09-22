"""Separate backwards peak search and second-moment variance; no production filter import."""
from decimal import Decimal as D
from reference_alt import require, close_feature
import reference_threshold as prior
from verify_mean import check as check_decisions, audit


def bars15(block):
    result = []
    minutes = block['candles']
    require(len(minutes) % 15 == 0, 'Incomplete reference aggregation')
    for start in range(0, len(minutes), 15):
        rows = minutes[start:start+15]
        result.append(dict(t=rows[0]['t'], T=rows[-1]['T'],
                           h=max(D(b['h']) for b in rows), c=D(rows[-1]['c'])))
    return result


def reference_peaks(bars, i):
    selected = []
    # Start from the most recent possible peak and search backwards, unlike production.
    for k in range(i-2, max(19, i-12)-1, -1):
        if bars[k-1]['h'] >= bars[k]['h'] or bars[k+1]['h'] >= bars[k]['h']:
            continue
        xs = [b['c'] for b in bars[k-19:k+1]]
        avg = sum(xs)/20
        var = sum(x*x for x in xs)/20 - avg*avg
        require(var >= 0, 'Reference variance negative')
        selected.append(dict(bar_index=k, high=str(bars[k]['h']), close_ms=bars[k]['T'],
                             confirmed_ms=bars[k+1]['T'], mean20=str(avg), variance20=str(var),
                             z=str((bars[k]['h']-avg)/var.sqrt()) if var > 0 else None))
        if len(selected) == 2:
            break
    return list(reversed(selected))


def reference(block, spec, cost_name, raw):
    definitions = {
        'BASE030': ('V030_R30', 'NONE'), 'BASE035': ('V035_R30', 'NONE'),
        'LOWER_HIGH': ('V035_R30', 'LOWER_HIGH'), 'Z_WEAKENING': ('V035_R30', 'Z_WEAKENING'),
    }
    require(spec.get('id') in definitions and set(spec) == {'id', 'parent', 'pattern'}, 'Reference spec')
    require((spec['parent'], spec['pattern']) == definitions[spec['id']], 'Reference spec mutation')
    old = dict(id=spec['parent'], min_vol='0.0030' if spec['id']=='BASE030' else '0.0035', room_multiple='3.0')
    result, gates = prior.reference(block, old, cost_name, raw)
    bars, reports = bars15(block), []
    for gate in gates:
        j, pattern = gate['index'], spec['pattern']
        peaks = reference_peaks(bars, j//15) if pattern != 'NONE' else []
        if pattern == 'NONE':
            ok, reason = True, 'NO_STRUCTURE_FILTER'
        elif len(peaks) != 2:
            ok, reason = False, 'FEWER_THAN_TWO_CONFIRMED_PEAKS'
        elif any(p['z'] is None for p in peaks):
            ok, reason = False, 'ZERO_STANDARD_DEVIATION'
        elif pattern == 'LOWER_HIGH':
            ok = D(peaks[0]['high']) > D(peaks[1]['high'])
            reason = 'LOWER_HIGH' if ok else 'NOT_LOWER_HIGH'
        else:
            ok = D(peaks[0]['high']) <= D(peaks[1]['high']) and D(peaks[0]['z'])-D(peaks[1]['z']) > D('0.000000000001')
            reason = 'Z_WEAKENING' if ok else 'NO_HIGHER_HIGH_Z_WEAKENING'
        if not ok:
            result[j] = dict(result[j], direction=0)
        reports.append(dict(index=j, base_gate=gate, pattern_allowed=ok, reason=reason,
                            peaks=peaks, final_direction=result[j]['direction']))
    return result, reports


def check(actual, expected, events, reference_events):
    check_decisions(actual, expected)
    require(len(events) == len(reference_events), 'Structure event count')
    # Also check inherited gate values independently, even for rejected events.
    prior.check(actual, expected, [e['base_gate'] for e in events], [e['base_gate'] for e in reference_events])
    for a, b in zip(events, reference_events):
        require(a.keys() == b.keys(), 'Structure event fields')
        for key in ('index', 'pattern_allowed', 'reason', 'final_direction'):
            require(a[key] == b[key], 'Structure decision '+key)
        require(len(a['peaks']) == len(b['peaks']), 'Peak count')
        for p, q in zip(a['peaks'], b['peaks']):
            require(p.keys() == q.keys(), 'Peak fields')
            for key in ('bar_index', 'close_ms', 'confirmed_ms'):
                require(p[key] == q[key], 'Peak time/index')
            for key in ('high', 'mean20', 'variance20', 'z'):
                if p[key] is None or q[key] is None:
                    require(p[key] == q[key], 'Undefined peak feature')
                else:
                    close_feature(p[key], q[key], 'Peak '+key)
