# 🎯 Sniper Entry/Exit Bot — KhanSaab V.02

Automated trading bot based on the **Sniper Entry/Exit with SL&TP** Pine Script strategy, running on **BingX Perpetual Futures** with **Telegram signals**.

---

## 📁 File Structure

```
sniper_bot/
├── bot.py            # Main loop & orchestrator
├── strategy.py       # Pine Script → Python (EMA cross, scores, levels)
├── bingx_client.py   # BingX API (orders, positions, klines)
├── telegram_bot.py   # Telegram alerts
├── risk_manager.py   # Position sizing & guards
├── requirements.txt
├── Dockerfile
├── railway.json
├── .env.example
└── README.md
```

---

## ⚙️ Strategy Logic

| Component | Detail |
|-----------|--------|
| **Signal** | EMA 9 / EMA 21 crossover (buy) / crossunder (sell) |
| **Bull/Bear Score** | 7-factor scoring (VWAP, RSI, MACD, EMA, ADX, Volume, 5m RSI) |
| **SL** | Entry ± (ATR × multiplier) |
| **TP1–TP5** | Entry ± (ATR × 1R … 5R) |
| **Bias** | STRONG BULL / MILD BULL / MILD BEAR / STRONG BEAR |

---

## 🚀 Quick Start (Local)

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/sniper-bot.git
cd sniper-bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your keys
```

### 3. Get your Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy **token**
2. Message [@userinfobot](https://t.me/userinfobot) → copy your **chat_id**
3. Start your bot (send it `/start`)

### 4. Get BingX API Keys

1. Login to [BingX](https://bingx.com)
2. Account → API Management → Create API
3. Enable: **Read**, **Trade** (do NOT enable withdrawal)
4. Whitelist your IP if possible
5. Copy **API Key** and **Secret Key**

### 5. Run (signals only first!)

```bash
# Test with SIGNALS_ONLY=true first
SIGNALS_ONLY=true python bot.py
```

---

## 🚂 Deploy on Railway

### Method A — GitHub (recommended)

1. Push repo to GitHub:
```bash
git init
git add .
git commit -m "Initial sniper bot"
git remote add origin https://github.com/YOUR_USERNAME/sniper-bot.git
git push -u origin main
```

2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**

3. Select your repo → Railway auto-detects Dockerfile ✅

4. Click **Variables** → Add each variable from `.env.example`:

| Variable | Value |
|----------|-------|
| `BINGX_API_KEY` | your key |
| `BINGX_SECRET` | your secret |
| `TG_TOKEN` | your bot token |
| `TG_CHAT_ID` | your chat id |
| `SYMBOL` | `BTC-USDT` |
| `TIMEFRAME` | `15m` |
| `RISK_PCT` | `1.0` |
| `LEVERAGE` | `5` |
| `SIGNALS_ONLY` | `false` |
| `ATR_MULTIPLIER` | `1.5` |
| `POLL_SECONDS` | `60` |

5. Click **Deploy** — done! 🎉

### Method B — Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init
railway up
railway variables set BINGX_API_KEY=xxx BINGX_SECRET=xxx TG_TOKEN=xxx TG_CHAT_ID=xxx
```

---

## 📊 Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `SYMBOL` | `BTC-USDT` | Trading pair (BingX format) |
| `TIMEFRAME` | `15m` | Candle interval for signals |
| `RISK_PCT` | `1.0` | % of balance to risk per trade |
| `LEVERAGE` | `10` | Futures leverage (⚠️ start with 5) |
| `ATR_MULTIPLIER` | `1.5` | SL distance multiplier |
| `POLL_SECONDS` | `60` | Loop interval in seconds |
| `SIGNALS_ONLY` | `false` | If `true`, only send Telegram, no orders |
| `HEARTBEAT_HOURS` | `4` | Alive ping interval |

---

## 📲 Telegram Messages

| Event | Message |
|-------|---------|
| Bot starts | 🚀 Bot started with config |
| New signal | 🟢/🔴 Full signal with all TP/SL levels |
| TP hit | 🔥 Target hit with PnL |
| SL hit | 🛑 Stop loss with PnL |
| Order filled | ✅ Entry confirmed |
| Error | ⚠️ Error details |
| Heartbeat | 💓 Balance + PnL status every N hours |

---

## ⚠️ Risk Warnings

- **Start with `SIGNALS_ONLY=true`** until you trust the signals
- **Use low leverage** (3-5x) when starting with real money
- **Never risk more than 1-2%** per trade
- This bot trades **perpetual futures** — losses can exceed initial investment
- Past performance does not guarantee future results
- **You are responsible** for all trades

---

## 🛠 Supported Symbols (BingX format)

```
BTC-USDT   ETH-USDT   SOL-USDT   BNB-USDT
XRP-USDT   DOGE-USDT  ADA-USDT   AVAX-USDT
```

---

## 📝 License

Private use only. Strategy © KhanSaab 2026.
