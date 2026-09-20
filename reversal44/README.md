# A44 false-break / breakout-retest research

Refs Issue #1, original research PR #2. Historical simulation only. No wallet,
real/testnet order, paid data service, live feed, schedule, merge or deployment.

## What is being tested

24 new candidates = 2 families x 4 location/depth choices x 3 EMA filters.
Three controls use immediate close breakout, immediate breakout plus EMA9/21,
and EMA9/21 crossover, all with the SAME 360-minute/risk-only exit policy.
The controls are not claimed to be byte-identical strategy replicas of the old
90-minute/EMA-exit experiment; the underlying accounting engine stays unchanged.
The prespecified study reference is `RETEST_0.618_9_21`, not an automatic winner.

All 27 strategies receive both original cost assumptions and both OHLC/OLHC
within-minute paths on all 27 continuous A-class data blocks: 2,916 episode rows.
No candidate is removed for making no trades or losing. This is retrospective
research on a previously selected data subset, not unseen forward validation.

## Exact data

Only the 44 dates indexed in Issue #1 comment 5751058103 and `dataset_index.json`.
Parent run 35519132209, artifact 10607489773 (`ct93-full-35519132209-1`), ZIP SHA256
`2f74b1d2de23dfbcc43ddf00f9719bb9552731e64868bf536ea43bc978d44b5d`.
The JSON index pins 44 compressed 1m files, 12 saved official funding responses,
and two source audit reports by hash. Metadata is the previously preserved
4-decimal ETH size step and 25 exchange maximum leverage, NOT per-date verified
historical metadata. Model margin leverage remains only 2.

Admission requires EXACT `PRICE_RESEARCH_CANDIDATE`; the 10 empty-minute days
and 39 mismatch days are excluded. 63,360 input 1m bars are regrouped into
27 real contiguous periods; periods have 1, 2 or 3 days. Missing dates are never
bridged. Each period starts a SEPARATE 10-USDC hypothetical experiment. Within a
period there is no daily cash reset, halt reset or forced midnight liquidation.
At the end of the actual available period an open position is explicitly closed
as `BACKTEST_SEGMENT_END_ASSUMED_FILL`, with its costs and loss retained.

Signals use 5m closed candles, execution uses the retained 1m candles. The first
120 five-minute candles (10h) of EACH block are warmup, giving 47,160 potentially
executable minutes in total. The original warm-start convention skips the first
signal at startup. 15m signals with a 120-bar warmup would consume 30h and discard
all single-day periods; choosing 5m is an explicit timeframe change, not a claim
that historical 15m results have been reproduced.

## Causal rules

At closed bar k, the reference range is the highest high / lowest low of the
PREVIOUS 20 complete 5m candles. The current candle is excluded. ATR is the mean
of the last 14 true ranges. EMA uses the original engine's rolling-120-close,
first-close-seeded recursion, alpha=2/(n+1), not an indefinitely seeded live EMA.
EMA9/21 and EMA20/50 require fast-minus-slow AND fast slope in the TRADE direction.
No EMA filter is a separate control. Filters are evaluated at confirmation, not
retroactively at an earlier candle.

A setup must exceed one range edge by more than 0.10*ATR. A candle exceeding BOTH
edges is ignored. The impulse anchors are frozen when the event is armed:
A = opposite extreme of the preceding six closed candles; E = event-bar extreme.
They are never moved to fit later highs/lows. d is the ORIGINAL breakout direction.

### False break (`FAKE`)

A wick break or a closing break can arm the event. From its closed event candle
through the next three closed bars, require a close back inside the frozen edge
by at least 0.10*event_ATR and a candle body pointing in the reverse direction.
Enter -d at the NEXT assumed open, subject to account/cost gates.

Depth = d*(E-confirmation_close)/abs(E-A). The four conditions are no extra depth,
depth>=0.50, depth>=0.618, depth>=0.66. These ratios measure recovery of the frozen
impulse, not a future-extreme Fibonacci drawing. A confirmation consumes its
setup even if the EMA filter rejects it. One setup never generates repeated votes.

### Breakout then retest (`RETEST`)

First require an actual CLOSE outside the range by more than 0.10*ATR.
Only subsequent candles, at most 12 after arming, may touch the test level:
- no ratio: the old breakout boundary;
- ratio r: E - d*r*abs(E-A), with r in 0.50 / 0.618 / 0.66.

