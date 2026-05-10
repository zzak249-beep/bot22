# 🤖 EMA Slope + EMA Cross Bot
**BingX Perpetual Futures | Automated | Telegram Alerts | Railway Deployment**

Faithful Python port of ChartArt's Pine Script v3 strategy — running fully automated on 3-minute candles with real-money risk controls.

---

## 📁 File Structure

```
ema_bot/
├── main.py            ← Bot orchestrator (entry point)
├── strategy.py        ← EMA signal logic (mirrors Pine Script exactly)
├── bingx_client.py    ← BingX Swap REST API wrapper
├── telegram_client.py ← Telegram notifications
├── risk_manager.py    ← Position sizing, SL/TP, drawdown guard
├── backtest.py        ← Validate strategy before going live
├── requirements.txt
├── Dockerfile
├── railway.toml
└── .env.example       ← Copy to .env and fill in your keys
```

---

## ⚙️ Strategy Logic

| Signal | Condition |
|--------|-----------|
| **LONG**  | `price crossunder EMA3` OR `(price↓ AND EMA1↓ AND price crossunder EMA1 AND EMA2↑)` |
| **SHORT** | `price crossover EMA3`  OR `(price↑ AND EMA1↑ AND price crossover EMA1  AND EMA2↓)` |

Always in the market (non-stop long/short). Default EMAs: 2 / 4 / 20 on 3m candles.

---

## 🚀 Quick Start

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/ema_bot.git
cd ema_bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your keys (see below)
```

### 3. Get your keys

#### BingX API
1. Log in → **Account → API Management → Create API Key**
2. Enable: **Read**, **Perpetual Futures Trading**
3. Whitelist your IP (or Railway's IP)
4. Copy `API Key` + `Secret Key` into `.env`

#### Telegram Bot
1. Message `@BotFather` → `/newbot` → copy the token
2. Message `@userinfobot` → copy your `id` (that's your chat_id)
3. Start a chat with your new bot (send `/start`)

### 4. Run backtest first (strongly recommended!)

```bash
python backtest.py
```

### 5. Paper trade

```bash
DEMO_MODE=true python main.py
```

### 6. Go live (when you're confident)

```bash
DEMO_MODE=false python main.py
```

---

## ☁️ Deploy to Railway (recommended)

Railway gives you a free always-on server — perfect for a 24/7 bot.

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → **New Project → Deploy from GitHub**
3. Select your repo
4. In **Variables**, add all keys from `.env.example`
5. Railway auto-detects `railway.toml` and starts the bot
6. Check **Logs** to confirm it's running

---

## ⚖️ Risk Settings (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RISK_PCT` | `1.0` | % of balance at risk per trade |
| `SL_PCT` | `1.5` | Stop-loss distance from entry (%) |
| `TP_RATIO` | `2.0` | TP = SL × ratio (2.0 → 3% TP for 1.5% SL) |
| `MAX_DD_PCT` | `10.0` | Auto-halt if account drawdown exceeds this % |
| `LEVERAGE` | `5` | Futures leverage (start at 3–5!) |

**Start conservative:** `RISK_PCT=0.5`, `LEVERAGE=3`, `DEMO_MODE=true` for at least 24 hours.

---

## 📱 Telegram Messages You'll Receive

| Event | Message |
|-------|---------|
| Bot starts | Startup summary |
| Signal fires | 🟢 LONG / 🔴 SHORT with entry, EMA levels, SL, TP |
| Order filled | Confirmation with fill price |
| Position closed | PnL summary |
| Heartbeat | Every 20 candles — price, position, balance |
| Errors | Full error context for debugging |

---

## ⚠️ Disclaimers

- **This bot trades real money. You can lose your entire investment.**
- Always backtest and paper trade before going live.
- Never trade with money you cannot afford to lose.
- The authors provide no warranty of profitability.
- Past performance does not guarantee future results.

---

## 🔒 Security

- Never commit your `.env` file (it's in `.gitignore`)
- Use IP-restricted BingX API keys
- Start with minimal leverage (3–5x)
- Enable 2FA on your BingX account
