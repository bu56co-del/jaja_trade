"""Net win-rate and cost sensitivity. No claim of future probabilities."""
from collections import defaultdict
from decimal import Decimal as D
import math
import random


def net(trade):
    return (D(trade['gross_pnl'])-D(trade['entry_fee'])-D(trade['exit_fee'])
            +sum((D(f['amount']) for f in trade['funding_events']), D(0)))


def wilson(wins, n, z=1.959963984540054):
    if not isinstance(n, int) or not isinstance(wins, int) or not 0 <= wins <= n:
        raise ValueError('Invalid counts')
    if not n:
        return None
    p = wins/n
    den = 1+z*z/n
    center = (p+z*z/(2*n))/den
    width = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0.0, center-width), min(1.0, center+width)]


def metrics(trades, start_ms=None, end_ms=None):
    vals = [net(t) for t in trades]
    winners, losers = [v for v in vals if v > 0], [v for v in vals if v < 0]
    n, wins = len(vals), len(winners)
    total = sum(vals, D(0))
    groups = defaultdict(list)
    for t, v in zip(trades, vals):
        groups[t['closed_ms']//86400000].append(v)
    if start_ms is not None and end_ms is not None:
        for day in range(start_ms//86400000, end_ms//86400000+1):
            groups[day]
    interval = None
    if len(groups) >= 3 and n:
        rng = random.Random(190919)
        blocks, samples = list(groups.values()), []
        for _ in range(1000):
            sample = [v for block in rng.choices(blocks, k=len(blocks)) for v in block]
            if sample:
                samples.append(sum(v > 0 for v in sample)/len(sample))
        if samples:
            samples.sort()
            interval = [samples[int((len(samples)-1)*p)] for p in (.025, .975)]
    return dict(wins=wins, losses=len(losers), flat=n-wins-len(losers), closed_trades=n,
                net_usdc=str(total), net_win_rate=None if not n else wins/n,
                return_pct=str(total/10*100), return_ge_5pct=total >= D('0.5'),
                mean_net=None if not n else str(total/n),
                mean_win=None if not wins else str(sum(winners)/wins),
                mean_loss=None if not losers else str(sum(losers)/len(losers)),
                profit_factor=None if not losers else str(sum(winners, D(0))/-sum(losers)),
                wilson95_iid=wilson(wins, n),
                wilson_covers_50=None if not n else wilson(wins, n)[0] <= .50 <= wilson(wins, n)[1],
                wilson_covers_55=None if not n else wilson(wins, n)[0] <= .55 <= wilson(wins, n)[1],
                day_block_bootstrap95=interval, calendar_days=len(groups),
                active_exit_days=sum(bool(v) for v in groups.values()),
                uncertainty_note='Wilson assumes independent Bernoulli trials, is unadjusted for selection; '
                    'day bootstrap is exploratory, especially with few days. Neither proves a durable win rate.',
                observed_55=bool(n and wins/n >= .55 and total > 0),
                observed_60=bool(n and wins/n >= .60 and total > 0))


def fixed_trade_stress(trades):
    """Same quantity, times, funding; change spread, slippage and fees only.

    This counterfactual does NOT rerun gates, triggers, liquidation or sizing.
    """
    out = []
    for trade in trades:
        t, d, q = dict(trade), trade['direction'], D(trade['qty'])
        entrymid = D(trade['entry_raw_vwap'])/(1+D(d)*D('0.00005'))
        exitmid = D(trade['exit_raw_vwap'])/(1-D(d)*D('0.00005'))
        entry = entrymid*(1+D(d)*D('0.00015'))*(1+D(d)*D('0.0003'))
        exit_ = exitmid*(1-D(d)*D('0.00015'))*(1-D(d)*D('0.0003'))
        t.update(entry=str(entry), exit=str(exit_), gross_pnl=str(d*q*(exit_-entry)),
                 entry_fee=str(q*entry*D('0.0009')), exit_fee=str(q*exit_*D('0.0009')))
        out.append(t)
    return out
