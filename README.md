# jaja_trade — public-data historical paper backtest

Runs the previously delivered **reviewed Colab v2** application on a bounded GitHub-hosted Ubuntu runner. This is software/strategy validation, not a continuously hosted trading bot.

**No deposit, wallet, private key, API key, real orders, paid-data service, package installation, or deployment.** Only public Hyperliquid `/info` requests are made by the backtest. GitHub actions have `contents: read`; checkout does not persist credentials. There is no schedule.

## Experiment

Default: mainnet ETH perpetual 1-minute data; virtual 10 USDC. The same 12 EMA candidates, two cost assumptions, two intraminute paths and four independent time windows produce 192 scenarios. The headline refers to the **original EMA9/21 long-and-short** candidate across all four cost/path assumptions, not the best candidate selected afterwards. POSITIVE / NEGATIVE / MIXED / NO_TRADES / INVALID_MODEL remain distinct from data or workflow failures.

The ordinary data endpoint supplies at most 5,000 recent candles (about 3.47 days at one-minute resolution). Funding rates are historical but missing oracle prices use a preceding-close proxy. Spread, depth, intraminute execution paths and slippage are assumptions. **No future profitability or subscription income is established.**

## Source preservation

`bundle/part*.b64` contains compressed, checksummed application source from reviewed Colab v2, not executable downloads from an outside site. Run `python3 bootstrap.py` once in a fresh checkout to obtain the readable `application/` source. All 20 file hashes and the whole decompressed payload hash must match before execution. Original strategy SHA-256: `abe98cf655254613ba9462049122579e1fb26aa3343c844278f129acaa88aba1`.

The workflow first verifies source and runs the existing 210 software tests (artificial fixtures are tests, not performance evidence), then fetches actual market data and runs the full historical experiment. Job summaries and the `backtest-<run>-<attempt>` artifact include outcome, exact commit/run identity, raw public data, results, per-trade records, source manifest, readable source and logs. Artifacts are retained for 14 days.

Use the workflow's manual Run workflow control for another explicitly selected mainnet/testnet experiment. Changes to this workflow or `RUN_BACKTEST` on `main` also trigger a bounded run. No automatic network fallback, account reset or strategy tuning.

## Sources

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow
