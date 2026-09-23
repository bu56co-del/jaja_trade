"""15m signals, separately sampled execution. Offline paper research only."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, replace
from decimal import Decimal as D

from paperlab import engine as core
from paperlab.backtest import assumed_quote, FundingPrices, COSTS, PATHS
from paperlab.common import Config
import model as native
from market_data import MINUTE, HOUR, INTERVALS, need

QUARTER = 15 * MINUTE
MODES = ('COARSE_15M', 'FINE_1M', 'FINE_1M_TP_CAP')
SPECS = (
    dict(id='CONFIRM_90', max_minutes=90, exit='confirm'),
    dict(id='CONFIRM_360', max_minutes=360, exit='confirm'),
    dict(id='FAILURE_360', max_minutes=360, exit='failure'),
)
PRIMARY = ('CONFIRM_360', 'FINE_1M_TP_CAP')


def features(bars):
    result = native.features(bars)
    for i, f in result.items():
        prior = bars[i-21:i-1]
        f['upper'] = max(D(b['h']) for b in prior)
        f['lower'] = min(D(b['l']) for b in prior)
        f['last_two_closes'] = [D(b['c']) for b in bars[i-2:i]]
        f['last_two_ends'] = [b['T'] for b in bars[i-2:i]]
    return result


def failed_breakout(f, trade):
    # Two completed bars AFTER entry; the breakout reference never rolls forward.
    if min(f['last_two_ends']) <= trade['opened_ms']:
        return False
    d = trade['direction']
    return all(d * (p-D(trade['breakout_boundary'])) < -D('.25')*D(trade['signal_atr'])
               for p in f['last_two_closes'])


def wants_exit(f, spec, trade):
    if spec['exit'] == 'failure':
        return failed_breakout(f, trade)
    return f['confirm_long'] if trade['direction'] == 1 else f['confirm_short']


def capped_price(price, trade, mode):
    if mode != 'FINE_1M_TP_CAP':
        return price
    target = D(trade['target'])
    return min(price, target) if trade['direction'] == 1 else max(price, target)


class ExecutionEngine(core.Engine):
    def __init__(self, cfg, state, spec, mode, holder):
        super().__init__(cfg, state)
        self.spec, self.mode, self.holder = spec, mode, holder
        self.counts = Counter()

    def decision(self, at, code, message):
        self.counts[code] += 1
        return super().decision(at, code, message)

    def open_position(self, quote, signal):
        opened = super().open_position(quote, signal)
        if opened:
            f = self.holder['f']
            self.position.update(breakout_boundary=str(f['upper'] if signal.direction == 1 else f['lower']),
                entry_reference_mid=str(quote.mid), entry_15m_index=self.holder['i'])
        return opened

    def valuation(self, quote):
        value = super().valuation(quote)
        t = self.position
        if t and quote is not None and self.mode == 'FINE_1M_TP_CAP':
            fill = core.market_fill(quote, -t['direction'], D(t['qty']), self.cfg)
            px = capped_price(fill.price, t, self.mode)
            q = D(t['qty'])
            value['net_equity'] = str(self.cash + t['direction']*q*(px-D(t['entry']))
                                      - q*px*D(self.cfg.taker_fee))
            value['estimated_exit_fee'] = str(q*px*D(self.cfg.taker_fee))
        return value

    def close_position(self, quote, reason):
        t = self.position
        if not t:
            return False
        if reason == 'OPPOSITE_EMA_CROSS':
            reason = 'BREAKOUT_FAILURE_CONFIRMED' if self.spec['exit']=='failure' else 'CONFIRMED_TREND_REVERSAL'
        original = core.market_fill
        observed = original(quote, -t['direction'], D(t['qty']), self.cfg)
        # Only target exits get the one-sided haircut. Stops are never improved.
        price = capped_price(observed.price, t, self.mode) if reason=='TARGET_OBSERVED_PRICE' else observed.price
        haircut = t['direction']*D(t['qty'])*(observed.price-price)
        if price != observed.price:
            def capped(qt, direction, qty, cfg):
                need(qt is quote and direction == -t['direction'] and qty == D(t['qty']), 'Unexpected cap fill')
                raw = price / (1+direction*D(cfg.adverse_slippage_bps)/10000)
                return core.Fill(price, raw, qty, qty*price*D(cfg.taker_fee),
                                 abs(price-raw)*qty, observed.impact_bps)
            core.market_fill = capped
        try:
            ok = super().close_position(quote, reason)
        finally:
            core.market_fill = original
        if ok:
            t.update(exit_reference_mid=str(quote.mid), observed_exit_price=str(observed.price),
                     target_cap_haircut_usdc=str(haircut))
        return ok


@contextmanager
def installed(holder, spec):
    original = core.strategy_signal
    def signal(bars, cfg):
        f = holder['f']; pos = holder['engine'].position
        need(bars[-1]['T'] == f['bar_ms'], 'Incorrect signal join')
        direction = -pos['direction'] if pos and wants_exit(f, spec, pos) else 0 if pos else f['break']
        return core.Signal(direction, f['bar_ms'], f['atr'], f['close'], spec['id'])
    try:
        core.strategy_signal = signal
        yield
    finally:
        core.strategy_signal = original


def summarize(trades, cost_name, mode):
    fee = D(COSTS[cost_name]['taker_fee'])
    half = D(COSTS[cost_name]['spread_bps'])/20000
    slip = D(COSTS[cost_name]['adverse_slippage_bps'])/10000
    net=[]; pure=[]; friction=[]; fees=[]; funding=[]; caps=[]; stress=[]
    for t in trades:
        d, q = t['direction'], D(t['qty'])
        e, x = D(t['entry_reference_mid']), D(t['exit_reference_mid'])
        ep, xp = D(t['entry']), D(t['exit'])
        f = sum((D(r['amount']) for r in t['funding_events']), D(0))
        raw_exit = x*(1-d*half)*(1-d*slip)
        pure.append(d*q*(x-e));friction.append(pure[-1]-d*q*(raw_exit-ep))
        caps.append(D(t['target_cap_haircut_usdc']))
        fees.append(q*(ep+xp)*fee);funding.append(f)
        net.append(d*q*(xp-ep)-fees[-1]+f)
        se=e*(1+d*D('.00015'))*(1+d*D('.0003'))
        sx=x*(1-d*D('.00015'))*(1-d*D('.0003'))
        if t['close_reason']=='TARGET_OBSERVED_PRICE':sx=capped_price(sx,t,mode)
        stress.append(d*q*(sx-se)-q*(se+sx)*D('.0009')+f)
    total=sum(net,D(0));n=len(net);w=sum(v>0 for v in net)
    wins=[v for v in net if v>0];losses=[v for v in net if v<0]
    return dict(trades=n,wins=w,losses=len(losses),ties=n-w-len(losses),
        net_win_rate=w/n if n else None, wilson95_iid=native.wilson(w,n),
        net_usdc=str(total),return_pct=str(total*10),mean_net_usdc=str(total/n) if n else None,
        mean_win_usdc=str(sum(wins)/len(wins)) if wins else None,
        mean_loss_usdc=str(sum(losses)/len(losses)) if losses else None,
        profit_factor=str(sum(wins)/-sum(losses)) if losses else None,
        price_only_usdc=str(sum(pure,D(0))),spread_slippage_usdc=str(sum(friction,D(0))),
        fees_usdc=str(sum(fees,D(0))),funding_usdc=str(sum(funding,D(0))),
        target_cap_haircut_usdc=str(sum(caps,D(0))),
        fixed_trades_stress_net_usdc=str(sum(stress,D(0))),
        fixed_trades_stress_win_rate=sum(v>0 for v in stress)/n if n else None,
        exit_reasons=dict(Counter(t['close_reason'] for t in trades)),
        mean_hold_minutes=sum((t['closed_ms']-t['opened_ms'])/MINUTE for t in trades)/n if n else None,
        observed55_and_positive=bool(n and w/n>=.55 and total>0),
        observed60_and_positive=bool(n and w/n>=.60 and total>0),return5=total>=D('.5'))


def replay(data, start, end, spec, mode, cost_name, path, fv):
    need(spec in SPECS and mode in MODES and cost_name in COSTS and path in PATHS,'Unknown frozen scenario')
    signals=data['15m']['candles'];dt=QUARTER if mode=='COARSE_15M' else MINUTE
    execution=data['15m'] if dt==QUARTER else data['1m']
    clock={b['t']:i for i,b in enumerate(signals)}
    need(start in clock and clock[start]>=120 and start%QUARTER==end%QUARTER==0,'Window alignment/warmup')
    xb=[b for b in execution['candles'] if start<=b['t']<end]
    need(len(xb)==(end-start)//dt and len(xb)>1,'Execution coverage')
    need(all(b['t']==start+j*dt and b['T']==b['t']+dt-1 for j,b in enumerate(xb)),'Execution gap/duplicate')
    cost=COSTS[cost_name]
    cfg=replace(Config(),taker_fee=cost['taker_fee'],adverse_slippage_bps=cost['adverse_slippage_bps'],
                max_hold_seconds=spec['max_minutes']*60,gap_halt_seconds=45 if dt==MINUTE else 945).validate()
    holder={}; eng=ExecutionEngine(cfg,core.new_state(cfg,start+2000,'PAIRED_OFFLINE_PAPER'),spec,mode,holder)
    holder['engine']=eng
    eng.ingest_funding([r for r in data['15m']['funding'] if start-HOUR<=r['time']<end])
    funding=FundingPrices(data['15m']) # held identical across modes, still NOT an oracle
    halt=None;quote=None
    with installed(holder,spec):
        for b in xb:
            for field,offset in native.nodes(dt,path):
                quote=assumed_quote(b['t']+offset,b[field],cost['spread_bps'],execution['metadata'])
                eng.reconcile_funding(funding,quote.observed_ms)
                bs=None
                if field=='o' and b['t']%QUARTER==0:
                    i=clock[b['t']];holder.update(i=i,f=fv[i]);bs=signals[i-120:i]
                eng.tick(quote,bs);eng.update_drawdown(quote)
                if eng.state['halt_reason'] and halt is None:halt=quote.observed_ms
            if halt and not eng.position:break
        if eng.position:
            eng.close_position(quote,'BACKTEST_WINDOW_END_ASSUMED_FILL');eng.update_drawdown(quote)
            if eng.state['halt_reason'] and halt is None:halt=quote.observed_ms
        eng.reconcile_funding(funding,quote.observed_ms)
    return dict(candidate=spec['id'],strategy_spec=spec,mode=mode,cost=cost_name,path=path,
        start_ms=start+2000,end_ms=end-2,last_observation_ms=quote.observed_ms,
        initial_usdc='10',ending_usdc=str(eng.cash),account_config=asdict(cfg),
        halt_reason=eng.state['halt_reason'],halt_ms=halt,
        halted_fraction=(end-2-halt)/(end-2-start-2000) if halt else 0,
        sampled_max_drawdown_pct=str(D(eng.state['max_drawdown_fraction'])*100),
        open_position=eng.position is not None,pending_funding=eng.state['pending_funding'],
        integrity_warnings=eng.state['integrity_warnings'],trades=eng.state['trades'],
        metrics=summarize(eng.state['trades'],cost_name,mode),decisions=dict(eng.counts),
        evidence='INSUFFICIENT_SAMPLE_RETROSPECTIVE_PAIRED_DIAGNOSTIC',research_gate55=False)


def shadow_signals(data,start,end,fv):
    """Forward price labels only, not trades, capital, win rate or strategy P&L."""
    by_time={b['t']:b for b in data['1m']['candles']}
    output=[]
    for i,f in fv.items():
        at=data['15m']['candles'][i]['t']
        if not(start<=at<end and f['break']):continue
        p=D(by_time[at]['o']);d=f['break'];labels={}
        for minutes in (90,360):
            finish=at+minutes*MINUTE
            if finish>=end:
                labels[str(minutes)]={'status':'RIGHT_CENSORED'};continue
            future=[by_time[t] for t in range(at,finish,MINUTE)]
            moved=d*(D(by_time[finish]['o'])/p-1)
            favorable=max(d*(D(b[k])/p-1) for b in future for k in 'hl')
            adverse=min(d*(D(b[k])/p-1) for b in future for k in 'hl')
            labels[str(minutes)]={'status':'OBSERVED_REFERENCE_ONLY',
                'signed_reference_return':str(moved),'favorable_excursion_fraction':str(max(D(0),favorable)),
                'adverse_excursion_fraction':str(min(D(0),adverse))}
        output.append({'signal_open_ms':at+2000,'signal_bar_ms':f['bar_ms'],'direction':d,
                       'reference_open':str(p),'labels':labels})
    return dict(mode='SHADOW_SIGNAL_DIAGNOSTIC_NOT_TRADES',account_return=None,win_rate=None,
        warning='Overlapping hypothetical price horizons, no sizing/fills/stops/costs/account or trading claims. '
                'First two seconds of OHLC are not resolved. Never add to main ledger or trade sample.',signals=output)
