# A44 — improve the positive BB15 short candidate, without omitting dates

Refs Issue #1. This is retrospective paper research, not live orders or income.

## What is being tested

The frozen input is **44** A-class ETH dates, not the earlier erroneous 41-day
summary. All 63,360 one-minute bars are loaded and verified against the existing
58-file hash index. They form 27 truly consecutive blocks; missing dates are not
bridged. Each block's first600 minutes are warmup: 16,200 minutes total. The
remaining47,160 minutes are execution windows, before account halting. All44
calendar dates have an execution window. `coverage.csv` documents every date;
`daily.csv` additionally separates entries, closes and inactivity after halting.
Closed-trade attribution in that table is NOT daily mark-to-market performance.

The base signal uses **15m closed candles**, BB20 = SMA20 +/-2 population SD.
Previous close must be above its own upper band; latest close must be strictly
inside both latest bands and below its own open. Only short positions are
allowed. Model execution and risk observations use **1m** bars at +2s,+21s,+40s,
+59.998s in OHLC or OLHC order. These are assumptions, not actual tick paths.

## Twelve predeclared definitions

3 trend choices x2 room choices x2 candle-confirmation choices =12, comprising
one exact baseline and11 new variants. Primary: `BB15_UP_VETO_ROOM_LOWER_HALF`.

- `NONE` trend: original entry; `UP_VETO`: reject when EMA9>EMA21 AND EMA9 rise
  over4 completed15m intervals exceeds0.5 current ATR. `DOWN_ONLY`: require
  EMA9<=EMA21 AND nonrising EMA9 over1 interval.
- `ROOM`: at the current assumed short entry fill, require
  `(entry_fill - frozen_signal_middle)/entry_fill >=3*roundtrip_cost`, where
  `roundtrip_cost =2*(fee + half_spread + adverse_slippage)`. `NONE` does not add
  this constraint. Middle is the SMA20 of the setup's closes, never a future
  mean. This is a gate, NOT a promise price reaches the mean, nor a new target.
- `LOWER_HALF`: require the red signal candle's close at or below the midpoint
  of its own high/low range and a positive range; `NONE` is the original body rule.

EMA calculation uses the same preceding40 completed15m candles (10h), starting
from that window's first close. This is intentionally not a claim of infinitely
warmed EMA. Production uses recurrence; reference uses geometric closed-form
weights and independently reconstructs the same preceding candles. A current
1m OPEN is only used at its execution observation for ROOM, not as a past signal.
Filtered events are not replayed later. All accounts are re-run from block start;
we do not delete losing rows from a saved ledger or omit August.

## Preserved economics

Original account engine and strategy files are unchanged. Per block there is
one independent virtual10USDC account. Net sum/270 is average block return, not
a continuous10USDC track record and not a request to invest270USDC. No resets
inside blocks. Stops, capped1.8R targets, max360min holding, original position
and margin limits,15min cooldown,6 entries/24h,3-loss halt and5% drawdown halt
are preserved. Funding uses original saved rate timestamps and preceding1m
close as a labeled oracle proxy. Both cost assumptions and both price paths
are run:12*27*2*2 =1,296 cases. Base and higher-cost entry reruns are distinct
from fixed-same-trade cost stress. Zero-trade variants cannot be recommended
as the best trading strategy.

## Evidence contract

Parent: run35599128691, commit bfb9f5591b879c8d0531c9316e42f26192b3b789,
artifact10638072896, ZIP SHA256
8cc071656e4c5b15c0ef272925dfea1f5291430ea2eb241cf5c5afe06cc35879.
Source and input identity, prior group hashes/merged rows,44 dates and all27
blocks are checked. The unchanged baseline must exactly reproduce all108
parent cases' core fields, including trades, balances, decisions and halts.
The existing engine-independent ledger auditor verifies each trade, costs,
funding, earliest applicable exit, cash, drawdown and halt. Extra predicates
and numerical features are checked by the separate `reference_bb.py`.

Outputs preserve every case and every raw short setup's filter decisions, even
when rejected, with reference checks. `aggregate.csv` compares net, mean net,
win rate, count, block results, drawdown and same-trade stress; `coverage.csv`
and `daily.csv` answer which dates/time were actually covered. Hashes and
provenance reject missing/stale group output. Artifacts expire; no permanent
repository market-data archival is claimed.

## Resource and safety boundary

One workflow: original account tests + relevant mean/direction tests + new
tests;3 research jobs, each max25min, then combine. No schedule, wallet, private
key, paid data/API, order submission, main write, merge, force push or release.
No packages or external bots are installed; only previously audited source
and public market artifacts are read. No original source file is edited.

The same selected A44 is development data. Price-research acceptance does not
prove native mark-trigger stops, L2, latency/queue or precise oracle cashflows.
No stable-income, future-win-rate or guaranteed-profit claim is made.

## Method references (not performance evidence)

https://www.bollingerbands.com/bollinger-band-rules — band tags are not standalone
reversal signals and trending prices may keep walking a band.
https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/ema
— EMA definition. Filters and thresholds above are our testable hypotheses,
not rules asserted to have profitable ETH results by either source.
