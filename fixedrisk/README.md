# Fixed-risk position sizing on A44 and saved September

Issue #1, specification comment5797776342. Three predeclared comparisons only.
No orders, wallet, live trading, deployment, paid data, new market download or
schedule. Default Config, all previous strategies and dataset indexes unchanged.

## Profiles

- BASE_B: exact original V030_R30 / VOL account, original approximate10.10USDC
  target position and2x margin setting; SL=d, TP=1.8d.
- RISK125 (primary): same entry and exits, dynamic size at maximum1.25% planned
  risk; post-entry mark-proxy exposure at most2x; margin setting5x, NOT5x exposure.
- RISK125_TP_HALF: same risk sizing and SL, only target1.8d becomes0.9d.

Here d=max(.003,1.5*ATR15/signal_close); d>.01 rejects as before. All entry
Bollinger15m/lower-half close/0.30% minimum raw volatility/3x room rules remain.

Before entry, cash equals flat account equity. Let E=cash, P=known historical
peak equity and H=max(.95*P,9.50). Planned risk budget is
R=min(.0125*E,max(0,E-H-.001*E)). The .001*E headroom is predeclared, not tuned.

For modeled short entry price e, current modeled cover price x, mark m, fee f,
stop fraction d, and original roundtrip friction c, floor the minimum quantity:

1. R/[e*(d+c)] -- planned loss capacity;
2. 2*E/[m+2*(e*f+max(0,m-e))] -- post-entry mark exposure cap;
3. max(0,E-3.50)/[m/5+max(0,m-e)+f*(e+x)] -- margin, both fee reserves,
   initial mark loss and original3.50USDC cash reserve.

Floor to the saved ETH lot step. Below10USDC notional is refused, never rounded
up through risk limits. Tightening TP does not increase simultaneous quantity.
Planned loss is not a guaranteed maximum: future funding, gaps, depth and latency
are not bounded by this formula. Actual funding retains the previous-close proxy.

Keep the original5% peak-DD/initial-balance floor, three losses halt,900s cooldown,
6entries/24h and6h maximum hold. Never weaken target>=3*roundtrip-cost to force
trades. No martingale, equity reset within a continuous block or halt bypass.

## Dataset, model and reporting

A44: all44 dates /63,360 one-minute candles,27 genuinely contiguous blocks.
September: saved5,490 minute candles,one continuous block. Each block has600min
warmup. Combined68,850 input /16,800 warmup /52,050 execution-window minutes.
A44 net is the sum of27 separate virtual10USDC accounts. September is one
continuous10USDC account. Do not concatenate the two or divide A44 total by10.

3profiles x28blocks x2 original costs x2 OHLC/OLHC paths =336 cases, with112
exact original B account regressions. The remaining224 are new sizing/exit cases.
Parent artifact crossperiod-final-35683448454-1, commit
c65d35732046228bf250e5cedbd92577540e97c6, archive SHA256
ced6865fcd3281443e85fc9ca3595f7d8744d73514a870126aa800a1b522a58e.

No interpolation or improved-fill assumption in this batch: retain model
observations2/21/40/59.998s. First-crossing and latency experiments must be a
separate change, not mixed into this comparison. Neither path is a rigorous
execution bound. No historical mark/L2/queue/oracle reconstruction.

Report both periods separately, every loss/no-trade case, returns, costs,
position exposure, sum(net)/sum(planned_loss), drawdown, halt and rejections.
Fixed-trade legacy stress and extra1/2bp per side are distinct sensitivities;
only the original two cost models get full account replays.

## Verification and safety

Use the unchanged engine for BASE_B. New risk code uses existing exit and margin
observation machinery, with a restricted Config subclass. reference_fixed.py
separately declares size limits and reconstructs every permitted entry, refusal,
first exit, quantity, fees, funding, cash, peak/DD/halt and margin observation.
It derives from the earlier engine-external reference: self-verification, not
third-party certification. Repeated cost/path scenarios are not independent trades.

Load validates frozen parent/source hashes and raw September overlap. Missing
inputs, unverifiable zero-volume fills, invalid accounts or maintenance breaches
fail closed without a successful result headline. New tests use artificial data.
CI has read-only permissions, pinned actions, no installs, no persistent checkout
credentials, no socket/child processes during replay and a25min maximum job.
Evidence retains full parent inputs, source, protocol, logs, all trades/rejections
and results in an expiring artifact, not permanent storage.

Official background, not profitability evidence:
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl
