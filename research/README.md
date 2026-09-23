# Issue #1: bounded strategy research (ChatGPT + GitHub Actions)

This is a paper-only experiment, not an unattended trading bot. No wallet, private key,
account query, exchange order, package installation, paid data, external compute service,
schedule, deployment or real-money balance. The Actions token is read-only and is used
only by the pinned artifact-download action. The market client accepts only three exact
public Hyperliquid ETH `/info` request shapes, with verified TLS and bounded retries.

## Run identity and source

The original main commit `4cc3aa9239383a5a07e4f3305a9b2b17f61c9e57`, run `35452535157`,
artifact `10587127973`, and original dataset/engine hashes are explicit anchors.
`bootstrap.py` and all original application files are unchanged. The workflow downloads
the original artifact; expired/unavailable evidence is a failure, not an invented replacement.
New source is readable under `research/`. Each run stores code hashes, protocol, selection,
raw API responses, dataset hashes, every candidate result, CSV, tests and provenance.

## Two predeclared development rounds; no endless search

The two controls are original EMA9/21 and previously selected EMA16/36 (labelled post-selection).
Round 1 tests eight NEW entry filters: each EMA pair with 5m EMA3/8 or EMA4/12 trend-and-slope
confirmation, and with 20-close directional efficiency >=0.20 or >=0.35.
Round 2 tests four NEW breakouts: prior 20- or 40-bar range, with efficiency >=0.20 or >=0.35.
All 12 new candidates are frozen before the run; this is two hypothesis batches, not a claim
that the second batch was intelligently fitted to first-batch results.

Efficiency = abs(last close - close 20 periods ago) / sum(abs(consecutive close changes)).
A zero denominator gives zero. Higher-timeframe bars require five complete UTC-aligned
1-minute candles; incomplete groups are discarded. Trend filters restrict ENTRIES only;
opposite base EMA signals still close a position. A breakout requires the latest closed
price to newly exceed the PRIOR range, excluding the current bar. Its opposite breakout
signal closes the position. Signals only see the previous 120 completed 1-minute bars.

Original sizing, 1.8 target/stop ratio, ATR, costs, cash reserve, leverage model, one-position
limit, max-six-entries/24h, cooldown and permanent loss/drawdown halts are unchanged.
Stops still use model sample prices, not guaranteed stop prices. Costs and intraminute
paths remain explicitly assumed, not actual historical depth/executions.

## Selection and independent data check

The entire original dataset has already been seen: it is development/regression data.
Both controls and all candidates run across both costs and both paths (56 scenarios).
Controls must reproduce the old trades exactly. An independent verifier (no engine import)
checks original raw/normalized data and all 192 original scenarios, and checks new simulated
fills, entry signals, ATR, quantities, fees, funding, net profit and sampled drawdown.

The leader among NEW candidates is selected using maximum worst-case development net P&L,
then worst-case net win rate, then lexicographically smallest ID. It is an exploratory leader,
not automatically a qualifying strategy. `selection.json` is saved BEFORE downloading fresh
market data. Re-downloaded overlapping candles, rates and metadata must match. This is a
second download from the SAME API, not independent-source confirmation or a guarantee that
exchange data is true. Historical L2, exact oracle and point-in-time metadata remain unverified.

Only data strictly after the original dataset end is used for the new evaluation. Selected
candidate plus two controls run four scenarios each (12 more); controls do not reselect a
winner after seeing new data. New accounts start at the single predeclared evaluation boundary,
not after losses. Three reporting folds partition one continuous account, with state snapshots
and no resets. Trades are assigned once by close time; their sum is NOT a standalone fold return.
Compare marked-to-model equity snapshots to include positions spanning fold boundaries.

## Outcomes and uncertainty

A win is a CLOSED round trip with net P&L >0 after fees and funding. Zero-net trades remain in
the denominator. No trades means undefined win rate. Net return and >=5% return are separate.
Report Wilson 95% (independent Bernoulli assumption, unadjusted for selection) and a deterministic
1,000-resample day-block sensitivity when at least three calendar days exist. Few blocks are
not strong evidence; dependence, selection and non-stationarity remain. No probabilistic
claim that true future win rate >=55% or >=60% is made.

Base fills also receive a fixed-trade cost stress: keep original sizes, times and funding;
reprice at 3bp spread, 3bp adverse slippage and 0.09% fee per side. This is a counterfactual,
NOT a realistic new execution path and NOT the same as rerunning entry gates at higher costs.
The two stress reports remain separate. Research gate >=55% additionally requires positive
net, >=100 unique round trips per scenario and trades in three non-overlapping reporting folds;
scenario/path copies are never pooled. Passing these custom gates would still not establish
reliable income. >=60% observed in this tail is not a new independent post-55 confirmation.

The ordinary API window is short; no trade count or history is fabricated. A completed job can
legitimately have NEGATIVE, MIXED, NO_TRADES or INSUFFICIENT_SAMPLE results. Stop after these
finite hypotheses/data; no repeated dispatch until a lucky headline, no automatic merger.

## Evidence scope

`verify.py` independently checks data projections and arithmetic, NOT every possible missing
trade, queue event or exchange rule. Regression tests and baseline exact replay complement it.
Future profitability, exact historical fills and independent market-source validation remain
NOT VERIFIED even when the model-consistency checks pass.

Official references (checked during this work):
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
- https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data
- https://docs.github.com/en/actions/reference/limits
