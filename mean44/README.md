# A44 recovery confirmation and dynamic exits

Paper research only. No new market requests, orders, accounts, paid APIs, scheduler,
remote bot code, or dependencies. This tests hypotheses, not guaranteed income.

## Why another batch

The immediately preceding public-method experiment (run35560801424, commit
37e821f456dd0787d57930ca84f85c312532b59d) tested 24 Bollinger/RSI entries with one
common hard-exit model. None had positive base-cost total net profit. That does
not test their complete original systems. The user's response was interrupted;
those results remain evidence and are not re-run or represented as new work.

New primary sources:
- John Bollinger, official rules: https://www.bollingerbands.com/bollinger-band-rules
  Tags alone are not signals; outside closes can continue. Test recovery rather
  than assuming every extreme immediately reverses.
- Larry Connors, Dynamic Exits, 2009-08-12:
  https://tradingmarkets.com/recent/dynamic_exits_how_to_properly_exit_a_trade-641067
  Describes stock/ETF exits beyond SMA5 or RSI2 above70/below30. This motivates
  exit hypotheses; his historical performance is not evidence for ETH.
- Freqtrade BbandRsi, inspected via connected GitHub:
  https://github.com/freqtrade/freqtrade-strategies/blob/7f91ff52bb664423ae673092a6a18d76c50c2c29/user_data/strategies/berlinguyinca/BbandRsi.py
  Its 1h/long-only/25% stop/10% ROI configuration is NOT adopted. Standard
  mathematics is implemented independently; no third-party strategy source is
  copied or executed. This is not a full reproduction of that program.

## Frozen 24 new variants + one unchanged baseline

Two entries x two signal intervals x two filters x three exits =24.
The unchanged RETEST_0.5_20_50 adds a25th control. All25 x27 A44 episodes x
2 costs x2 OHLC paths =2700 cases. Primary BB_REENTRY_15_RANGE_SMA5 is fixed
before these results. No optimizer, cherry-picked period or no-trade winner.

Entries (mirrored for shorts):
- BB_REENTRY: previous close below its preceding lower20-period Bollinger band;
  newest completed close back inside its own lower band; positive candle body.
  This is recovery after an extreme, not a touch or assumed limit-order fill.
- RSI_RECOVERY: preceding RSI2<=10, newest RSI2>10, positive candle body.
  Short: preceding RSI2>=90, latest<90 and negative candle body.
Filters: NONE or RANGE (20-period direction efficiency<=0.30). RANGE is an
experimental label, not an assertion that market regimes are correctly known.
Signal timeframe:5m or15m. Same10h warmup:120 completed5m bars or40 completed15m
bars. All features use that fixed trailing window. The15m version is explicitly
NOT the earlier120-bar15m model. No missing dates are borrowed for warmup.

Exits: original hard exits only (HARD), or hard exits plus SMA5, or hard exits
plus RSI2 above70 for long/below30 for short (RSI70). SMA5 exits long when a
completed signal-timeframe close is above SMA5, short when below. Dynamic exits
occur at the next1m modeled open, never at the already observed close. Hard
stop/target/account halt/time limit take precedence. They may close losing trades;
there is no rule that hides a loss until the dynamic exit becomes profitable.
All variants retain the original1.8R hard target; the dynamic versions are thus
bounded adaptations, not a claim of exact reproduction of no-stop literature.

BB uses close SMA20 plus/minus2 population standard deviations. RSI2 uses
Wilder smoothing, seeded from the first2 changes of its trailing window; flat
RSI=50. ATR14 is arithmetic mean true range, same as the retained engine.
ER20=absolute20-period close displacement/sum20 absolute one-period changes;
zero denominator gives0. Numerical1e-12 deadband on comparisons is applied in
both independent algorithms, not tuned from returns. Exit price and monetary
checks retain the original1e-17 tolerance.

## Data, accounting and cost boundaries

Restore alternatives44-final-35560801424-1, artifact10622755203, ZIP SHA256
`d21a7a1509778ee42d9ef3f8acb9cd18ee319ab37c0e60ad13dff2d1db8e3748`.
Its previous/previous/input contains the original audit. All58 pinned files
are checked against reversal44/dataset_index.json. Exact44 days =63360 1m bars,
27 real adjacent episodes, with first600min each warmup. No synthetic filling,
B/C dates, cross-gap position, daily balance reset or new funding download.

Each episode is a separate virtual10USDC account. Total net/270 is average
experimental return, NOT one10USDC compounded income record;270 is not a real
capital requirement. Keep all episode losses and zero-trade cases.

Unchanged original risk constraints: one position, target notional10.10,
minimum notional10, maximum1.15x equity, modeled2x margin and3.50 cash reserve,
planned loss including friction<=1.25% equity, max3 consecutive losses and5%
drawdown halts, stop=max(0.3%,1.5ATR/close), reject above1%, target1.8R,
max360min hold,15min cooldown,6 openings/24h. No relaxation to obtain profit.
Base model:0.045% taker fee each side,1bp spread and1bp adverse slippage each
side; stress model:0.09%,3bp,3bp. Fixed assumptions, not a user-specific quote.
Every case also has fixed-same-trade higher-cost decomposition; stress replays
can change entry gates and therefore do not preserve the original trade set.
Funding uses saved actual event times/rates and prior1m close as oracle proxy.
Targets are one-sided capped at the target; stops retain adverse overshoot.
Execution has four assumed observations per1m; not historical book matching,
native mark-triggered stops, actual spread/depth/latency or full oracle prices.

## Evidence and execution

New signals/exit decisions are recomputed independently from1m inputs using
separate aggregation and RSI/variance calculations. Every trade and every
held-position observation is independently checked for signal time, side,
quantity, costs, funding, cap, earliest hard or dynamic exit, cash, drawdown
and halting. Verifier imports no production strategy/engine. This is self-audit,
not third-party certification. Repeated path/scenario trades are not independent.
Original control108 cases must match the saved previous results exactly.

One bounded workflow: prior629 software regressions + new tests, four25min
maximum research jobs and one combine. Ubuntu standard hosted runners only;
no paid data/services, live/testnet orders, main writes, merge or deployment.
Artifacts retained7days are temporary, not permanent archival.
