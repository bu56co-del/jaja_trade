# A44: nine volatility / room admission thresholds

This implements the previously proposed 3x3 comparison, not another new signal
family. All alternatives are retrospective paper research; no income guarantee.

## Fixed definitions before this batch's returns

`1.5 * ATR15 / completed15m close >= min_vol` with min_vol 0.0025, 0.0030,
0.0035. `(modeled short fill - signal SMA20) / fill >= room_multiple * roundtrip
friction` with room_multiple 2.5, 3.0, 3.5. Friction is twice the sum of per-side
fee, half the modeled full spread, and per-side adverse slippage.

All nine combinations are included. The unchanged B/VOL baseline is
`V030_R30`; primary is predeclared `V035_R30`. No result-based replacement of
that primary, deletion of failed alternatives, or follow-up fine grid in this batch.

The 0.25% admission test DOES NOT change the original 0.30% stop floor. Equality
at either threshold qualifies. All variants start from the full closed15m BB
setup stream, NOT from the already filtered three-cost baseline trades.

The original BB20 upper-band re-entry short signal, red body closing in the
candle's lower half, next1m model open entry, 1.8R capped target, ATR stop,
360min maximum hold, fees, quantities, funding-rate timestamps / preceding-close
oracle proxy, single-position restriction, cooldown, entry caps, consecutive-loss
halt and drawdown halt remain unchanged. No micro-confirmation delay, no EMA
filter, no stop widening and no increased leverage.

## Dataset, parent and experimental unit

Input is the full fixed A44 set: 44 dates, 63,360 one-minute candles, 58 pinned
input-file hashes. The same 27 truly contiguous blocks each have 600 minutes
warmup. All 47,160 remaining execution-window minutes are included, subject to
unchanged account halts. No rejected days, missing hours or prices are filled.
Each block is a separate virtual10USDC account, with no reset inside the block.
Total net/270 is the mean episode return, NOT one10USDC continuous return and
NOT a requested270USDC deposit. The date gaps are not bridged.

The exact prior artifact is `entry44-final-35677804240-1`, run35677804240,
commit54e21b5c92185a9dd63d52f1f04619069c55cf0d, artifact10673857849, ZIP SHA256
ab8834f9c02aa482c4a48e4d8d553aeef31f835bd3b2f8a43351fae76f26c840.
Its evidence manifest, previous source and 432-case grid are checked. All108
original B baseline episode/cost/path core account records must match exactly.

9 definitions x27 blocks x2 costs x2 paths =972 cases. Base model: fee0.045%
per side, full spread1bp, adverse slippage1bp/side. Stress model:0.09%,3bp,3bp.
These are unchanged research scenarios, not a new exchange fee quote. Higher-cost
full replay and fixed-same-trade stress are separate outputs.

## Verification and scope

Production division-based threshold gates are checked against separate
cross-multiplied inequalities and independently computed input features. The
reference module does not import the production threshold module or account
engine. Existing engine-external chronological auditing checks every fill,
fee, funding event, size, first eligible exit, cash, drawdown and halt.

Tests cover equal boundaries, looser room restoring otherwise rejected signals,
unchanged stop floor, invalid values, unknown definitions, full artificial
accounts, baseline decision identity, future suffix/entry-candle HLC exclusion,
failed/incomplete output and an offline guard that allows SSL import but denies
socket creation/DNS. Fixtures are not historical performance evidence.

One ordinary ubuntu-24.04 Actions job <=25min; no new market request, external
package install, wallet/account API, order, payment, schedule, main write, merge
or release. Research Python denies socket audit events. Evidence includes all
972 cases, all gates, complete date coverage, parent input and failures. Artifacts
expire and are not advertised as permanent storage.

A high win rate alone does not select a replacement. Compare total net and
same-trade cost stress, then sample count, blocks and drawdown. No-trade results
cannot be the best trading candidate. No extra fine parameter search is included.
Minute OHLC paths are not tick/L2 fills, and true mark-triggered stops, oracle
payments, queue priority, liquidity and latency are still not reconstructed.
