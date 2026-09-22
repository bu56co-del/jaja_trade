# Frozen strategy comparison on saved September ETH history

This experiment compares only `V030_R30` and `V035_R30` from threshold44.
It does not modify either strategy or the account engine. It answers whether
the 0.35% admission rule's A44 improvement survives a different saved period.
This is retrospective research, not a never-seen holdout or live income.

## Inputs and chronology

Use these exact same-repository GitHub Actions artifacts:
- threshold44-final-35681103912-1 (commit f1a086139c48a81126b5a8fb6c9512cf6eca737e)
- backtest-35452535157-1 (main commit 4cc3aa9239383a5a07e4f3305a9b2b17f61c9e57)
- native-prepared-35502377024-1 (commit e5d8746973ee748a90b05e7aaa91c6287c334a3e)

`source-pins.json` binds the two historical archives and their on-disk roots.
Raw request logs, response hashes, projections, metadata, duplicate timestamps,
OHLCV/counts, and funding histories must agree. No downloads from market APIs.
The original 5,000 and native 3,000 minute candles overlap in 2,501 exact rows;
the complete union is 5,499 minutes. Remove only the leading nine-minute partial
15m bar, leaving 5,490 bars from 2026-09-16 04:30 UTC through 2026-09-20 00:00
exclusive. The first 600 minutes warm indicators. The execution window starts
September 16 14:30 UTC and contains 4,890 minutes, subject to existing halts.
No interior dates are removed. The 366 complete 15m aggregates are cross-checked
against saved native15m OHLCV and trade counts, using the existing data-audit
tolerances (price max1e-6 absolute/1e-9 relative, volume max1e-5/1e-6, count exact).

91 actual funding timestamps are retained, including sub-second hour offsets.
Oracle payment prices remain prior-close proxies; metadata is not reconstructed
point-in-time. The two official records on September 19 at 08:34 and 08:36 UTC
have flat OHLC and zero volume/count in all three saved downloads. They are
preserved and flagged, not forward-filled; entries/exits and held positions on
these minutes are separately counted and do not certify tradeability.

## Fixed comparison

15m closed BB20 bands, short-only recovered upper-band signal, red candle lower-
half close, room>=three model round-trip costs; volatility qualification is
1.5*ATR15/close>=0.0030 or >=0.0035. Both equality boundaries pass. No new EMA,
confirmation, dynamic exits, leverage or discounts. Existing stop floor/ceiling,
1.8R conservative target,360min hold, sizing, reserve, fees/funding, cooldown,
entry cap, consecutive-loss and drawdown halts remain identical.

Two candidates x two cost models x two minute OHLC/OLHC paths = eight new-period
scenarios. Each has one independent virtual10USDC account for the full continuous
window; never reset daily or splice A44 profit into it. Reporting per day assigns
whole trade net to closing date, not daily mark-to-market return. A no-trade case
has undefined win rate. Fixed-trade cost sensitivity and complete higher-cost
account replays are separate outputs.

The same two candidates also replay all27 A44 contiguous blocks (216 regression
scenarios) and must match every saved core account field. These are regressions,
not 216 additional fresh-market experiments. All failures are preserved and no
result may be reported complete without all216+8 cases.

## Evidence and safety

One ordinary GitHub-hosted job, maximum25min, no schedule. Artifact download
requires only read Actions/contents; persist-credentials is false. Research runs
with socket audit events blocked before imports. No wallet, private key, live or
testnet orders, account API, paid data, package installation, main write or merge.
Only public market data and existing research evidence enter artifacts; artifact
retention7days is not permanent storage. Synthetic tests are software tests only.

`results/input-block.json` stores the joined market inputs. `data-checks.json`,
`protocol.json`, `comparison.csv`, `daily.csv`, the eight full ledgers and event
traces, A44 regression ledgers and evidence hashes make results reproducible.
Each account uses the existing engine-external reference for causal signals,
quantity/fees/funding, earliest exit, cash/drawdown/halting. This is self-audit,
not independent third-party certification or exact exchange/L2/mark execution.

Run `run_compare.py --parent parent --original september-original --native
september-native --out results` with the same PYTHONPATH as the workflow.
