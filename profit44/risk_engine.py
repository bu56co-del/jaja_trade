"""Same paper accounting and safety gates, explicit alternative stop geometry."""
from decimal import Decimal as D, ROUND_CEILING
from paperlab import engine as core
from paperlab.common import DataError
import paired
from signals_profit import risk_plan


class ProfitEngine(paired.ExecutionEngine):
    def open_position(self, quote, signal):
        at = quote.observed_ms
        quote.validate(at, self.cfg)
        if self.position or signal.direction not in (-1,1):
            return False
        if self.state['halt_reason'] or self.state['pending_funding']:
            self.decision(at,'BLOCKED','Halted or missing funding')
            return False
        if signal.bar_ms >= quote.book_ms or at-signal.bar_ms > 90000:
            self.decision(at,'BAD_SIGNAL_TIME','Only recently completed bars')
            return False
        if quote.spread_bps > D(self.cfg.max_spread_bps):
            self.decision(at,'WIDE_SPREAD','Spread rejected')
            return False
        cash = self.cash
        direction = signal.direction
        reference = quote.asks[0][0] if direction==1 else quote.bids[0][0]
        step = D(1).scaleb(-quote.sz_decimals)
        qty = (D(self.cfg.target_notional)/reference/step).to_integral_value(rounding=ROUND_CEILING)*step
        try:
            fill = core.market_fill(quote,direction,qty,self.cfg)
        except DataError:
            self.decision(at,'ENTRY_DEPTH','Insufficient model depth')
            return False
        notional = fill.price*qty
        if notional < D(self.cfg.min_open_notional):
            self.decision(at,'MIN_NOTIONAL','Minimum opening notional')
            return False
        if fill.impact_bps > D(self.cfg.max_entry_impact_bps):
            self.decision(at,'ENTRY_IMPACT','Impact rejected')
            return False
        if notional > cash*D(self.cfg.max_notional_to_equity):
            self.decision(at,'NOTIONAL_CAP','Notional cap')
            return False
        margin = qty*quote.mark/self.cfg.leverage_for_margin
        if margin+fill.fee+D(self.cfg.minimum_cash_reserve)>cash:
            self.decision(at,'MARGIN_RESERVE','Reserve protected')
            return False
        trace = self.holder['choice']['trace']
        friction = 2*D(self.cfg.taker_fee)+2*D(self.cfg.adverse_slippage_bps)/10000+quote.spread_bps/10000
        plan = risk_plan(fill.price,signal,trace,self.cfg,friction,self.spec)
        stop = plan['stop_fraction']
        if self.spec['stop']=='STRUCTURE' and plan['structure_fraction']<=0:
            self.decision(at,'STRUCTURE_INVALID','Structure on wrong side of fill')
            return False
        if stop > D(self.cfg.stop_ceiling_fraction):
            self.decision(at,'VOLATILITY','Stop exceeds unchanged ceiling')
            return False
        loss = notional*(stop+friction)
        if loss > cash*D(self.cfg.risk_fraction_per_trade):
            self.decision(at,'RISK_MINIMUM_CONFLICT','Minimum order exceeds unchanged risk budget')
            return False
        if plan['target_fraction'] < friction*3:
            self.decision(at,'COST_GATE','Target smaller than cost buffer')
            return False
        if not plan['room_ok']:
            self.decision(at,'IMPULSE_ROOM','Insufficient room to previously observed extreme')
            return False
        trade = dict(id=len(self.state['trades'])+1,opened_ms=at,closed_ms=None,
                     direction=direction,qty=str(qty),entry=str(fill.price),exit=None,
                     stop=str(fill.price*(1-direction*stop)),
                     target=str(fill.price*(1+direction*plan['target_fraction'])),
                     entry_fee=str(fill.fee),exit_fee='0',gross_pnl='0',
                     entry_adverse_cost=str(fill.adverse_cost),exit_adverse_cost='0',
                     entry_raw_vwap=str(fill.raw_vwap),exit_raw_vwap=None,
                     planned_loss=str(loss),initial_margin_model=str(margin),
                     signal_bar_ms=signal.bar_ms,signal_atr=str(signal.atr),signal_close=str(signal.close),
                     open_reason=signal.description,close_reason='',funding_events=[],
                     entry_reference_mid=str(quote.mid),signal_i=self.holder['i'],pattern_trace=trace,
                     risk_plan={k:v if isinstance(v,bool) else str(v) for k,v in plan.items()})
        self.state['trades'].append(trade)
        self.decision(at,'PAPER_OPEN',self.spec['id'])
        return True
