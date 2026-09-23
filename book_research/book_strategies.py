"""Predeclared book-inspired hypotheses, NOT the authors' original systems.

Only closed 1m/5m bars; no networking, account access or order APIs here.
The original engine, replay, fees and account protections remain unchanged.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D

from paperlab import backtest as bt, engine as core
import strategies as previous


def candidates():
    items = []
    for length in (60, 100):
        for threshold in (5, 10):
            items.append(dict(id=f'CONNORS_ADAPT_SMA{length}_RSI2_{threshold}',
                              family='book_rsi', fast=9, slow=21,
                              trend_length=length, threshold=threshold))
    for fast, slow in ((3, 8), (4, 12)):
        for threshold in (20, 30):
            items.append(dict(id=f'ELDER_ADAPT_EMA{fast}_{slow}_K5_{threshold}',
                              family='book_elder', fast=9, slow=21,
                              higher_fast=fast, higher_slow=slow, threshold=threshold))
    return items


def controls():
    names = ('CONTROL_EMA9_21', 'POST_SELECTED_EMA16_36', 'EFF_16_36_20_0.20')
    return [s for name in names for s in previous.candidates() if s['id'] == name]


def complete_five(bars):
    """Discard incomplete clock-aligned groups; never synthesize missing bars."""
    groups = {}
    for b in bars:
        groups.setdefault(b['t'] // 300000, []).append(b)
    out = []
    for key, group in sorted(groups.items()):
        if (len(group) != 5 or [b['t'] for b in group] !=
                [key * 300000 + i * 60000 for i in range(5)] or
                any(b['T'] != b['t'] + 59999 for b in group)):
            continue
        out.append(dict(t=group[0]['t'], T=group[-1]['T'],
                        c=D(group[-1]['c'])))
    return out


def rsi2(values):
    """Wilder RSI, two-change arithmetic seed. Flat=50, no losses=100."""
    if len(values) < 3:
        return None
    gains = [max(D(0), b-a) for a, b in zip(values, values[1:])]
    losses = [max(D(0), a-b) for a, b in zip(values, values[1:])]
    g, loss = sum(gains[:2], D(0))/2, sum(losses[:2], D(0))/2
    for up, down in zip(gains[2:], losses[2:]):
        g, loss = (g+up)/2, (loss+down)/2
    return D(50) if g+loss == 0 else D(100)*g/(g+loss)


def stochastic5(bars):
    if len(bars) < 5:
        return None
    hi = max(D(b['h']) for b in bars[-5:])
    lo = min(D(b['l']) for b in bars[-5:])
    return D(50) if hi == lo else D(100)*(D(bars[-1]['c'])-lo)/(hi-lo)


def features(bars, spec):
    if len(bars) != 120:
        raise ValueError('Book hypotheses require exactly 120 prior closed 1m bars')
    higher = complete_five(bars)
    values = [g['c'] for g in higher]
    close = D(bars[-1]['c'])
    fresh5 = bool(higher and higher[-1]['T'] == bars[-1]['T'])
    if spec['family'] == 'book_rsi':
        n = spec['trend_length']
        return dict(fresh5=fresh5, close=close,
                    mean=sum((D(b['c']) for b in bars[-n:]), D(0))/n,
                    rsi=rsi2(values), mean5=sum(values[-5:], D(0))/5 if len(values)>=5 else None,
                    last5=values[-1] if values else None)
    if len(values) < spec['higher_slow']+2:
        trend = 0
    else:
        f = core.ema(values, spec['higher_fast'])
        s = core.ema(values, spec['higher_slow'])
        trend = 1 if f[-1]>s[-1] and f[-1]>f[-2] else -1 if f[-1]<s[-1] and f[-1]<f[-2] else 0
    prior_k = [stochastic5(bars[:-i]) for i in (1, 2, 3)]
    return dict(trend=trend, k=stochastic5(bars), prior_k=prior_k,
                above=close>D(bars[-2]['h']), below=close<D(bars[-2]['l']))


def entry(f, spec):
    threshold = D(spec['threshold'])
    if spec['family'] == 'book_rsi':
        if not f['fresh5'] or f['rsi'] is None or f['mean5'] is None:
            return 0
        if f['close']>f['mean'] and f['last5']<f['mean5'] and f['rsi']<=threshold:
            return 1
        if f['close']<f['mean'] and f['last5']>f['mean5'] and f['rsi']>=100-threshold:
            return -1
        return 0
    if f['trend']==1 and f['above'] and any(k is not None and k<=threshold for k in f['prior_k']):
        return 1
    if f['trend']==-1 and f['below'] and any(k is not None and k>=100-threshold for k in f['prior_k']):
        return -1
    return 0


def exit_signal(f, direction, spec):
    if spec['family']=='book_rsi':
        if not f['fresh5'] or f['rsi'] is None or f['mean5'] is None:
            return False
        return (f['rsi']>70 or f['last5']>f['mean5']) if direction==1 else (f['rsi']<30 or f['last5']<f['mean5'])
    return f['trend']==-direction or (f['k']>=80 if direction==1 else f['k']<=20)


@contextmanager
def installed(spec, diagnostic):
    """Sequential process-local hook, always restored; risk checks run first."""
    original_engine, original_signal = bt.DirectionEngine, core.strategy_signal
    holder = {}
    class BookEngine(original_engine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            holder['engine'] = self
        def decision(self, at, code, message):
            diagnostic[code] += 1
            return super().decision(at, code, message)
        def close_position(self, quote, reason):
            if reason == 'OPPOSITE_EMA_CROSS':
                reason = 'BOOK_SIGNAL_EXIT'
            return super().close_position(quote, reason)
    def signal(bars, cfg):
        raw = original_signal(bars, cfg)  # original ATR; unchanged risk distances
        f = features(bars, spec)
        position = holder['engine'].position
        if position:
            direction = -position['direction'] if exit_signal(f, position['direction'], spec) else 0
        else:
            direction = entry(f, spec)
        return replace(raw, direction=direction, description=spec['id'])
    try:
        bt.DirectionEngine, core.strategy_signal = BookEngine, signal
        yield
    finally:
        bt.DirectionEngine, core.strategy_signal = original_engine, original_signal


def replay(data, lo, hi, spec, cost, path):
    if spec in controls():
        return previous.replay(data, lo, hi, spec, cost, path)
    if spec not in candidates():
        raise ValueError('Unknown or altered book candidate')
    diagnostic = Counter()
    with installed(spec, diagnostic):
        row = bt.replay(data, lo, hi, 9, 21, 'both', cost, path)
    row.update(candidate=spec['id'], strategy_spec=spec,
               decision_counts=dict(diagnostic), interpretation='BOOK_INSPIRED_ADAPTATION_NOT_ORIGINAL')
    return row
