# A44 public-method alternatives — research, not live trading

## Purpose and provenance

The previous breakout/retest family, early entry, structure stop and larger
reward targets did not improve total net profit. This batch tests **different
entry families**, preserving the same accounting and risk protections.
Sources were researched before returns were calculated:

1. Freqtrade's public BbandRsi example combines Bollinger bands and RSI. File
   `user_data/strategies/berlinguyinca/BbandRsi.py`, blob
   `addc87268affc2f3b1b00549f1ca8b119e41e655`, last-file-change commit
   `7f91ff52bb664423ae673092a6a18d76c50c2c29`.
   https://github.com/freqtrade/freqtrade-strategies/blob/7f91ff52bb664423ae673092a6a18d76c50c2c29/user_data/strategies/berlinguyinca/BbandRsi.py
   The example is 1h, long-only and has a 25% stop and 10% ROI configuration.
   Those settings are **not adopted**. No GPL source implementation is copied
   or executed: standard mathematical ideas are implemented here independently.
2. John Bollinger's rules distinguish band tags from signals and discuss band
   walking and continuation. This motivates testing reversal and expansion
   separately, not assuming every outer-band observation reverses.
   https://www.bollingerbands.com/bollinger-band-rules
3. Larry Connors's published RSI2 research motivates short-horizon extremes.
   His R2 example is equities on daily bars, not an ETH strategy or these rules.
   https://tradingmarkets.com/recent/the_improved_r2_strategy_84_correct_with_just_6_rules_-674361
4. QuantConnect's strategy concepts distinguish momentum from mean reversion.
   https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/behavioral-finance

External returns are not imported as evidence. These 5m, long/short adaptations,
our thresholds, micro confirmations and original hard exits are original research
hypotheses, **not exact reproductions** of the external complete strategies.
No Freqtrade/MQL/external bot, dependency, wallet or API key is installed.

## Fixed grid (before this batch's performance)

24 new entries + the unchanged `RETEST_0.5_20_50` baseline. All variants use the
same 27 real contiguous A44 blocks and 2 costs x 2 OHLC paths: **2,700 cases**.
Primary: `BB_FADE_20_MICRO_ROOM`; no result-based primary replacement.

| Family | Parameters | Base setup | Optional filter |
|---|---|---|---|
| BB_FADE | Band window 20 or 40; DIRECT or MICRO | Close below lower band and RSI14<30 => long; above upper and RSI14>70 => short | ROOM: frozen setup mean minus actual entry fill, in trade direction, >=3 x modeled roundtrip cost |
| BB_EXPAND | Band window 20 or 40; DIRECT or MICRO | New close beyond band, prior close not beyond prior band; band width increasing; latest volume >=1.2 x prior20 volume mean; RSI14>=55 long / <=45 short | EMA20 above/below EMA50, and EMA20 slope matching trade direction |
| RSI_DIP | RSI2 crosses below5 or10 / above95 or90; DIRECT or MICRO | Contrarian entry on the new threshold crossing | Price above/below EMA50 and EMA50 slope in trade direction |

Each family has 2 x 2 x 2 = 8 variants. Filters are NONE versus ROOM, EMA20_50 or
EMA50 respectively. No hidden optimization, ensemble account, DCA or grid orders.

## Indicator definitions and causality

Each completed 5m feature uses exactly the previous120 complete5m candles, the
same history length as the retained baseline. Typical price = (H+L+C)/3.
Band mean is SMA of typical price; variance is population variance (divide by N),
and bands are mean +/-2 standard deviations. Width = (upper-lower)/mean.
No Gaussian-probability claim is made. EMA seeds from the first close of that
120-bar window; alpha =2/(N+1). ATR14 is the arithmetic mean of the last14 true
ranges (not renamed Wilder ATR). Wilder-style RSI seeds gain/loss means from
its first N changes and recursively smooths the remaining window; RSI is 50
for completely flat data, 100 if gains but no losses. A newly computed rolling
window is deliberate, not an assertion of an infinitely warmed indicator.
Volume reference excludes the signal bar. All component values are recorded.

Only a change from zero/opposite to a nonzero 5m setup creates a fresh event.
DIRECT enters at the next1m open observation, not the prior close.
MICRO freezes the 5m setup, then waits up to5 **subsequently closed**1m bars.
A long confirmation must close above the preceding1m high and above its own open;
a short must close below the preceding1m low and below its own open. Execution
is at the next1m model open. No same-bar confirmation or implied limit fill.
An active event ignores new5m events until consumed or expired; an event can fire
once only. Signals rejected by cooldown/risk are not subsequently backfilled.
ATR stays the original5m setup value; signal_close is the latest1m close.
ROOM is checked against the actual assumed entry fill and the frozen mean,
including the selected cost scenario, before core opening checks.
No new strategy-based exits are added: this batch isolates entry ideas under
one common risk/exit framework, not full external-system replications.

## Execution and experimental unit

Input is the exact A44 dataset indexed by `reversal44/dataset_index.json`, restored
through prior run35555725628, artifact10620291401, SHA256
`867da4705070240f4e1d9e0646e05d2e2f6d07968b7d8cd739df9d3e39218312`.
Underlying audit source: run35519132209, artifact10607489773. All58 pinned input
files are verified. 44 days =63,360 1m candles, split into27 genuinely contiguous
blocks; 600min per block are warmup; no missing/rejected days are inserted.
There is one independent **virtual10USDC** account per block, not a continuous
10USDC track record. Sum/270 is mean episode return;270 is an analytical
initial-balance sum, not requested real capital. No resetting inside a block.

Original protections: single position, minimum notional10, target notional10.10,
max1.15x equity, margin model2x with3.50 reserve, planned trade loss including
friction<=1.25% equity, stop=max(0.3%,1.5ATR/close) and reject above1%,1.8R target,
360min limit,15min cooldown,6 entries/24h,3 consecutive loss halt and5% drawdown
halt. Original end-of-block liquidation is reported, not silently omitted.
Base fee0.045% per side,1bp full spread,1bp adverse slippage per side. Stress fee
0.09%,3bp spread,3bp slippage per side. These are fixed model scenarios, not a
claim about the user's fee tier or a new live fee quotation. Funding uses saved
actual-rate timestamps and preceding1m close as labeled oracle proxy.
Stop exits use observed model prices (adverse overshoot not improved); target
exits retain the one-sided cap on favorable overshoot. OHLC/OLHC are scenarios,
not proven bounds. No L2, true mark/oracle, queue or latency reconstruction.

## Validation and disclosure

Production features and pending-state signal generation are checked against a
separate reference calculation and a forward event scan, without importing the
production strategy engine. Price features tolerate1e-12 relative/absolute for
algebraic rounding; monetary checks retain1e-17. Direction/identity is exact.
Every trade has independent entry timing, signal/trace, original sizing/risk,
fees, funding, target-cap, cash and first-risk-exit/drawdown/halt checks. The
unchanged baseline's108 cases must match its saved prior outcomes.
All cases and negative/no-trade outcomes are stored. A zero-trade candidate
cannot be the reported best trading candidate. Basic net, win rate, episode
results, mean trade net, drawdown and fixed-same-trade stress are separate.
No number of software tests proves profitability. Results are retrospective
on the same A44 development data; no claim of future guaranteed income.

One bounded workflow: tests +4 jobs (25min maximum each) +combine. No schedule,
paid service, new market download, real/testnet orders, main write, merge or
release. Artifacts expire; this is not permanent archival of raw trade history.
