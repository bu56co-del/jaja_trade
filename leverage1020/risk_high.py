"""Explicit 5/10/20x paper sizing. Defaults and previous modules remain unchanged.

Margin diagnostics and exits inherit the reviewed 5x engine; only frozen size
and planned-risk budgets vary. No network or real orders.
"""
from dataclasses import dataclass, asdict, replace
from decimal import Decimal as D, ROUND_FLOOR
from paperlab.common import Config, ConfigError, DataError
from paperlab import engine as core
from risk5 import ExposureEngine as PriorExposureEngine

LEVELS = {'EXPOSURE5_WHATIF': 5, 'EXPOSURE10_WHATIF': 10, 'EXPOSURE20_WHATIF': 20}
MODES = tuple(LEVELS)


@dataclass(frozen=True)
class ResearchConfig(Config):
    experiment_mode: str = 'EXPOSURE5_WHATIF'

    def validate(self):
        if self.experiment_mode not in MODES:
            raise ConfigError('Unknown bounded research mode')
        expected_lev = LEVELS[self.experiment_mode]
        expected = dict(leverage_for_margin=expected_lev, target_notional='10.10',
                        max_notional_to_equity=str(expected_lev),
                        minimum_cash_reserve='0',
                        risk_fraction_per_trade=str(D('0.0125')*expected_lev))
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ConfigError('Configuration outside frozen 5/10/20x paper experiment')
        # All non-experiment fields must pass the original validator and retain
        # the original stop, target, account drawdown, cooldown, and halt policy.
        raw = asdict(self); raw.pop('experiment_mode')
        normal = dict(raw, leverage_for_margin=2, max_notional_to_equity='1.15',
                      minimum_cash_reserve='3.50', risk_fraction_per_trade='0.0125')
        Config(**normal).validate()
        standard = asdict(replace(Config(), max_hold_seconds=21600,
                                 taker_fee=self.taker_fee, adverse_slippage_bps=self.adverse_slippage_bps))
        if normal != standard:
            raise ConfigError('Unapproved change to baseline safeguards')
        return self


def configuration(mode, cost):
    if mode not in MODES: raise ConfigError('Unknown mode')
    changes = dict(experiment_mode=mode, max_hold_seconds=21600,
                   leverage_for_margin=LEVELS[mode],
                   taker_fee=cost['taker_fee'], adverse_slippage_bps=cost['adverse_slippage_bps'])
    changes.update(max_notional_to_equity=str(LEVELS[mode]), minimum_cash_reserve='0',
                   risk_fraction_per_trade=str(D('0.0125')*LEVELS[mode]))
    return ResearchConfig(**changes).validate()


def affordable_quantity(cash, quote, cfg):
    """Short only: margin + entry mark loss + entry fee + close fee reserve.

    q * [mark/cfg.leverage_for_margin + (mark-entry_fill) + fee*(entry_fill+cover_fill)] <= cash.
    A quote here is a synthetic OHLC observation, NOT historical L2/mark truth.
    """
    if not cash.is_finite() or cash <= 0: raise DataError('Nonpositive cash')
    entry = quote.bids[0][0] * (1-D(cfg.adverse_slippage_bps)/10000)
    cover = quote.asks[0][0] * (1+D(cfg.adverse_slippage_bps)/10000)
    unit = quote.mark/cfg.leverage_for_margin + max(D(0), quote.mark-entry) + (entry+cover)*D(cfg.taker_fee)
    step = D(1).scaleb(-quote.sz_decimals)
    return (cash/unit/step).to_integral_value(rounding=ROUND_FLOOR)*step


