"""Bounded fixed-risk sizing. Simulation only; never changes the default Config."""
from dataclasses import dataclass, asdict, replace
from decimal import Decimal as D, ROUND_FLOOR
from paperlab.common import Config, ConfigError, DataError
from paperlab import engine as core
from risk5 import ExposureEngine as MarginEngine

PROFILES=('BASE_B','RISK125','RISK125_TP_HALF')


def baseline(cost):
    return replace(Config(),max_hold_seconds=21600,taker_fee=cost['taker_fee'],
                   adverse_slippage_bps=cost['adverse_slippage_bps']).validate()


@dataclass(frozen=True)
class FixedConfig(Config):
    risk_profile: str = 'RISK125'

    def validate(self):
        if self.risk_profile not in PROFILES[1:]:
            raise ConfigError('Unknown fixed-risk profile')
        expected=asdict(baseline(dict(taker_fee=self.taker_fee,adverse_slippage_bps=self.adverse_slippage_bps)))
        expected.update(leverage_for_margin=5,max_notional_to_equity='2',
                        reward_to_risk='0.9' if self.risk_profile=='RISK125_TP_HALF' else '1.8',
                        risk_profile=self.risk_profile)
        if asdict(self)!=expected:
            raise ConfigError('Change outside the frozen fixed-risk experiment')
        return self


def configuration(profile,cost):
    if profile not in PROFILES:raise ConfigError('Unknown profile')
    old=baseline(cost)
    if profile=='BASE_B':return old
    values=asdict(old)
    values.update(leverage_for_margin=5,max_notional_to_equity='2',risk_profile=profile,
                  reward_to_risk='0.9' if profile=='RISK125_TP_HALF' else '1.8')
    return FixedConfig(**values).validate()


def size_plan(cash,peak,mark,entry,cover,stop,friction,fee,decimals):
    """Plan a short using only cash/peak/quote known before entry.

    Max 1.25% planned stop+friction; retain 0.10% cash headroom above
    the stricter 5% peak / initial-balance floor. Funding and gap losses
    are not guaranteed bounded. All caps are per ETH, then floor to lot.
    """
    vals=(cash,peak,mark,entry,cover,stop,friction,fee)
    if any(not x.is_finite() for x in vals) or min(vals)<=0 or peak<cash:
        raise DataError('Invalid sizing inputs')
    if isinstance(decimals,bool) or not isinstance(decimals,int) or not 0<=decimals<=8:
        raise DataError('Invalid lot precision')
    boundary=max(D('9.50'),peak*D('.95'))
    room=max(D(0),cash-boundary-cash*D('.001'))
    risk=min(cash*D('.0125'),room)
    markloss=max(D(0),mark-entry)
    entrycost=entry*fee+markloss
    limits={
        'RISK':risk/(entry*(stop+friction)),
        'POST_ENTRY_EXPOSURE':D(2)*cash/(mark+D(2)*entrycost),
        'CASH_RESERVE':max(D(0),cash-D('3.50'))/(mark/D(5)+markloss+fee*(entry+cover)),
    }
    raw=min(limits.values());step=D(1).scaleb(-decimals)
    qty=(raw/step).to_integral_value(rounding=ROUND_FLOOR)*step
    return qty,dict(risk_budget=str(risk),drawdown_boundary=str(boundary),
                    remaining_drawdown_budget=str(room),headroom=str(cash*D('.001')),
                    quantity_caps={k:str(v) for k,v in limits.items()},
                    binding_caps=[k for k,v in limits.items() if v==raw])


class FixedRiskEngine(MarginEngine):
    def open_position(self,quote,signal):
        cfg=self.cfg;at=quote.observed_ms;quote.validate(at,cfg)
        if self.position or signal.direction!=-1:return False
        if self.state['halt_reason'] or self.state['pending_funding']:
            self.decision(at,'BLOCKED','Halted or unresolved funding');return False
        if signal.bar_ms>=quote.book_ms or at-signal.bar_ms>90000:
            self.decision(at,'BAD_SIGNAL_TIME','Noncausal signal');return False
        if quote.spread_bps>D(cfg.max_spread_bps):
            self.decision(at,'WIDE_SPREAD','Spread cap');return False
        cash=self.cash;peak=max(cash,D(self.state['peak_equity']))
        fee=D(cfg.taker_fee);slip=D(cfg.adverse_slippage_bps)/10000
        ep=quote.bids[0][0]*(1-slip);cp=quote.asks[0][0]*(1+slip)
        stop=max(D(cfg.stop_floor_fraction),signal.atr/signal.close*D(cfg.atr_multiplier))
        friction=2*fee+2*slip+quote.spread_bps/10000
        qty,details=size_plan(cash,peak,quote.mark,ep,cp,stop,friction,fee,quote.sz_decimals)
        reasons=[]
        if qty<=0:reasons.append('SIZE_ZERO')
        if qty*ep<D(cfg.min_open_notional):reasons.append('MIN_NOTIONAL')
        if stop>D(cfg.stop_ceiling_fraction):reasons.append('VOLATILITY')
        if stop*D(cfg.reward_to_risk)<3*friction:reasons.append('COST_GATE')
        loss=qty*ep*(stop+friction);margin=qty*quote.mark/5
        post=cash-qty*(ep*fee+max(D(0),quote.mark-ep))
        item=dict(time=at,cash=str(cash),peak_equity=str(peak),quantity=str(qty),entry=str(ep),
                  notional=str(qty*ep),initial_margin=str(margin),planned_loss=str(loss),
                  loss_fraction=str(loss/cash),post_entry_mark_exposure=str(qty*quote.mark/post),
                  fee_reserve=str(qty*cp*fee),rejected_by=reasons,**details)
        self.attempts.append(item)
        if reasons:
            self.decision(at,reasons[0],'Fixed-risk refused: '+','.join(reasons));return False
        fill=core.market_fill(quote,-1,qty,cfg);cover=core.market_fill(quote,1,qty,cfg)
        # Quotes are explicitly the inherited one-level OHLC execution model.
        if fill.price!=ep or cover.price!=cp:raise DataError('Unexpected modeled depth')
        if (loss>D(details['risk_budget']) or qty*quote.mark>D(2)*post or
            margin+qty*max(D(0),quote.mark-ep)+fill.fee+cover.fee+D('3.50')>cash):
            raise DataError('Sizing exceeded frozen risk or reserve cap')
        if fill.impact_bps>D(cfg.max_entry_impact_bps):raise DataError('Modeled entry impact')
        t=dict(id=len(self.state['trades'])+1,opened_ms=at,closed_ms=None,direction=-1,
            qty=str(qty),entry=str(fill.price),exit=None,stop=str(ep*(1+stop)),
            target=str(ep*(1-stop*D(cfg.reward_to_risk))),entry_fee=str(fill.fee),
            exit_fee='0',gross_pnl='0',entry_adverse_cost=str(fill.adverse_cost),exit_adverse_cost='0',
            entry_raw_vwap=str(fill.raw_vwap),exit_raw_vwap=None,planned_loss=str(loss),
            initial_margin_model=str(margin),signal_bar_ms=signal.bar_ms,signal_atr=str(signal.atr),
            signal_close=str(signal.close),open_reason=signal.description,close_reason='',funding_events=[],
            entry_reference_mid=str(quote.mid),signal_i=self.holder['i'],pattern_trace=self.holder['choice']['trace'])
        self.state['trades'].append(t);self.decision(at,'PAPER_OPEN','Fixed-risk simulated position');return True
