# A44: 10倍及20倍實際曝險，5倍原樣對照

此為離線假資金研究，不是交易策略升級或實盤指令。沿用B版VOL/V030_R30、原44日期與15分鐘訊號／1分鐘執行。原leverage44、所有舊策略及Config均不改。

## 固定規格

三組 `EXPOSURE5_WHATIF`、`EXPOSURE10_WHATIF`、`EXPOSURE20_WHATIF`，各27個真實連續片段、兩種成本及兩種OHLC路徑，共324情境；其中108個5倍為父結果回歸，216個為新10/20倍情境。所有成本和OHLC副本不算獨立市場样本。

父來源為run35744998756、commit3b7569b56f5aaf496e41c96004ec9a2064aa4718、artifact10702622233。ZIP SHA256: dd9373317fc18ba1bd573be3cb5c08bc69dba7bc7f9a0d2c5ccc8058da3d1fe0。

每個片段使用独立虛擬10USDC，不在片段內補本金或重啟；總淨利除270只是平均片段回報，不是連續44日／單一10USDC收益。44日63,360根1m，每段600分鐘預熱，47,160分鐘執行窗口，仍受原停機規則限制。

## 倉位政策不是原低風險設定

L只可取5/10/20。按當時現金、費用、mark損失與ETH lot向下取整：

`qty = floor_to_lot(cash / [mark/L + max(0, mark-entry_fill) + fee*(entry_fill+cover_fill)])`

費用及取整令實際曝險略低於指定L。跨倉單一ETH空倉；无其他抵押或持倉，非portfolio margin。margin與真實清算仍只按保存資料和明示代理假設。

與5倍what-if相同方式擴展：notional/equity上限=L；按費用/mark損失預留，不是原固定3.50USDC；計劃單筆風險上限=.0125×L，分別6.25%、12.5%、25%。這是使用者指定的高曝險假設，不是推薦風險，不會寫入原Config。

保留原0.3%–1% ATR止損、1.8R保守封頂止賺、6小時持倉上限、15分鐘冷靜期、每24小時6次開倉、單倉、三連敗與5%帳戶回撤／本金底線停機。5%為觸發值，不是保證最大虧損。止損、成本、funding前收市代理及入場全部不改。

## 清算證據界線

每個持倉取樣先核查權益減維持保證金；維持率沿用保存ETH maxLeverage25的2%小額級別假設，並不隨所選L改變。若模型取樣越界，參考驗算拒絕正常有效收益結論，不補造清算成交；原始失敗證據仍保存。沒有越界也只能稱模型未見，不能聲稱實盤不爆倉。

缺歷史mark、oracle、L2、排隊與延遲，精確清算NOT VERIFIED。OHLC／OLHC不是保證的上下界。公式來源：
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations
https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margin-tiers

## 安全、測試與交付

只下載已保存GitHub artifact，純標準庫、無新行情、套件、付費服務、排程、錢包、API key、實盤或testnet訂單。執行進程禁止socket與子進程，Actions只讀權限，單次25分鐘上限。

`risk_high.py`繼承原引擎的退出與margin診斷，只擴展三個固定配置及開倉數量。`reference_high.py`不用交易引擎／生產數量程式，逐交易及逐觀測點核對進出、首次退出、費用、funding、cash、DD、halt、margin與所有應入場時點。這是自行驗算，不是第三方認證。

先驗證31項新人工fixture測試和原349項相關測試；再完整回測324情境、逐欄重現108個5倍對照，保存全部正負／零交易結果、CSV、來源／輸入hash及protocol。失敗沒有成功headline，不覆寫舊输出。Artifacts保存7日，不是永久資料庫。