class ExposureEngine(PriorExposureEngine):
    def open_position(self, quote, signal):
        cfg = self.cfg; at = quote.observed_ms; quote.validate(at, cfg)
        if self.position or signal.direction != -1: return False
        if self.state['halt_reason'] or self.state['pending_funding']:
            self.decision(at, 'BLOCKED', 'Halted or unresolved funding'); return False
        if signal.bar_ms >= quote.book_ms or at-signal.bar_ms > 90000:
            self.decision(at, 'BAD_SIGNAL_TIME', 'Noncausal signal'); return False
        if quote.spread_bps > D(cfg.max_spread_bps):
            self.decision(at, 'WIDE_SPREAD', 'Spread cap'); return False
        cash = self.cash; qty = affordable_quantity(cash, quote, cfg)
        if qty <= 0:
            self.decision(at, 'SIZE_ZERO', 'No affordable lot'); return False
        try: fill = core.market_fill(quote, -1, qty, cfg)
        except DataError:
            self.decision(at, 'ENTRY_DEPTH', 'No modeled depth'); return False
        n = fill.price*qty; margin = qty*quote.mark/cfg.leverage_for_margin
        stop = max(D(cfg.stop_floor_fraction), signal.atr/signal.close*D(cfg.atr_multiplier))
        friction = 2*D(cfg.taker_fee)+2*D(cfg.adverse_slippage_bps)/10000+quote.spread_bps/10000
        loss = n*(stop+friction)
        cover = core.market_fill(quote, 1, qty, cfg)
        mark_loss = max(D(0), qty*(quote.mark-fill.price))
        reasons = []
        for fail, code in (
            (n < D(cfg.min_open_notional), 'MIN_NOTIONAL'),
            (fill.impact_bps > D(cfg.max_entry_impact_bps), 'ENTRY_IMPACT'),
            (n > cash*D(cfg.max_notional_to_equity), 'NOTIONAL_CAP'),
            (margin+fill.fee+D(cfg.minimum_cash_reserve)>cash, 'MARGIN_RESERVE'),
            (margin+fill.fee+mark_loss+cover.fee>cash, 'AFFORDABILITY'),
            (stop > D(cfg.stop_ceiling_fraction), 'VOLATILITY'),
            (loss > cash*D(cfg.risk_fraction_per_trade), 'RISK_MINIMUM_CONFLICT'),
            (stop*D(cfg.reward_to_risk)<3*friction, 'COST_GATE')):
            if fail: reasons.append(code)
        item = dict(time=at, cash=str(cash), quantity=str(qty), entry=str(fill.price),
            notional=str(n), initial_margin=str(margin), planned_loss=str(loss),
            loss_fraction=str(loss/cash), pre_equity_exposure=str(qty*quote.mark/cash),
            post_entry_mark_exposure=str(qty*quote.mark/(cash-fill.fee-mark_loss)),
            fee_reserve=str(cover.fee), rejected_by=reasons)
        self.attempts.append(item)
        if reasons:
            self.decision(at, reasons[0], 'Requested '+str(cfg.leverage_for_margin)+'x blocked: '+','.join(reasons)); return False
        # Same transaction fields as core.Engine; only sizing and budgets differ.
        t = dict(id=len(self.state['trades'])+1, opened_ms=at, closed_ms=None, direction=-1,
            qty=str(qty), entry=str(fill.price), exit=None, stop=str(fill.price*(1+stop)),
            target=str(fill.price*(1-stop*D(cfg.reward_to_risk))), entry_fee=str(fill.fee),
            exit_fee='0', gross_pnl='0', entry_adverse_cost=str(fill.adverse_cost), exit_adverse_cost='0',
            entry_raw_vwap=str(fill.raw_vwap), exit_raw_vwap=None, planned_loss=str(loss),
            initial_margin_model=str(margin), signal_bar_ms=signal.bar_ms, signal_atr=str(signal.atr),
            signal_close=str(signal.close), open_reason=signal.description, close_reason='', funding_events=[],
            entry_reference_mid=str(quote.mid), signal_i=self.holder['i'], pattern_trace=self.holder['choice']['trace'])
        self.state['trades'].append(t)
        self.decision(at, 'PAPER_OPEN', 'Paper-only enlarged exposure')
        return True

