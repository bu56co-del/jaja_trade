# A44 entry quality: low volatility and 1m confirmation

Four fixed comparisons approved in Issue #1. Primary is VOL, not whichever wins.
This is retrospective paper research on the SAME A44 data, not live income.

| Definition | Change from BB15_NONE_ROOM_LOWER_HALF |
|---|---|
| BASE | None; all108 core account cases must match run35602620940 exactly |
| VOL | Require 1.5*ATR15/close15 >=0.003 at the original setup; equality passes |
| MICRO | Wait for a subsequently closed1m bearish break; rules below |
| VOL_MICRO | Apply VOL before creating the MICRO event |

The original setup is short only: previous15m close outside its upperBB20,
latest close inside both bands, red body and close in its lower half. Modeled
entry-to-frozen-SMA20 space must be >=3 modeled roundtrip costs. No extra EMA,
4h filter, 0.618/0.66, long orders, enlarged risk or altered exit is introduced.
VOL's0.003 is the existing stop floor, not a claim about an optimal market level.

## Exact MICRO semantics

At the original next1m open observation, the cost-qualified original signal
creates an event rather than an order. Freeze its15m high, ATR and SMA20.
Look at the next up-to-five completed1m candles, never the setup15m candle.
Cancel first if a waiting close is ABOVE the frozen15m high (equality does not
cancel). Otherwise a red candle with close STRICTLY below the preceding1m low
confirms. The fifth candle can confirm; a sixth cannot. Emit at the NEXT1m
modeled open+2s, never retroactively at the confirmation low/close.

Recheck original ROOM against that new assumed fill and the frozen mean. A
failed room check consumes the event, as does confirmation followed by account
rejection. Never wait for another confirmation to bypass cooldown/risk. At the
end of a block an unconfirmed event is cancelled, not executed. Process an
active event before another setup and ignore overlap until it ends. Real setups
are15m apart, while pending time is at most5m.

ATR stays at its setup value. At actual entry, the latest closed1m price is the
original engine's signal_close for sizing/stop-distance calculation. VOL is a
setup qualification only; there is no undisclosed second VOL filter at delayed
entry. The original stop floor/ceiling still applies at execution. Preserved
hard exits precede all other handling and are observed on1m modeled paths.

Potential event traces include times when an account is holding or halted;
these are signal diagnostics, NOT executable orders or extra trade samples.

## Dataset, capital and costs

Restore parent artifact `refine44-final-35602620940-1`, run35602620940, commit
c186c5dff4b9ff4ca0be6903b1b047bafac76268. Artifact10639710297 has ZIP SHA256
ab4a681046220dabf6396e5a8cbd77efac462b185f5c2ed9ade39d9a8058d701.
All58 indexed inputs are verified by the unchanged parent loader:44 days,
63,3601m candles,27 genuinely contiguous episodes. Each uses600min warmup;
47,160min execution windows before account halts. All44 dates remain, including
August. No gap bridging, date exclusion, new market downloads or forward fills.

Each episode separately starts with virtual10USDC; no reset within an episode.
Total net is the sum of27 distinct experiments, NOT a continuous10USDC account.
Total net/270 is mean episode return;270 is not a request to deposit money.
Four definitions x27 episodes x2 costs x2 price paths =432 cases.

Retain the existing position/notional/margin limits, <=1.25% planned account
risk including friction, original0.3%-1% stop interval,1.8R capped target,360min
max holding,15min cooldown,6 entries/24h,3 consecutive-loss and5% drawdown halts.
Base scenarios:0.045% fee per side,1bp full spread,1bp slippage per side.
Stress:0.09%,3bp,3bp. These are unchanged simulation assumptions, not an updated
exchange fee quotation. Funding retains original timestamps and the preceding
minute close as a labelled oracle proxy. Same-trade cost stress and higher-cost
full replays remain separate. Both OHLC/OLHC paths are retained, not combined
as independent trades or presented as proven price bounds.

## Evidence and operation

One bounded Actions job, <=25min; no schedule, wallet, private API, paid service,
orders, main update, merge or deployment. The research process disables sockets.
No external strategy bot or new dependency is downloaded/executed. Source and
unit tests are preserved, including failures. The output directory must be new.

The production minute-state machine is checked against a separate per-event
reference scan, which imports no production entry code or engine. Parent BB and
ROOM calculations also have their existing independent reference. Every executed
trade is audited with the existing engine-external ledger and first-risk-exit
checker, including fees, sizing, funding, stop/target, cash, drawdown and halt.
All108 BASE accounts must reproduce the parent core fields. Unit tests are
artificial, not profit evidence. Result hashes,432 case records,16 aggregate
rows,44-day coverage and all potential-entry outcomes are kept in the artifact.

After bootstrap.py, set PYTHONPATH to application, application/tests, longer,
execution, reversal44, alternatives44, mean44, direction44, refine44, entry44.
Run: python entry44/run_entry.py --parent parent --out results
The workflow restores the exact parent. Artifacts expire; no permanent archive
is claimed. Limits remain: selected/disconnected development dates, no actual
L2, mark-trigger reconstruction, oracle payment prices, queue or latency. No
observed sample win rate is described as guaranteed future income.
