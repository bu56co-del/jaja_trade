# A44 direction asymmetry: complete account replays, not selected winning trades

This is a final small follow-up to the current public-method research. The user
asked for methods that improve net profit. In the existing two-sided account,
the RSI recovery15m hard-exit method's short trades summed positive while its
long trades summed negative. That is a retrospective diagnostic, NOT a tested
short-only return: removing a side changes later opportunities, cooldown, cash
and halting. Hence the complete-account replay below.

## Frozen six definitions

Base entries: BB_REENTRY_15_NONE_HARD and RSI_RECOVERY_15_NONE_HARD, from corrected
mean44. Each is fully replayed with BOTH, LONG_ONLY and SHORT_ONLY. Six definitions,
four new one-sided candidates,27 episodes,2costs,2paths =648cases. Primary is
RSI_RECOVERY_15_NONE_HARD_SHORT_ONLY. No further candidate additions in this batch.

Only the causal entry signal's allowed direction changes. A rejected direction
is set to0 before the original engine sees it. This does not remove losses from
saved results, skip adverse funding, or alter already-open positions. Entry
confirmation, conservative target cap, initial stop, fees, slippage, risk budget,
1.8R,360min holding, position/margin limits,15min cooldown,6entries/24h,3-loss and
5% drawdown halts are unchanged. Every loss, flat episode and boundary exit stays.
The BOTH variants must exactly reproduce all216 parent cases, not just totals.

BB: after a previous close outside a20period2SD band, a completed15m candle closes
back BETWEEN both bands with its body in the recovery direction. RSI: previous
RSI2>=90 then latest<90 with a falling body gives a short; <=10 then>10 with a
rising body gives a long. These are original ETH adaptations, not copied external
bots. Source concepts remain in mean44/README.md (John Bollinger, Larry Connors,
and inspected Freqtrade public example). No external returns are imported.

## Data and experimental unit

Parent: corrected mean44 run35598241627, commit45a0c92a4adfe6f8d1c554b01164b4b995b437c8.
The runner requires that exact run/attempt1, all2700 parent cases, the correction
note and byte-identical core engine/validator files. It verifies parent group
hashes and complete group-to-combined equivalence, and all58 original input hashes.
A44 remains63,360 real1m candles across44 selected dates,27 truly contiguous episodes,
600min warmup per episode. No new market download, interpolation or B/C-class dates.

Each episode is a separate virtual10USDC paper account. Sum net/270 is mean
experimental return, not one continuous10USDC income record or a request for
270USDC. No resets inside an episode; no bridging missing calendar periods.
No new market-data or cost claims. Fees/funding, native mark-triggered stops,
L2/queue/depth/spread/latency limitations remain explicitly modeled.

## Validation

Production and independent reference directions are filtered separately. The
engine-free verifier checks every trade and every held-position observation,
including entry/exit clock, sizing, fees, funding proxy, target cap, first exit,
cash, drawdown and halting. All actual trade directions must match the side rule.
This is self-audit, not third-party assurance. Path/candidate replay duplicates
are not independent samples. Historial positive net, if any, is not a guarantee
or established future win rate; fixed-same-trade higher costs are also disclosed.

One bounded workflow: targeted parent43 regression tests plus10 new tests, two
25min jobs and a combine job. The full672-test parent run is retained as evidence
for unchanged files, not claimed to have been rerun here. No dependencies, paid
service, scheduler, real/testnet orders, main writes, merge or deployment.
Artifacts expire after7days; do not call them permanent market archival.
