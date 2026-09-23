# Tight SL/TP on the fixed A44 B strategy

Issue #1: user's requested tighter stop-loss and take-profit research only.
No live/testnet trading, website, main change, merge, deployment, schedule,
new data request or paid service. Existing Config and strategy sources unchanged.

## Frozen definitions

Keep `VOL / V030_R30`: closed15m Bollinger short re-entry, lower-half red
close, raw volatility>=0.30%, frozen mid-band room>=3x modeled cost.
Use the same 1m OHLC/OLHC execution models, all44 dates/63,360 candles,
27 genuinely contiguous blocks,600min warmup each/47,160min execution window.
Each block has a separate virtual10USDC account. Sum net/270 is mean block
return, NOT one10USDC account's continuous44-day return.

Let `d=max(.003,1.5*ATR15/signal_close)`. Original `d>.01` still rejects.
Distances below are fractions of modeled entry price, not account equity.

| Profile | Stop distance | Target distance |
| --- | ---: | ---: |
| BASE | d | 1.8d |
| SL_HALF | .5d | 1.8d |
| TP_HALF | d | .9d |
| BOTH_HALF (primary) | .5d | .9d |
| BOTH_QUARTER | .25d | .45d |

Stop floor, ATR multiplier and stop ceiling scale together. The price move
ceiling is therefore still the same original1% eligibility test. TP_HALF
explicitly uses0.9R; this is a pinned research variant, not the default Config.

All5/10/20x sizing, fee reserves, prior higher-risk budgets(.0125*L),
1m observations, target favorable-fill cap,6h cap,15min cooldown,6entries/24h,
three-loss halt,5% account drawdown/initial-balance floor remain unchanged.
Tightening stops does NOT increase simultaneous entry quantity.
Recompute target>=3x roundtrip friction. Preserve refusals and no-trades;
never weaken this gate just to force shorter targets to trade.

## Scope and result integrity

5profiles x3levels x27blocks x2costs x2paths =1,620 cases:324 exact parent
exit regressions and1,296 new exit scenarios. No additional tuning afterwards.
5/10/20x entries are paper risk scenarios, not recommendations.
Restore parent `leverage1020-final-35798881696-1`, revision
`9d7a8de3993c8c8c7af48d4da83d4604ff867b37`.
Archive SHA256: `7681786ac79292ddf828d814b7815b118601167e95665a7ea087d53cc20cca17`.

`exit_config.py` only provides restricted configurations to the unchanged
parent execution engine. `reference_tight.py` independently declares the
factors and rechecks entries, stop/target, earliest exit, costs, funding,
quantity, cash/drawdown/halt and maintenance proxy on all observations.
BASE profiles use the unchanged parent reference and must reproduce all
324 parent accounts before results qualify as complete.
Reference reuse is disclosed; this is self-verification, not third-party
certification. Simulated path repeats are not independent market samples.

Every failure produces status/evidence, not a profitability headline.
The runner refuses an existing output directory, incomplete parent/grid,
source drift, inconsistent accounts or maintenance breach.
The workflow has a25-minute cap, pinned action SHAs, read-only permissions,
no dependency installs, non-persistent checkout credentials and socket/process
audit guards during replay. All parent evidence and outputs are retained in
an expiring artifact, not described as permanent storage.

## Limitations

Trade OHLC is not historical mark/L2. Tight exits can amplify sensitivity to
unobserved intraminute moves. OHLC and OLHC are scenarios, not rigorous bounds.
Funding uses a preceding-close payment-price proxy. Actual fee tier, queue,
latency, exact TP/SL triggering and liquidation remain NOT VERIFIED.
Official TP/SL uses mark price; model consistency cannot establish live fills.

https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations
