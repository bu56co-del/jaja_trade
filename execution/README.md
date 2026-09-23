# Paired signal/execution research — Issue #1

This batch separates a **closed 15-minute trading signal** from a **one-minute
execution sampling clock**. It is an offline experiment, not a trading service.
No funds, accounts, orders, network clients, paid data, schedule, merge or deployment.

## Fixed inputs and question

Use saved public ETH mainnet artifacts of run `35502377024`, commit
`e5d8746973ee748a90b05e7aaa91c6287c334a3e`. Data and source hashes are pinned.
The paired window is **2026-09-18 00:00 through 2026-09-20 00:00 UTC exclusive**.
1m execution coverage is two days. 15m indicator warmup uses 120 prior 15m bars
from the already-saved longer history. This is already-viewed development data,
NOT new/unseen history or a forward trading record. No 49-day 1m history is claimed.

The question is whether the prior results depend on coarse execution, and whether
changing the holding limit or invalidation exit improves the same experiment.
No parameters are optimized; `CONFIRM_360 / FINE_1M_TP_CAP` is the predeclared primary.

## Strategies and clocks

All three use the previous batch's identical breakout+EMA9/21 trend/slope+ER20
>=0.20 entry. Signals come only from completed 15m candles at the next 15m
boundary +2 seconds. At other minute observations, only position risk is managed.
The initial startup signal is skipped, matching the original baseline.

- `CONFIRM_90`: original two-bar EMA reversal confirmation, maximum90min.
- `CONFIRM_360`: same, maximum360min.
- `FAILURE_360`: max360min; replace the discretionary confirmation exit with
  failed-breakout confirmation. Freeze the prior20-bar upper/lower boundary at
  entry (exclude the breakout bar). Both latest15m closes must have ended after
  entry and be back inside the fixed boundary by **strictly more than0.25 of the
  entry ATR**. Exit next15m boundary. The boundary never trails/rolls forward.

Stops, targets, account drawdown, maintenance and time exits have priority over
strategy exits. None of the initial stops, risk1.25%, 10USDC starting balance,
one-position limit, 6entries/24h, 15min cooldown, 3loss halt or5% drawdown halt is
relaxed. The original engine and all older source files are unchanged.

Each strategy is tested with two costs, two paths and three execution modes:
**3 x 2 x 2 x 3 =36 complete, independent-account scenarios**.

- `COARSE_15M`: original four assumed observations within each15m bar.
- `FINE_1M`: same signal, four observations within EACH native1m bar.
- `FINE_1M_TP_CAP`: same fine replay, but favorable price beyond the configured
  take-profit target is capped at the target on TARGET exits. Exit fees, capital,
  loss streaks and future decisions are recalculated using that capped fill.
  Stop losses are not improved. Liquidation-value estimates also cap favorable
  target surplus to avoid a phantom high-water mark in this sensitivity mode.

At each native bar, observations are open+2s, first extreme at35% of duration,
second extreme at2/3, close at duration-2ms. OHLC and OLHC are assumptions, not
true tick paths or guaranteed best/worst bounds. No intraminute interpolation
or exchange matching is claimed. No limit-order fill guarantee is implied.

The target-cap case is deliberately one-sided model sensitivity, not a statement
that real TP execution equals its target. Hyperliquid exchange TP/SL uses MARK
price triggers. Our original engine instead compares a model executable-price
proxy; without historical mark and L2 we cannot call this an exchange-order replay.

## Funding and cost isolation

All modes use the SAME historical funding rates and preceding15m close proxy,
so a change in funding price source does not confound execution timing. Holding
period changes can still change the number of funding payments. It is not oracle.

Each trade saves original reference mids, observed exit before haircut, actual
modeled exit, target-cap deduction, fixed breakout anchor, signal time and15m index.
Net = direction*qty*(exit-entry) - entry_fee - exit_fee + funding.
Cost report = pure reference-price P&L - assumed spread/slippage - cap deduction
- explicit fees + funding. Fixed-trade stress preserves quantities/times/targets
and recomputes adverse costs; it does NOT re-run the strategy or restore losses.
The separate cost_stress replay does re-evaluate all original entry gates.

## Stopped accounts and shadow observations

Main accounts NEVER reset after a halt. Independently enumerate all valid15m
entry signals and report reference-price change at90/360min, plus observed
minute-OHLC favorable/adverse excursions. Signals after each scenario's halt are
listed separately. Truncated horizons are RIGHT_CENSORED, never fabricated.
These are overlapping, unsized price labels without stops, fees or execution.
`SHADOW_SIGNAL_DIAGNOSTIC_NOT_TRADES` has no account return and no win rate.
It is not added to the main ledger or counted toward100 independently executed trades.
First2s of the entry minute are not resolved; extrema are descriptive only.

## Checks and artifacts

Pinned manifests, priorrun identity, 25original source hashes, raw->normalized,
two old downloads, and 1m->15m overlapping OHLC must all agree. The two coarse
confirmation strategies must reproduce the saved old common2d trades and risk
results exactly, excluding only descriptive entry labels and new tracing fields.

A separate verifier never imports this replay/engine. It independently recomputes
causal15m features, entry quantity/risk/cost gates, all modeled fills/caps/fees,
funding, net/return/win statistics, and chronological cash/drawdown. It checks
every held-position sample for an earlier missed exit and exit priority.
Causality, gap/tamper, cap, reversal, cooldown, halt and failure-handling tests
use clearly artificial fixtures. They are not profitability evidence.

`all-results.json.gz`, `all-scenarios.csv`, `paired-differences.json`,
`shadow-signals.json`, `inputs.json`, input/feature audits and `manifest.json`
are retained with exact code and CI logs. Output directory must be new; failures
cannot reuse an earlier positive summary. Actual research process blocks socket
connect/DNS and subprocess audit events; Actions artifact download occurs outside it.

Run after bootstrapping original application:
`PYTHONPATH=application:longer:execution python3 execution/run_paired.py --prior prior-inputs --prior-final prior-results --out evidence/paired`
The GitHub Actions workflow restores the pinned inputs and runs this command.

No 55/60% future win rate or subscription income is established by a short seen
sample. Net win rate, average net trade, equity return/+5%, model sensitivity,
halts and sample limitations are all separate. New independent confirmation
needs subsequent unseen data and higher-quality mark/L2/oracle observations.

Official sources (consulted for model limitations, not a profitability claim):
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