A touch requires the candle's high-low range to intersect the level +/-
0.15*event_ATR, with the preceding close on the breakout side of that level.
A DIFFERENT closed candle, at most two bars after touch, must close beyond the
touch candle's high (long) or low (short), with its body pointing in direction d,
and must regain the original breakout boundary. Only then enter d next open.
A close beyond the original opposite anchor A invalidates the event. Expired
setups are abandoned; no filled limit order is invented at the retracement level.
All short rules mirror the long rules.

## Risk, execution and economics

All experiments keep the original single position, minimum opening notional 10,
target notional 10.10, cash reserve 3.50, notional/equity limit 1.15, risk per trade
1.25%, permanent three-loss halt within an episode, 5% account drawdown halt,
six entries per rolling 24h, and 15-minute cooldown. Stops are max(0.3%,1.5*ATR/C),
and rejected above 1%; target is 1.8 times the stop distance. Thus stops are ATR
risk stops, NOT necessarily beyond the entire false-break extreme.
No discretionary opposite-signal exit is added; exit only on original risk rules,
stop, target, 360-minute maximum holding time, or explicit episode end.

Use inherited `FINE_1M_TP_CAP`: four assumed observations per minute, with the
old model's one-sided target-price cap. An advantageous target overshoot is not
credited; adverse stop overshoot is not improved. Neither OHLC path is a proven
best/worst bound. Quotes, spread, depth, slippage, mark and oracle are modelled.
Funding retains each saved official event timestamp (including sub-second hour
boundary offsets); cash uses the preceding 1m close as a labelled oracle proxy.

Base: taker fee 0.045% each side, spread 1bp, adverse slippage 1bp each side.
Stress: fee 0.09% each side, spread 3bp, slippage 3bp each side. These are inherited
scenario assumptions, not a quote for any particular user's historical tier.
Stress reruns may admit different trades; fixed-SAME-trade stress is also reported.

Every candidate/cost/path has 27 separate 10-USDC episode experiments. Sum net
is an experimental aggregate over total assigned virtual capital 270, not a
continuous 10-USDC account return. Mean episode return = sum net /270 *100.
Net win rate pools wins/all closed round trips within that cost/path only; it does
not add OHLC and OLHC copies. Report maximum SINGLE-episode drawdown, not a fake
combined drawdown curve. No reset of a losing episode is hidden as continuation.

## Validation and reproduction

The independent reference module does not import the execution engine or the
production signal module. It recomputes EMA/ATR/features, searches forward from
fixed setup events, compares every entry decision, and verifies fills, quantity,
fees, funding, target cap, first risk/target/time exit, cash, drawdown and halts.
A local preflight on one assigned candidate group tests the same frozen design;
its result is not called another independent dataset or a separate strategy win.
Tests use labelled artificial fixtures, including future-prefix, mirrored side,
ratio separation, setup expiry, missing-day, fee corruption and account cases.

In an isolated checkout: `python3 bootstrap.py`; restore the pinned source
artifact under `ct93`; set PYTHONPATH to
`application:application/tests:research:book_research:mix_research:sweep:longer:execution:reversal44`.
Run `python3 reversal44/run44.py --group N --source ct93 --out output-N`, N=0..5,
then `--combine` the six output directories. The workflow performs the same steps.
No new market requests or package installation are required for this study.
Artifacts preserve original per-episode outputs, input data, source and tests.

## Method sources (not evidence of profitability)

CME describes support/resistance as zones and their potential role reversal:
https://www.cmegroup.com/education/courses/technical-analysis/support-and-resistance
CME explains Fibonacci retracements and their use with other indicators:
https://www.cmegroup.com/education/courses/technical-analysis/fibonacci-retracements-and-extensions
Fidelity documents 61.8% retracements:
https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/fibonacci-retracement
Fidelity documents EMA sensitivity and trend interpretation:
https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/ema

The 20/6-bar anchors, ATR buffers, waiting windows, EMA pairs, 0.66 threshold and
risk combination are this study's explicit hypotheses. 0.66 is NOT labelled a
standard golden ratio. There is no borrowed equity-market or book win-rate claim.
A historical positive result is not a guarantee of future income or exact fills.
