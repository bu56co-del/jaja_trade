"""Five frozen exit-distance profiles; never change the original default Config."""
from dataclasses import dataclass, asdict
from decimal import Decimal as D
from paperlab.common import ConfigError
import risk_high

# Factors relative to original d=max(.003,1.5*ATR/close) and target=1.8*d.
PROFILES = {
    'BASE': ('1', '1'),
    'SL_HALF': ('0.5', '1'),
    'TP_HALF': ('1', '0.5'),
    'BOTH_HALF': ('0.5', '0.5'),
    'BOTH_QUARTER': ('0.25', '0.25'),
}
PRIMARY = 'BOTH_HALF'
EXIT_KEYS = ('stop_floor_fraction', 'stop_ceiling_fraction', 'atr_multiplier', 'reward_to_risk')


def exit_values(profile):
    if profile not in PROFILES:
        raise ConfigError('Unknown frozen exit profile')
    s, t = map(D, PROFILES[profile])
    return dict(stop_floor_fraction=str(D('.003')*s),
                stop_ceiling_fraction=str(D('.01')*s),
                atr_multiplier=str(D('1.5')*s),
                reward_to_risk=str(D('1.8')*t/s))


@dataclass(frozen=True)
class TightConfig(risk_high.ResearchConfig):
    exit_profile: str = PRIMARY

    def validate(self):
        if self.exit_profile not in PROFILES or self.exit_profile == 'BASE':
            raise ConfigError('Use unchanged parent Config for BASE')
        cost = dict(taker_fee=self.taker_fee, adverse_slippage_bps=self.adverse_slippage_bps)
        expected = asdict(risk_high.configuration(self.experiment_mode, cost))
        expected.update(exit_values(self.exit_profile), exit_profile=self.exit_profile)
        if asdict(self) != expected:
            raise ConfigError('Change outside frozen exit-only experiment')
        # Parent validates all unmodified account policy fields. Exit ratios are
        # separately pinned, including TP_HALF's explicitly requested 0.9R.
        if not all(D(expected[k]).is_finite() and D(expected[k]) > 0 for k in EXIT_KEYS):
            raise ConfigError('Invalid exit distance')
        return self


def configuration(mode, profile, cost):
    values = exit_values(profile)
    parent = risk_high.configuration(mode, cost)
    if profile == 'BASE':
        return parent
    raw = asdict(parent)
    raw.update(values, exit_profile=profile)
    return TightConfig(**raw).validate()
