# Two confirmed peaks: frozen paper-only comparison

Refs Issue #1, predeclared comment5777362908. No website work or live orders.
Four definitions; all fixed A44 and saved September data. This is development
research, not a claim of future income or fresh unseen validation.

## Definitions

- BASE030: unchanged V030_R30.
- BASE035: unchanged V035_R30.
- LOWER_HIGH (primary): V035_R30 plus the latest two confirmed15m highs have
  high2 < high1.
- Z_WEAKENING: V035_R30 plus high2 >= high1 and Z2 < Z1 - 1e-12.

A pivot high is strictly greater than EACH immediately adjacent bar's high.
Both adjacent bars must be fully closed. Equal/plateau highs are not pivots.
With i completed15m bars, only k <= i-2 is eligible. Centres must be in
[i-12,i-2], i.e. the last three hours. Take the two most recent, NOT the pair
that yields the best backtest. Do not retrospectively insert a new pivot.

For each pivot k, Z=(high[k]-SMA20)/population_std20, using the20 closes ending
at k, not the confirmation bar. Both pivots need20 closes and nonzero SD.
An absent pair or undefined Z blocks the structure candidates, not the baselines.
The1e-12 Z tie threshold is fixed numerical handling, not a tuned market value.

All existing BB15 reentry, red lower-half close,3x modeled roundtrip cost room,
minimum1.5ATR/close admission, short-only and next1m modeled entry stay intact.
No new wait after the existing entry signal. No changes to stops,0.30% stop
floor/1% ceiling,1.8R conservative target cap,360min hold, sizes,fees,funding
proxy,cooldown,entry cap,or loss/drawdown halts. Hard risk rules remain first.

## Data and denominators

Parent: crossperiod-final-35683448454-1, artifact10676310582,
commit c65d35732046228bf250e5cedbd92577540e97c6, attempt1.
ZIP SHA256 ced6865fcd3281443e85fc9ca3595f7d8744d73514a870126aa800a1b522a58e.
The loader pins the parent's evidence-manifest hash, validates every result,
source identity and input chain, then reconstructs the September union.

A44 has44 dates,63360 minute bars in27 actual contiguous blocks. September
has5490 minute bars in one contiguous block. Each block has600 warmup minutes;
execution windows are47160 and4890 minutes. No excluded losing dates, invented
missing prices, cross-gap positions, or daily cash reset. Each contiguous block
is a separate virtual10USDC experiment. Never join those into one10USDC curve.
A44 net/270 is an average block return; September net/10 is period return.

4 definitions x28 blocks x2 costs x2 OHLC paths =448 cases, including224 exact
old baseline regressions and224 new structure cases. The price-path replicas
and repeated candidates are not additional independent market samples.

## Costs, evidence and safety

Original base cost: fee0.045% per side, full spread1bp, adverse slippage1bp per
side. Stress:0.09%,3bp,3bp. These remain assumptions, not an official fee change.
Fixed-trade stress and full higher-cost accounts are reported separately.
Keep zero-trade and loss cases. Compare both datasets, net, costs, sample size,
blocks and drawdown. No automatic promotion and no follow-up parameter sweep.

Source check, chronological pivot tests, backwards-search reference, exact
baseline comparisons and engine-external accounting audits are separate checks.
Fixtures do not prove market returns. OHLC paths do not reproduce L2, mark
triggers, oracle cashflows, queue, or latency, nor bound every possible fill.

Use PYTHONPATH with application,application/tests,longer,execution,reversal44,
alternatives44,mean44,direction44,refine44,entry44,threshold44,crossperiod,
structure44 after restoring unchanged source with bootstrap.py.
`python -m unittest discover -s structure44/tests -v`
`python structure44/run_structure.py --parent parent --out results`

Actions restores the fixed parent and runs one <=25min standard hosted job,
with read-only contents/actions permission, no persisted checkout credentials,
no installs, no market download, and all socket audit events denied during
research. Artifacts keep code, parent, results and failure logs for7 days;
they are not permanent archives. No wallet, account API, order, schedule,
paid service, main change, merge, release or deployment.

Method inspiration only (not evidence for these exact ETH rules):
https://www.bollingerbands.com/bollinger-band-rules
