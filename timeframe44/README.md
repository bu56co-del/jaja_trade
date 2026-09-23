# 4h context / 15m decisions / 1m execution

This experiment separates the user's 4h-chart idea from a four-hour maximum
holding time. It compares 12 definitions, not a guarantee of income.

The base is `BB15_NONE_ROOM_LOWER_HALF`, run35602620940/commit
c186c5dff4b9ff4ca0be6903b1b047bafac76268. It stays short-only: a completed15m
close moves from above the upper BB20 band back inside, the new candle is red
and closes in its lower half, and the modeled entry-to-middle distance covers
at least three modeled round-trip costs. No new long strategy is implied.

## Factorial comparison

Three context filters x two exits x two holding limits =12 variants:
- NONE: no4h filter, retaining the profitable historical baseline.
- UP_VETO: deny shorts when4h EMA9>EMA21 and EMA9 rose more than0.5 ATR4h
  over the last four4h intervals.
- DOWN_ONLY: permit shorts only when4h EMA9<=EMA21 and EMA9 is nonrising.
- HARD: original stop/capped1.8R target/account protections, plus holding limit.
- MID15: also close a short at the next1m modeled open after a completed15m
  close is at/below its contemporaneous SMA20. Original hard exits go first.
- Holding limit:360 or240min. No requirement to remain in a losing trade for4h.

Primary predeclared: DOWN_ONLY_MID15_240. Unmodified comparison:
NONE_HARD_360, with all108 original episode/cost/path ledgers required to match.
All12 x27 episodes x2 costs x2 OHLC paths =1,296 cases. All results are retained.

## Context and no future leak

A44 has63,3601m candles in44 disconnected dates. The27 actual contiguous
blocks remain separate virtual10USDC experiments, each with600min warmup;
47,160min total execution windows. No gap bridging, date deletion, or within-
block capital reset. Sum net/270 is mean episode return, not a compounded10USDC
track record and not a request to deposit270USDC.

The short blocks cannot independently warm up a4h EMA. Therefore the exact
public official ETH4h history from120bars before the earliestA44 date through
the end of the lastA44 date is downloaded twice. It is only indicator context,
not extra trade dates. Raw responses/request hashes are preserved and all264
complete4h aggregates withinA44 must agree in OHLCV/count within the same
fixed audit tolerance (prices max1e-6 absolute/1e-9 relative, volume max1e-5
absolute/1e-6 relative, count exact). Missing/context conflicts fail the batch.

Each decision uses only the last1204h candles with T<decision time. The latest
unfinished4h candle is excluded. EMA9/21 is first-close seeded over that window;
ATR is the arithmetic mean of the last14 true ranges. Independent geometric
EMA calculation checks the signals. Differences below1e-12 are treated as ties.
15m entry and strategy exit use closed candles;1m OHLC/OLHC four-point paths
model execution. These paths are not proven bounds or a reconstruction of L2,
mark-triggered exchange stops, order queue, latency or exact oracle cash flows.

## Costs and safeguards

Original quantity, planned-loss cap, single position, fees, funding-rate timestamps
and prior-close proxy, stop floor/ceiling, adverse stops, conservative target cap,
cooldown, entry cap, consecutive-loss and drawdown halts are unchanged.
Base: fee0.045%/side,1bp full spread,1bp adverse slippage/side. Stress:0.09%,3bp,
3bp. These are scenario assumptions, not a changed official fee quotation.
Fixed-trade stress and full higher-cost replays remain distinct.

No wallet/key/account endpoints, paid data, extra dependency, order, schedule,
merge or release. One bounded workflow: tests, context fetch, three research
jobs (each<=25min), combine. Only anonymous candleSnapshot requests for ETH4h
at the fixed public /info endpoint; no redirects, no credentials,6requests max.

## Reproduction and evidence

Run bootstrap.py, then put application, application/tests, longer, execution,
reversal44, alternatives44, mean44, direction44, refine44 and timeframe44 on
PYTHONPATH. Restore parent artifact refine44-final-35602620940-1 from its exact
run. `run_h4.py --prepare --parent parent --out context` downloads the context;
then `--group 0|1|2 --parent parent --context context/market --out output` replays
assigned cases. `--combine <three-group-directories> --out results` checks grid,
input hashes and run provenance, writes all results and aggregate CSV.

Auditor verify_h4.py is the existing engine-external account arithmetic extended
only to validate the declared240/360min maximum hold, plus independent4h EMA
and MID15 predicates. No parent code is modified. Unit tests are artificial,
not evidence of profitability. Every real case checks causal entries, costs,
funding, first eligible exit, cash, drawdown and halting. All artifacts have an
expiry; no permanent data archive is claimed.

Official API source (capabilities, not profit evidence):
https://hyperliquid.gitbook.io/Hyperliquid-docs/for-developers/api/info-endpoint
