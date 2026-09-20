# Mixed strategies — Issue #1, batch 4

Public mainnet ETH 1m data, virtual 10 USDC, **no real or testnet orders**.
This batch mixes the existing EMA/efficiency, range breakout, Elder-adapted
pullback and Connors-adapted RSI signals. These are original research hypotheses,
not claims that the authors recommended these combinations or ETH timeframes.

## Frozen experiment

Eight hybrid rules, six unchanged controls, two cost assumptions and two OHLC
paths. All 14 candidates run in the seen development window AND the new tail:
**56 + 56 = 112 scenarios**, not 112 independent datasets.
The primary candidate is fixed as `MIX_VOTE_2OF3` BEFORE examining new data.
There is no optimizer and no forced winner; a no-trade candidate is not called
best. All alternatives on the new tail are disclosed multiple comparisons, not
an untouched final test after choosing a winner.

Previous run: 35481147529, commit 7b9d97ca7e4f6dc97bec2876ba3633ff837cb431.
Known data SHA256: f68b63107e632e64c252e8df9bb050b911c12a702b6e8e111e82b56ef9572502.
All bars before **2026-09-20 01:25 UTC** have already been seen.
Only bars starting at/after that cutoff can be this batch's new evaluation.
Old and new windows are separate experiments, not a replenished income record.

## Components

Each index uses exactly its previous 120 CLOSED one-minute candles, including
the most recently closed candle. Complete, clock-aligned 5m groups are required.

- **E**: EMA16/36 cross, filtered by ER20 >= 0.20.
- **B**: new close crossing the previous 20-bar high/low, excluding the current
  bar from the range, ER20 >= 0.20. Same component as prior BREAK_20_0.20.
- **P**: Elder adaptation EMA3/8 on completed 5m closes. Positive trend requires
  fast>slow and fast rising; negative requires fast<slow and fast falling.
  In that direction, a prior-three-bar stochastic5 extreme (<=30 or >=70) and
  a latest close beyond the preceding bar's high/low trigger an entry.
- **R**: prior Connors adaptation SMA60 on 1m closes, Wilder RSI2 on completed
  5m closes, thresholds <=10 / >=90; also on the opposite side of 5m SMA5.
  It is evaluated only when the latest closed minute completes a 5m group.
- **ER20** = abs(Ct-Ct-20) / sum(abs(one-minute close changes), 20 changes).
  Zero denominator gives zero. It is a direction-efficiency filter, not accuracy.

## Eight combinations

| ID suffix | Entry | Additional discretionary-model exit beyond the original risk exits |
|---|---|---|
| BREAK_TREND | B agrees with completed 5m EMA3/8 trend | Opposite new entry |
| PULLBACK_EFF | P with ER20 >=0.20 | Opposite entry, opposite 5m trend, or stochastic5 >=80 for long / <=20 for short |
| UNION_VETO | Any current E/B/P/R, but no opposing nonzero component | Opposite entry |
| VOTE_2OF3 | E/B/P latest nonzero family events within current/prior 2 minutes; >=2 same-direction families; any opposing vote vetoes | Opposite new consensus |
| WEIGHTED_VOTE | E/B/P/R weights 1/1/2/1, same 3-bar validity; total >=3 and no opposing vote | Opposite new consensus |
| REGIME_SWITCH | ER>=0.35: B with trend; ER<=0.20: R; middle: P | Opposite routed entry |
| SEQUENCE | Prior-three-bar stochastic extreme then close beyond prior THREE-bar range, matching 5m trend and ER>=0.20 | Same additional exits as PULLBACK_EFF |
| UNION_DUAL_EXIT | Identical entries to UNION_VETO | Opposite entry OR adverse 5m trend OR stochastic strength as above |

Votes count each family ONCE. A family's newest nonzero signal supersedes its
older one. Signals older than 120,000ms relative to the latest closed candle
expire. Vote/weighted consensus is edge-triggered: the same consensus on the
next minute is not a fresh entry. No delayed entry is backfilled during cooldown.
These correlated indicators are NOT independent statistical witnesses.

All entries are simulated at the next bar's assumed +2s open quote. Original
stop/target, fees, funding, quantity rounding, 10 USDC starting balance, single
position, max 6 entries/24h, 15m cooldown and permanent halt protections remain.
A mixture does NOT create one account or extra buying power per component.
The dual-exit variant changes exit logic, not risk limits. Separate controls
retain the exact old entry/exit behavior, including unfiltered opposite EMA exits.

## Evidence and failure handling

The workflow verifies prior source/raw/normalized/ledger records, recomputes
causal component and mixed decisions with a separate reference implementation,
then independently checks each new trade's entry, signal-exit eligibility,
sizing, costs, funding, net P&L and sampled drawdown. Signal trace records show
components, retained votes, efficiency, trend and regime route on every entry.
This does not prove tick-perfect exchange execution or all unobserved data.

Current raw responses, hashes, protocol, frozen candidates, source, every result,
fixed-same-trade cost stress and failures are retained in the run artifact.
Gated cost reruns can change the trades; fixed-trade cost stress is a different
counterfactual. All no-trade and negative results are retained.

Net win rate = count(net > 0) / all closed round trips, with ties in the denominator.
No trades is undefined. +5% equity return is separate from 55% wins.
The existing >=100 distinct trades per case, 3 reporting folds with trades,
net-positive and >=55% base/stress gates remain. Wilson IID and day-block
sensitivity are retained. A supplementary Bonferroni Wilson interval accounts
for 56 candidate/cost/path cells only under the IID assumption; it does not fix
serial dependence, repeated research, nonstationarity or a short market period.
The 60% independent stage requires subsequent unseen data after a 55% decision.

One bounded Actions job, max25m, no schedule, no paid data/service, no secrets,
no environment dump, no wallet API and no automatic merge or deployment.
The only market network client remains the existing exact `/info` allowlist.
Output must be a new directory; no stale positive summary is reused.

## Reproduction

After `python3 bootstrap.py` in a fresh checkout, restore the prior artifact to
`previous-evidence`, set PYTHONPATH to `application:application/tests:research:book_research:mix_research`,
and run `python3 mix_research/run_mix.py --previous previous-evidence --application application --out evidence/mix`.
The GitHub workflow handles these steps. Local unit tests use labelled artificial
fixtures only and must never be reported as market performance.

## Sources and limitations

The old book source descriptions remain in `book_research/README.md`.
https://scholarworks.wmich.edu/math_pubs/42/ — backtest overfitting / selection risk;
this batch does not claim to implement the paper's full PBO method.
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint — market data API.
The existing cost rates are fixed scenario assumptions, not a new fee quotation.
OHLC path, depth, spread/slippage, funding-price proxy, metadata and short-sample
limitations are unchanged. Same-API overlap checks are not a second market source.
