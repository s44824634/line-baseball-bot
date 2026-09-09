# LINE 棒球賽前分析機器人 ⚾

仿 Capafy「Otata Baseball Analysis」的 LINE@ 棒球預測機器人。
輸入對戰組合，回傳五段完整賽前分析：勝率、預期得分、比分分布、
前 5 局走勢、先發投手、球隊狀態、比賽條件與分析信心。

> ⚠ 僅提供棒球賽事分析，不含任何投注或博弈建議。

## 支援賽事與資料來源

| 賽事 | 資料來源 | 完整度 |
|---|---|---|
| **MLB 美職** | MLB 官方 Stats API（免費免金鑰）＋ Open-Meteo 天氣 | ⭐⭐⭐ 最完整：真實得失分、先發 ERA、牛棚 ERA、近10場 |
| **CPBL 中職** | CPBL 官網 JSON API（含 CSRF）＋ Open-Meteo | ⭐⭐⭐ 真實得失分、先發投手、主客場拆分、近10場 |
| **NPB 日職** | Yahoo! Japan プロ野球（賽程/先発）＋ Wikipedia（戰績） | ⭐⭐ 先發投手姓名有；得失分由勝率估算 |
| **KBO 韓職** | Wikipedia（戰績）；KBO 官網（部分地區封鎖） | ⭐⭐ 得失分估算；官網連線失敗時自動降級 |
| **WBC 經典賽** | 賽期偵測（3 月） | 非賽期回傳賽事資訊 |
| **12強 Premier12** | 賽期偵測（11 月） | 非賽期回傳賽事資訊 |

### 聯盟專屬賽制（模型自動調整）

- **和局制度**：NPB／CPBL 例行賽 12 局上限和局、KBO 無上限可和局 →
  報告列出和局機率；MLB 無和局 → 延長賽機率併入勝率
- **得分環境**：CPBL 由當季實際資料動態計算聯盟平均；各聯盟獨立參數
- **NPB／KBO 得失分**：無公開 API，由勝率經 Pythagorean 公式反推，
  報告中標示並調降信心等級

## 使用方式

| 輸入 | 回應 |
|---|---|
| `道奇 vs 洋基` | 該場五段完整賽前分析 |
| `統一 vs 兄弟` | 自動判斷 CPBL |
| `巨人 vs 阪神` | 自動判斷 NPB |
| `三星 vs LG` | 自動判斷 KBO |
| `分析最近即將開打的 MLB 比賽` | 近期賽程清單 |
| `最近 中職 比賽` | CPBL 近期賽程 |
| `WBC` / `12強` | 國際賽事資訊 |
| `help` | 使用說明 |

### 每份報告的五段內容

1. **全場賽果與預期得分**：勝率（含和局）、預期得分、80% 總分區間、兩隊 80% 得分區間
2. **比分分布與得分節奏**：最大機率完賽比分、高機率比分帶、低/中/高總分情境
3. **前 5 局與先發投手**：前5局領先/平手機率、前5局最大機率比分、先發投手本季數據
4. **打線、牛棚與球隊狀態**：先發打線確認狀態、牛棚 ERA、近期戰績、主客場戰績
5. **比賽條件與分析信心**：球場/開賽時間/屋頂/天氣、信心等級（依資料完整度）、
   比賽波動性、資料來源與擷取時間

信心等級反映**官方賽前資料的完整性**，不代表結果一定發生。

## 本機開發

```bash
cd line-baseball-bot
python -m venv .venv
.venv\Scripts\activate          # Windows；macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # 填入 LINE 憑證
uvicorn app.main:app --reload --port 8000
```

LINE webhook 要求公開 HTTPS，本機用 ngrok 隧道：

```bash
ngrok http 8000
# 取得 https://xxxx.ngrok-free.app，加上 /callback 設到 LINE Console
```

## LINE@ 設定步驟

1. 到 [LINE Developers Console](https://developers.line.biz/console/) 登入
2. 建立 Provider → Create a new channel → **Messaging API**
3. 取得 `Channel secret` 與 `Channel access token`（long-lived）填入 `.env`
4. 設定 **Webhook URL** = `https://你的網址/callback`、開啟 **Use webhook**、
   關閉 **Auto-reply messages**，點 **Verify** 應顯示 Success
5. 加官方帳號好友，輸入 `統一 vs 兄弟` 測試

## 正式部署

需要常駐 + HTTPS：Render / Railway / Fly.io 免費方案即可，
或用 VPS（Ubuntu）+ systemd + Caddy 自動 HTTPS。

## 已知限制（誠實聲明）

- **先發打線**：四聯盟皆無穩定的賽前打線 API → 一律標示「尚未確認」，
  不以未確認名單推測（與 OBA 行為一致）
- **NPB 未來賽程**：Yahoo 僅「今日」卡片資料完整 → 賽程查詢列今日；
  未來日期僅對戰組合可信，球場/天氣不提供
- **KBO 官網**：對部分海外 IP 封鎖 → 連線失敗時對戰分析仍可用
  （Wikipedia 戰績），賽程查詢回傳降級訊息
- **牛棚近期使用量/疲勞**：僅 MLB 有逐場資料可擴充，目前四聯盟皆未顯示
- **WBC / 12強**：需賽事期間接入 WBSC 資料源（架構已預留）

## 檔案結構

```
line-baseball-bot/
├─ app/
│  ├─ main.py              # FastAPI webhook 伺服器、訊息路由
│  ├─ report.py            # 五段報告排版、信心等級
│  ├─ teams.py             # MLB 球隊別名
│  ├─ weather.py           # Open-Meteo 天氣 + 球場座標/屋頂
│  ├─ analysis/engine.py   # Poisson 預測引擎（和局規則、主客場拆分）
│  ├─ data/mlb.py          # MLB Stats API 客戶端
│  └─ leagues/
│     ├─ base.py           # 供應器介面
│     ├─ mlb.py / cpbl.py / npb.py / kbo.py
│     ├─ intl.py           # WBC / 12強 賽期偵測
│     └─ __init__.py       # 註冊表：關鍵字路由、隊名自動判斷
├─ requirements.txt
└─ .env.example
```
