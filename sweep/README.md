# Finite exhaustive hybrid sweep — Issue #1

This batch expands the previous eight hand-written examples into a **fully enumerated finite grammar**.
It does not pretend to test every imaginable strategy, arbitrary real-valued weight, timeframe, asset or risk policy.

## Exact coverage

Four existing component events: E=EMA16/36+ER20, B=20-bar breakout+ER20,
P=Elder-adapted pullback, R=Connors-adapted RSI. Component formulas are unchanged
in `mix_research/hybrids.py` and independently checked by `verify_mix.Reference`.

1. All subsets of size 2/3/4. Equal weights and every single-component double weight.
   All integer thresholds from 1 through total weight. Opposing-vote veto or signed-net-vote difference.
   All ordered 2/3/4-component priority lists (first nonzero wins).
   The 324 spellings collapse to **178 distinct truth tables**, tested on all 81 four-component direction states.
2. Each table: signal horizon 1 or 3 closed bars; filter none / matching completed 5m trend /
   ER20 >= 0.20; exit opposite entry / opposite entry plus adverse trend or stochastic strength.
   **178 × 2 × 3 × 2 = 2,136** definitions.
3. All **12 ordered pairs**: strict prior setup A, then current confirmation B, latest nonzero A within
   1 or 3 previous bars must agree; same 3 filters and 2 exits: **144**.
4. All **24 ordered assignments of three different components** to high (ER>=.35), middle,
   low (ER<=.20) regimes; same filters/exits: **144**.

Total **2,424**. Four equal existing `MIX_UNION_VETO`, `MIX_UNION_DUAL_EXIT`, `MIX_VOTE_2OF3`,
`MIX_WEIGHTED_VOTE` definitions are identified as already tried, not counted as new.
**2,420 new candidates plus all 8 previous mixtures and 6 single-strategy controls = 2,434**.
Each gets two cost assumptions × two intraminute paths × seen/new windows = **19,472 mapped scenario results**.
`universe.json`, `candidates.json` and `truth-table-aliases.json` document every item.

## Semantics and safeguards

One virtual 10 USDC account per scenario, not one account per component. Same original engine,
ATR stop/target, maximum position, fees, funding, cooldown, daily entry cap and permanent loss halt.
No real or testnet orders, accounts, API keys, signing, paid feeds, package installation or scheduled work.
No change to the original engine. No merge/deployment is implied by a successful batch.

Horizon 1 consumes the current closed-bar event. Horizon 3 retains the latest nonzero event per family
from current/prior two bars, expires by actual elapsed time, and only emits when raw consensus changes.
Filters apply after that consensus trigger. Sequence setups are strictly earlier than confirmation;
latest opposite setup vetoes older matching setups. A family's multiple events never produce extra votes.
The dual exit closes long on adverse 5m trend or stochastic>=80, short on adverse trend or stochastic<=20,
in addition to opposite-entry/risk exits. Each policy depends only on completed history.

Previous data through **2026-09-20 06:18:59.999 UTC** is already seen. Only later minutes are new.
All candidates/protocol/source hashes are frozen before public data is requested. No optimizer,
forced winner or early stop on reaching a nice score. All alternatives are multiple-comparison
exploration, not an untouched final validation after choosing the best.

## Exact computation sharing, not inflated test counts

Two candidates whose complete entry and long/short exit streams are byte-identical within the same
window can share one run of the unchanged original engine for that cost/path. This is an execution
optimization, **not a claim that the strategies are universally equivalent**. Every candidate has its
own mapping/result row. `actual_core_replays` counts real engine calls; `candidate_scenario_results`
counts mapped results. Shared/replayed trades are not independent observations or extra capital.

Each causal stream is separately computed by the independent reference policy implementation.
Every unique core ledger is independently recalculated, including entry prices, quantity, ATR,
fees, funding, net P&L and sampled drawdown. The cached signals and quotes are tested against
uncached original-engine execution; no candle/tick skipping changes the path.

## Execution and evidence

One workflow: prepare + 8 standard hosted shards + aggregate. Each job has a 25-minute ceiling.
Missing/mismatched/failed shards make aggregation fail, not return zero profit or a partial success.
Outputs use new directories. Only read permissions; no persisted checkout credentials or secrets dump.
The prepared artifact contains raw responses, normalized data, frozen universe, policy checks,
prior evidence, source and all test logs. Eight shard artifacts retain every exact ledger and status.
The final artifact contains CSV mappings, all candidate summaries, compressed core ledgers and manifest.

55/60 net win rates and positive net P&L are reported separately from equity return and +5%.
Existing sample gates require 100 distinct trades per case and three active reporting folds, positive
net/55% under cost reruns and fixed-same-trade stress. Wilson and day-block sensitivity remain.
A familywise Wilson display covers 2,434×4 new-case comparisons under IID only: it does not fix serial
correlation, previous rounds of research or changing market regimes. Subsequent independent 60%
confirmation needs later unseen data. No result proves future income.

Spread, slippage, depth, OHLC order, historical metadata and funding-price proxies retain prior limitations.
Same-API overlap checks are not an independent second market-data source.
Sources: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
and https://scholarworks.wmich.edu/math_pubs/42/ (selection/overfitting risk; not a claim to implement full PBO).
