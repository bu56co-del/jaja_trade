"""Bounded, offline-only 5x position-size experiment. Never changes default Config.

EXPOSURE5_WHATIF is deliberately NOT the old risk policy. It has explicit
larger size/loss budgets; all time/stop/account halt rules remain unchanged.
"""
from dataclasses import dataclass, asdict, replace
from decimal import Decimal as D, ROUND_FLOOR
from collections import Counter
from paperlab.common import Config, ConfigError, DataError
from paperlab import engine as core
from run_mean import MeanEngine

MODES = ('BASE', 'LEVERAGE5_SAME_SIZE', 'REQUEST5_ORIGINAL_LIMITS', 'EXPOSURE5_WHATIF')


@dataclass(frozen=True)
class ResearchConfig(Config):
    experiment_mode: str = 'BASE'

    def validate(self):
        if self.experiment_mode not in MODES:
            raise ConfigError('Unknown bounded research mode')
        expected_lev = 2 if self.experiment_mode == 'BASE' else 5
        enlarged = self.experiment_mode == 'EXPOSURE5_WHATIF'
        expected = dict(leverage_for_margin=expected_lev, target_notional='10.10',
                        max_notional_to_equity='5' if enlarged else '1.15',
                        minimum_cash_reserve='0' if enlarged else '3.50',
                        risk_fraction_per_trade='0.0625' if enlarged else '0.0125')
        if any(getattr(self, k) != v for k, v in expected.items()):
            raise ConfigError('Configuration outside frozen 5x experiment')
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
                   leverage_for_margin=2 if mode == 'BASE' else 5,
                   taker_fee=cost['taker_fee'], adverse_slippage_bps=cost['adverse_slippage_bps'])
    if mode == 'EXPOSURE5_WHATIF':
        changes.update(max_notional_to_equity='5', minimum_cash_reserve='0', risk_fraction_per_trade='0.0625')
    return ResearchConfig(**changes).validate()


def affordable_quantity(cash, quote, cfg):
    """Short only: margin + entry mark loss + entry fee + close fee reserve.

    q * [mark/5 + (mark-entry_fill) + fee*(entry_fill+cover_fill)] <= cash.
    A quote here is a synthetic OHLC observation, NOT historical L2/mark truth.
    """
    if not cash.is_finite() or cash <= 0: raise DataError('Nonpositive cash')
    entry = quote.bids[0][0] * (1-D(cfg.adverse_slippage_bps)/10000)
    cover = quote.asks[0][0] * (1+D(cfg.adverse_slippage_bps)/10000)
    unit = quote.mark/5 + max(D(0), quote.mark-entry) + (entry+cover)*D(cfg.taker_fee)
    step = D(1).scaleb(-quote.sz_decimals)
    return (cash/unit/step).to_integral_value(rounding=ROUND_FLOOR)*step


class ExposureEngine(MeanEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attempts = []
        self.margin_checks = 0
        self.min_margin_buffer = None
        self.min_margin_ratio = None
        self.min_margin_observation = None
        self.maintenance_breaches = 0

    def margin_check(self, quote):
        p = self.position
        if p is None: return
        n = D(p['qty'])*quote.mark
        equity = D(self.valuation(quote)['mark_equity'])
        maintenance = n/(2*quote.asset_max_leverage)
        buffer = equity-maintenance
        ratio = equity/n
        self.margin_checks += 1
        if buffer <= 0: self.maintenance_breaches += 1
        if self.min_margin_buffer is None or buffer < self.min_margin_buffer:
            self.min_margin_buffer = buffer
            self.min_margin_observation = dict(time=quote.observed_ms, trade_id=p['id'],
                equity=str(equity), notional=str(n), maintenance=str(maintenance), buffer=str(buffer))
        if self.min_margin_ratio is None or ratio < self.min_margin_ratio:
            self.min_margin_ratio = ratio

    def tick(self, quote, candles=None):
        self.margin_check(quote)
        before = len(self.state['trades'])
        super().tick(quote, candles)
        if len(self.state['trades']) > before:
            self.margin_check(quote)

    def open_position(self, quote, signal):
        mode = self.cfg.experiment_mode
        if mode in ('BASE', 'LEVERAGE5_SAME_SIZE'):
            return super().open_position(quote, signal)
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
        n = fill.price*qty; margin = qty*quote.mark/5
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
            self.decision(at, reasons[0], 'Requested 5x blocked: '+','.join(reasons)); return False
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

    def margin_summary(self):
        return dict(observations=self.margin_checks, breaches=self.maintenance_breaches,
            minimum_buffer_usdc=str(self.min_margin_buffer) if self.min_margin_buffer is not None else None,
            minimum_equity_to_notional=str(self.min_margin_ratio) if self.min_margin_ratio is not None else None,
            minimum_observation=self.min_margin_observation,
            exact_exchange_liquidation='NOT_VERIFIED_NO_HISTORICAL_MARK_L2',
            mark_source='ONE_MINUTE_TRADE_OHLC_PROXY')
