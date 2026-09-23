# Five-times margin and exposure, fixed A44 B strategy

User requested a 5x backtest on the 44 days, following the B result +1.09244
USDC. This is a size/risk experiment, not a new profitable-strategy claim.
Existing modules and default account configuration are not modified.

## Fixed experiment

Four modes x 27 real contiguous blocks x 2 costs x 2 OHLC paths = 432 cases.

- BASE: original B `VOL` / `V030_R30`, 2x margin setting, approximately10.10USDC
  target notional, 1.15x notional/equity cap,3.50 cash reserve,1.25% planned-loss cap.
- LEVERAGE5_SAME_SIZE: only margin setting becomes5. Original quantity and all
  original admission/risk limits remain. No profit multiplication is assumed.
- REQUEST5_ORIGINAL_LIMITS: compute the full near5x size below but retain original
  caps. Report every violated opening limit; zero trades are not profitability.
- EXPOSURE5_WHATIF: explicit enlarged-size paper experiment.5x margin setting;
  replaces fixed3.50 reserve with calculated fee reserve; notional/equity cap5;
  planned-loss cap6.25% (5 x original1.25%). This is NOT the old safe-risk policy.
  Do not promote it as an unchanged strategy or enable it for live orders.

All other policies remain: original15m Bollinger reentry short, lower-half red
close, raw1.5ATR/close >=0.003, entry-to-midband >=3 modeled roundtrip costs;
next1m modeled open, ATR stop floor0.3%/ceiling1%,1.8R conservatively capped TP,
360min maximum hold, single position,900s cooldown,6entries/24h,3loss halt,
5% peak-drawdown OR initial-equity-loss halt. Stops are not guaranteed prices.

At each eligible short event, let P be the current modeled mark proxy, E the
short fill price, X the current modeled cover price, f the per-side fee, C cash.
Full-size quantity is rounded DOWN to the saved ETH lot:

    q = floor_to_lot(C / [P/5 + max(0,P-E) + f*(E+X)])

Thus margin, entry mark loss, opening fee and estimated closing fee fit in cash.
Rounding/cost reserves make actual post-entry exposure slightly below5x. Trade
size is recomputed from that episode's current cash, never from fresh10USDC.
The original-limit comparison computes the same requested size but may reject it.
The enlarged version remains subject to its explicit6.25% planned-risk budget.

## Data and capital

Parent `entry44-final-35677804240-1`, artifact10673857849, commit
54e21b5c92185a9dd63d52f1f04619069c55cf0d, ZIP SHA256
ab8834f9c02aa482c4a48e4d8d553aeef31f835bd3b2f8a43351fae76f26c840.
All44 selected dates/63360 minute bars/58 input hashes remain; each of27 blocks
has600min warmup.47160 executable-window minutes before halt effects.
Separate10USDC experiment per block; no bridge across missing days. Sum/270 is
mean episode return, not sum/10 or a continuous44day compounded return.
The original108 B results must be reproduced. Previously viewed development data.

## Margin diagnostic and limitations

At every held-position model observation, compute account equity and maintenance
from saved maxLeverage25, i.e.2% for the small ETH positions. This is a fixed
historical-model assumption; metadata is not point-in-time margin-tier proof.
Record minimum equity-minus-maintenance and equity/notional. One-minute TRADE
OHLC substitutes for unavailable historical mark; no L2, order queue, latency,
exchange TP/SL, liquidation fills or exact oracle funding-price reconstruction.
Any maintenance breach is explicitly invalid for exact liquidation P&L; the
reference refuses an apparently verified profit result. Absence of breaches
only means none in the declared sampled proxy model, not live liquidation safety.

Official formula sources, not performance evidence:
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margin-tiers

## Execution and audit

One ordinary GitHub-hosted job <=25min; no schedule, market requests, wallets,
secrets, real/testnet orders, paid service, main change, merge or release.
Read-only Actions token restores only the fixed parent. Socket/DNS/subprocess
operations are denied in the research process; no installation is required.
Original source is restored from the existing checked bootstrap bundle.

`run_leverage.py --parent parent --out results` uses existing B signals with an
engine-external independent signal check and a separate sizing/account auditor.
The reference checks every filled trade, funding event, earliest eligible exit,
all flat-state entry opportunities, cash/drawdown/halts and margin diagnostics.
Artificial tests are software tests, not additional market samples.
Raw parent, source, all432 ledgers, aggregate/coverage and tests are preserved as
an expiring artifact. Repeated cost/path cases are not independent trades.
