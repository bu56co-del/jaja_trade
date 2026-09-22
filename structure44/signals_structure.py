"""Closed-bar double-peak filters. Paper-only; no network, orders, or exit changes."""
from decimal import Decimal as D
from inputs44 import aggregate, need
import signals_threshold as threshold

PRIMARY = 'LOWER_HIGH'
Z_TIE = D('1e-12')


def specs():
    return [
        dict(id='BASE030', parent='V030_R30', pattern='NONE'),
        dict(id='BASE035', parent='V035_R30', pattern='NONE'),
        dict(id='LOWER_HIGH', parent='V035_R30', pattern='LOWER_HIGH'),
        dict(id='Z_WEAKENING', parent='V035_R30', pattern='Z_WEAKENING'),
    ]


def parent_spec(spec):
    need(spec in specs(), 'Unknown frozen structure definition')
    return next(s for s in threshold.specs() if s['id'] == spec['parent'])


def peak(bars, k):
    """A peak's standardized height uses only its own 20 completed closes."""
    closes = [D(b['c']) for b in bars[k-19:k+1]]
    need(len(closes) == 20, 'Peak warmup missing')
    average = sum(closes) / 20
    variance = sum((c-average)**2 for c in closes) / 20
    return dict(bar_index=k, high=str(bars[k]['h']), close_ms=bars[k]['T'],
                confirmed_ms=bars[k+1]['T'], mean20=str(average), variance20=str(variance),
                z=str((D(bars[k]['h'])-average)/variance.sqrt()) if variance > 0 else None)


def contexts(block):
    """Advance through completed bars; a right-hand bar must close before admission."""
    bars = aggregate(block['candles'], 15)
    peaks, result = [], {}
    for i in range(1, len(bars)):
        # At the next bar's open i, bars 0..i-1, and ONLY those, are complete.
        k = i-2
        if k >= 19 and D(bars[k]['h']) > max(D(bars[k-1]['h']), D(bars[k+1]['h'])):
            peaks.append(peak(bars, k))
        peaks = [p for p in peaks if p['bar_index'] >= i-12]
        if i >= 40:
            result[i*15] = [dict(p) for p in peaks[-2:]]
    return result


def decision(peaks, pattern):
    need(pattern in ('NONE', 'LOWER_HIGH', 'Z_WEAKENING'), 'Unknown pattern')
    if pattern == 'NONE':
        return True, 'NO_STRUCTURE_FILTER'
    if len(peaks) < 2:
        return False, 'FEWER_THAN_TWO_CONFIRMED_PEAKS'
    first, second = peaks
    if first['z'] is None or second['z'] is None:
        return False, 'ZERO_STANDARD_DEVIATION'
    if pattern == 'LOWER_HIGH':
        ok = D(second['high']) < D(first['high'])
        return ok, 'LOWER_HIGH' if ok else 'NOT_LOWER_HIGH'
    ok = D(second['high']) >= D(first['high']) and D(second['z']) < D(first['z'])-Z_TIE
    return ok, 'Z_WEAKENING' if ok else 'NO_HIGHER_HIGH_Z_WEAKENING'


def choices(block, spec, cost, raw, extras, context):
    base, gates = threshold.choices(block, parent_spec(spec), cost, raw, extras)
    out, reports = {j: dict(row) for j, row in base.items()}, []
    for gate in gates:
        j = gate['index']
        peaks = context[j] if spec['pattern'] != 'NONE' else []
        allowed, reason = decision(peaks, spec['pattern'])
        need(all(p['confirmed_ms'] < block['candles'][j]['t'] for p in peaks), 'Future peak used')
        if not allowed:
            out[j]['direction'] = 0
        reports.append(dict(index=j, base_gate=gate, pattern_allowed=allowed,
                            reason=reason, peaks=peaks, final_direction=out[j]['direction']))
    return out, reports
