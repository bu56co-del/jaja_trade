# 經典書啟發的研究批次 / books-v1

Refs Issue #1。沿用 PR #2；新增有界批次，不改原 engine／replay／風險設定、不合併、不落單、不新增付費服務。

## 來源，而不是收益保證

只查閱以下公開作者／出版社介紹及公開研究，沒有聲稱讀完付費全書。以下8個假設是自行改編到 Hyperliquid ETH 的規則，**不是書籍原版策略或作者背書**。

- **Alexander Elder, The New Trading for a Living**：風險、紀律與交易記錄。作者的 Triple Screen 說明使用至少兩個時間尺度及多個篩選。來源：<https://www.elder.com/product-page/the-new-trading-for-a-living>；<https://www.spiketrade.com/shop/product/detail/R_PC44/>。
- **Larry Connors / Cesar Alvarez, Short Term Trading Strategies That Work**：短期回調／均值回歸。官方書籍頁：<https://store.tradingmarkets.com/products/short-term-trading-strategies-that-work-a-quantified-guide-to-trading-stocks-and-etfs-downloadable-pdf>。公開RSI研究及反彈離場：<https://tradingmarkets.com/recent/the_improved_r2_strategy_84_correct_with_just_6_rules_-674361>；<https://tradingmarkets.com/recent/locking_in_gains_in_etf_powerratings_trades_moving_averages_and_the_rsi-764360>。
- **Mark Douglas, Trading in the Zone**：出版社介紹的機率、風險與一致性，落實成事前凍結規則及保存失敗。<https://www.penguinrandomhouse.com/books/350665/trading-in-the-zone-by-mark-douglas/>。沒有量測心理狀態，不能宣稱改善了人的交易心理或因此增加勝率。

查閱日期：2026-09-20。股票／日線研究中的成功率不適用於本次ETH模擬；沒有抄入書籍章節。

## 固定8個新候選

所有決策只看**之前已收市120根1m**；5m分組須含完整、時鐘對齊的5根，缺分鐘就不能聚合。每次從該120根的完整分組重算指標，種子及時間窗明確固定。不是交易平台延續全歷史的指標。

### 4個 Connors-inspired 改編

參數為1m SMA長度60／100 × 5m RSI2閾值5／10。

做多：只在一根完整5m剛收市後、最新1m收市>SMA60或100，5m RSI2≤閾值，最新5m收市<最近5個5m收市均值。做空：最新1m收市<SMA，5m RSI2≥100−閾值，5m收市>其SMA5。不在同一5m未收市期間重複入場訊號。

做多訊號離場：完整5m RSI2>70，或5m收市>SMA5；做空對稱RSI2<30或收市<SMA5。使用2個收市變動的平均漲／跌作RSI種子，之後每次 `(舊平均+新變動)/2`，RSI=`100*平均漲/(平均漲+平均跌)`；全零=50。

**本研究的60／100分鐘SMA不是原方法的200日SMA；5m不是日線；雙向交易、下一分鐘執行及硬止損亦是改編。沒有假稱重現R2或原書的研究。**

### 4個 Elder-inspired 改編

參數為完整5m EMA3/8或4/12 × 1m stochastic %K5回調閾值20／30。

做多：完整5m快EMA>慢EMA且快EMA上升，之前3根1m中至少一根%K5≤閾值，最新1m收市>前一根最高價。做空對稱；%K5≥100−閾值，收市<前一根最低價。

%K5=`100*(close-low5)/(high5-low5)`；零區間=50。多倉訊號離場：最新%K5≥80或較長趨勢轉為下跌；空倉%K5≤20或較長趨勢轉為上升。中性趨勢不被冒充反向趨勢。

**是多時段、回調、確認的自行量化，不是完整Triple Screen原版。**

## 保留的風險與對照

原引擎的止損、1.8R目標、90分鐘上限與帳戶保護優先於新訊號離場。本金10 USDC，目標單額10.10，至少10最低單額，2x虛擬保證金但名義額不超過餘額1.15倍、預留3.50、單筆預估風險1.25%、同時一倉、每24小時最多6次、冷靜15分鐘、連敗3次／回撤5%永久停機。沒有daily reset、加槓桿、倍注、取消止損或刪除輸單。

3個已看過結果的對照：原EMA9/21、EMA16/36、上輪EFF16/36≥0.20，全部保留原行為。

## 不偷看新結果的比較方式

原run和上批的所有行情至**2026-09-19 16:39:59.999 UTC**都屬已見開發資料；尤其之前59分鐘的評估不能重新命名成新holdout。

先對8個新候選＋3個對照跑2成本×2路徑，共44個開發情境。另計基本成本成交紀錄的固定交易成本壓力（不重跑訊號，不能與新成本重新入場混稱）。

排名在新下載前固定：只在8個新候選中，取4個gated情境及2個固定交易壓力情境之最差淨利最高者，再比較最差gated淨勝率；0交易勝率以−1作排名；最後依ID字典序。即使最好者仍蝕錢或0交易，也只叫探索性候選，絕不宣稱合格。

先寫protocol及selection，再下載新行情，只對已知截止之後的新尾段測**选定候選＋3個對照**，共16情境。總共60情境，不是60個獨立策略／資料樣本。新尾段每情境由獨立10 USDC開始，不是舊戶口接續，不在尾段或3個報告分區間補本金／重置連敗。沒有根據尾段換另一個贏家。

結果列淨勝率、wins／losses／flat、淨回報、是否+5%、均值損益、profit factor、取樣回撤、停機與曝險、95% Wilson（獨立二項假設且未校正選擇）、按日分組重抽樣。至少100筆不重複樣本外交易及3個有交易報告分區的研究閘保留；即使通過也不是未來勝率保證。全成本／路徑及固定交易壓力也要滿足正淨利及55%門檻。60%獨立階段需55%決策之後的新數據。

沒有交易為`NO_TRADES`／勝率undefined；失敗不冒充負回報；樣本不足即使高胜率仍`INSUFFICIENT_SAMPLE`。不保證本批一定有正結果。

## 核對與限制

`book_verify.py`不import新策略或engine，另寫RSI／stochastic／方向及訊號離場公式，再利用既有獨立核算器核對成交、數量、ATR、兩邊手續費、funding、盈虧、取樣回撤。舊來源、原始回應及192+68舊情境重新核對；新下載與已知資料重疊處逐欄比較。Hash證明一致性，不是市場事實保證。

精確歷史L2、撮合路徑、排隊、oracle及歷史市場規格仍未重建；5,000根官方1m只有約3.47日，與保存資料合併亦不會變成一年。所有入場用下一分鐘第2秒可取得開價的假設，差價／滑價／深度仍為模型；不收取真錢。

## 執行及證據

由專屬 `.github/workflows/book-research.yml` 在 PR 分支執行。25分鐘job上限；沒有schedule或無限重試。原source從已核驗bundle解出，沒有套件安裝、錢包、API key或付費AI服務；GitHub權限僅contents/read及actions/read。

Artifact保存舊證據、新完整source、raw、protocol、selection、dataset、全部結果、每筆交易、CSV、校驗manifest及測試log。失敗／中斷保留中途文件；不覆寫舊實驗。這批不是部署交易機械人。
