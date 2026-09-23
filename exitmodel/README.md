# Exit execution sensitivity (Issue #1)

This experiment changes execution assumptions, not the strategy. Never select a
more favorable model and label the resulting gain new alpha or live profit.

## Frozen scope

Parent: fixedrisk run35885160110, head395614a3876c24c29c04f8a685c3ca6727579b32.
Artifact10762872258 SHA256:
8e844569021bef82d58adeabce4c6c5f43cd62a506e6312fdb9bae2b62fb7b4f

BASE_B, RISK125 and RISK125_TP_HALF unchanged. All A44 (63,360 1m candles,
27 contiguous blocks) and saved September (5,490 1m candles, one block).
600 minutes warmup per block. Original entry signals, stops, targets, sizing,
fees, spread, adverse slippage, funding proxy, cooldown, six-hour maximum,
three-loss halt and five-percent account drawdown rules are retained.
A44 consists of27 separate virtual10USDC accounts, not one continuous44-day
return; September is a separate virtual10USDC account. Data were already seen.

3 profiles x4 execution models x28 blocks x2 costs x2 paths =1,344 cases,
including336 unchanged original-model regressions and1,008 changed-model cases.
No extra parameter search, new data fetch, paid services or live/testnet orders.

## Four models

SAMPLED: original observations at2,21,40,59.998 seconds of each minute.
CROSS_0MS: first modeled executable-cover crossing of the original SL/TP line,
then fill at the first qualifying integer millisecond.
CROSS_1000MS (primary): the same trigger, then a1-second assumed delay.
CROSS_5000MS: the same trigger, then a5-second assumed delay.

Within each minute interpolate linearly between existing OHLC/OLHC price nodes.
No close-to-next-open interpolation: due fills falling in that unobserved gap
wait for the next original open and use its price, never the skipped trigger
price. No fill is admitted on a zero-volume official minute.
The first trigger latches; crossing back does not cancel a scheduled exit.
Original DD/maintenance/time-limit exits can preempt it. At an original sample
with simultaneous events, original emergency risk checks run before the fill.
Never use future highs/lows to create an entry signal. New executions can
change later cash, sizing, cooldown and halt paths; replay the complete account.

These are modeled executable-price triggers (preserving the prior trigger
basis), NOT native Hyperliquid mark-price triggers. Delays are hypothetical,
not measured network/venue latency. Original spread/slippage are applied at
execution. Favorable target overshoot is capped; adverse fills are not improved
back to a trigger price. No limit-order fill, queue or depth claim is made.

Account risk monitoring stays at the original four samples. Additional fill
events also update before/after equity and sampled drawdown; this is not a
continuous-risk-monitoring experiment. Emergency exit latency is not changed.

## Verification

The three original models must match every saved parent account, including
financial and timing fields, before results are complete. Existing input,
parent and source hashes are verified. The new engine-external reference uses
binary search and weighted-sum interpolation rather than production algebraic
crossing roots, and independently replays entries, sizes, fills, financing,
remaining cash, drawdown and halts. It reuses pre-existing engine-external
signal features and risk-size reference helpers; this is self-verification,
not third-party certification.

Artificial tests cover crossings, boundaries, latching, adverse/favorable
execution, delays, gap deferral, risk preemption, funding during delay, stale
orders, full account paths, future-data isolation and tamper rejection.

## Safety and interpretation

New research module/workflow only; default Config and production sources stay
unchanged. No main change, merge, deployment, schedule, wallet, payment or orders.
Actions runs with read-only contents/actions permissions, pinned actions,
non-persistent checkout credentials, no new dependency installation and a
25-minute cap. Computation denies socket and child-process operations. All
results and failures are preserved in an expiring artifact, not permanent
market-data storage.

OHLC paths and interpolation are hypotheses, not actual ticks or guaranteed
upper/lower bounds. Native mark-price triggers, historical L2, queue, latency
and exact oracle-funded cash flows remain NOT VERIFIED. A result becoming
positive under interpolation does not establish an implementable improvement.

Official method boundary (not historical-return evidence):
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl
