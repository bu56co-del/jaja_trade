"""Issue #1: finite, predeclared educational strategies; no order API."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
from paperlab import engine as core
from paperlab import backtest as bt


def candidates():
    out = [dict(id='CONTROL_EMA9_21', family='control', fast=9, slow=21, round=0),
           dict(id='POST_SELECTED_EMA16_36', family='control', fast=16, slow=36, round=0)]
    for fast, slow in ((9, 21), (16, 36)):
        for hf, hs in ((3, 8), (4, 12)):
            out.append(dict(id=f'TREND_{fast}_{slow}_5m_{hf}_{hs}', family='trend',
                            fast=fast, slow=slow, higher_fast=hf, higher_slow=hs, round=1))
        for threshold in ('0.20', '0.35'):
            out.append(dict(id=f'EFF_{fast}_{slow}_20_{threshold}', family='efficiency',
                            fast=fast, slow=slow, length=20, threshold=threshold, round=1))
    for length in (20, 40):
        for threshold in ('0.20', '0.35'):
            out.append(dict(id=f'BREAK_{length}_{threshold}', family='breakout',
                            fast=9, slow=21, length=length, efficiency_length=20,
                            threshold=threshold, round=2))
    return out


def efficiency(bars, length):
    closes = [D(b['c']) for b in bars[-length-1:]]
    if len(closes) != length+1:
        return D(0)
    distance = sum((abs(b-a) for a, b in zip(closes, closes[1:])), D(0))
    return abs(closes[-1]-closes[0])/distance if distance else D(0)


def five_minute_closes(bars):
    groups = {}
    for bar in bars:
        key = bar['t']//300000
        groups.setdefault(key, []).append(bar)
    return [D(g[-1]['c']) for key, g in sorted(groups.items())
            if len(g) == 5 and [b['t'] for b in g] == [key*300000+i*60000 for i in range(5)]
            and g[-1]['T'] == key*300000+299999]


def eligible(bars, direction, spec):
    family = spec['family']
    if family == 'control':
        return True
    if family in ('efficiency', 'breakout'):
        return efficiency(bars, spec.get('efficiency_length', spec['length'])) >= D(spec['threshold'])
    values = five_minute_closes(bars)
    if len(values) < spec['higher_slow']+2:
        return False
    fast = core.ema(values, spec['higher_fast'])
    slow = core.ema(values, spec['higher_slow'])
    return direction*(fast[-1]-slow[-1]) > 0 and direction*(fast[-1]-fast[-2]) > 0


def breakout_direction(bars, length):
    if len(bars) < length+2:
        return 0
    c, prev = D(bars[-1]['c']), D(bars[-2]['c'])
    high = max(D(b['h']) for b in bars[-length-1:-1])
    low = min(D(b['l']) for b in bars[-length-1:-1])
    prevhigh = max(D(b['h']) for b in bars[-length-2:-2])
    prevlow = min(D(b['l']) for b in bars[-length-2:-2])
    if c > high and prev <= prevhigh:
        return 1
    if c < low and prev >= prevlow:
        return -1
    return 0


@contextmanager
def installed(spec):
    """Process-local, sequential hook; always restore original classes/functions."""
    old_engine, old_signal = bt.DirectionEngine, core.strategy_signal

    class FilteredEngine(old_engine):
        def tick(self, quote, candles=None):
            if candles is not None:
                self.research_bars = candles
            return super().tick(quote, candles)

        def open_position(self, quote, signal):
            if not eligible(self.research_bars, signal.direction, spec):
                self.decision(quote.observed_ms, 'RESEARCH_FILTER', spec['id'])
                return False
            return super().open_position(quote, signal)

    def signal(bars, cfg):
        raw = old_signal(bars, cfg)
        if spec['family'] == 'breakout':
            return replace(raw, direction=breakout_direction(bars, spec['length']), description=spec['id'])
        return raw

    try:
        bt.DirectionEngine, core.strategy_signal = FilteredEngine, signal
        yield
    finally:
        bt.DirectionEngine, core.strategy_signal = old_engine, old_signal


def replay(data, lo, hi, spec, cost, path):
    if spec not in candidates():
        raise ValueError('Strategy not in frozen candidate grid')
    with installed(spec):
        row = bt.replay(data, lo, hi, spec['fast'], spec['slow'], 'both', cost, path)
    row['candidate'], row['strategy_spec'] = spec['id'], spec
    return row
