# 🤖 BingX SuperBot v2

Automated crypto trading bot for BingX Perpetual Futures.
Combines **VWAP Volatility Bands [BOSWaves]** + **Sniper Entry [KhanSaab V.02]**.

---

## 🐛 Bug Fix (v2)

The original bot crashed with:
```
KeyError: 'BINGX_SECRET_KEY'
```
**Root cause:** Railway variable was named `BINGX_API_SECRET` but code expected `BINGX_SECRET_KEY`.

**Fix:** Bot now accepts BOTH names automatically:
```
BINGX_API_SECRET   ← Railway default (use this)
BINGX_SECRET_KEY   ← also accepted for compatibility
```

---

## 🧠 Strategy Logic

### Signal Generation (dual confirmation)
| Check | Source |
|---|---|
| EMA 9/21 crossover | Sniper trigger |
| T3-smoothed VWAP trend | BOSWaves |
| Bull/Bear score ≥ 5/7 conditions | Sniper dashboard |
| ADX > 22 (strong trend) | Both indicators |
| Price not overextended (within Band 4) | BOSWaves |
| HTF RSI alignment (1h) | Multi-TF filter |
| R:R ratio ≥ 1.5 | Risk filter |
| Bollinger Band squeeze bonus | Volatility filter |

### Entry Conditions (ALL must be true)
**LONG**: EMA9 crosses above EMA21 + T3-VWAP rising + Bull ≥ 71% + ADX > 22  
**SHORT**: EMA9 crosses below EMA21 + T3-VWAP falling + Bear ≥ 71% + ADX > 22

### Risk Management
- **Risk per trade**: 1% of balance (configurable)
- **Max open positions**: 5 (configurable)
- **Stop Loss**: entry ± (ATR × 1.5) via exchange order
- **TP1**: 1× risk → close 50% (lock profit)
- **TP2**: 2× risk → close remaining
- **Daily loss limit**: 5% → kill switch (configurable)
- **Leverage**: 5× isolated margin (configurable)
- **Margin check**: reject if position uses >30% of balance

### Commission Savings
| Order Type | Fee | vs Market |
|---|---|---|
| LIMIT entry (maker) | **0.02%** | **60% cheaper** |
| MARKET entry (taker) | 0.05% | baseline |
| LIMIT TP (maker) | **0.02%** | **60% cheaper** |

The bot uses LIMIT orders with a 0.03% price offset for fill probability.
Unfilled limit orders auto-cancel after 2 minutes (configurable).

---

## 🚀 Deploy to Railway

### Step 1: BingX API Key
1. Go to [BingX API Management](https://bingx.com/en-us/account/api/)
2. Create API key with **Futures Trading** permission
3. Note both the **API Key** and **Secret Key**

### Step 2: Push to GitHub
```bash
git init
git add .
git commit -m "SuperBot v2"
git remote add origin https://github.com/YOUR_USER/bingx-superbot.git
git push -u origin main
```

### Step 3: Railway Setup
1. [railway.app](https://railway.app) → New Project → Deploy from GitHub
2. Select your repo
3. Set environment variables:

| Variable | Value | Required |
|---|---|---|
| `BINGX_API_KEY` | your API key | ✅ |
| `BINGX_API_SECRET` | your API secret | ✅ |
| `DRY_RUN` | `true` ← **start here** | ✅ |
| `TELEGRAM_BOT_TOKEN` | bot token | optional |
| `TELEGRAM_CHAT_ID` | your chat ID | optional |
| `MAX_OPEN_TRADES` | `5` | optional |
| `LEVERAGE` | `5` | optional |
| `RISK_PER_TRADE` | `0.01` | optional |
| `SCAN_PERIOD_SECONDS` | `900` | optional |
| `DAILY_LOSS_LIMIT` | `0.05` | optional |

---

## ⚠️ CRITICAL: Test Protocol

```
Step 1: DRY_RUN=true   →  3-7 days minimum. Read every log entry.
Step 2: DRY_RUN=false  →  Start with $50-100 USDT MAXIMUM
Step 3: Scale up       →  Only after 2+ weeks of profitable live trading
```

**Crypto futures are high risk. Only trade what you can afford to lose entirely.**

---

## 📁 File Structure

```
bingx-superbot/
├── main.py          # Entry point (validates env vars early)
├── bot.py           # Main orchestrator loop
├── strategy.py      # Signal logic (VWAP + Sniper + BB squeeze)
├── scanner.py       # Parallel market scanner (150 symbols)
├── bingx_client.py  # BingX REST API (retry logic, rate limiting)
├── risk_manager.py  # Position sizing + kill switch + fee tracking
├── notifier.py      # Telegram alerts (optional)
├── requirements.txt
├── railway.toml
├── Procfile
└── .env.example
```

---

## 📊 Bot Loop (every 15 min)

```
1. Daily reset check
2. Check pending limit orders (fill / timeout / cancel)
3. Manage active positions:
   → TP1 hit? Close 50%, log profit
   → TP2 hit? Close 100%, log profit
   → Position gone? (SL hit by exchange) → record loss
4. If capacity available (<5 positions):
   → Scan 150 USDT perpetuals in parallel (8 threads)
   → Filter: vol >$3M, ADX >22, score ≥71%, R:R ≥1.5
   → Open top signals with LIMIT orders
5. Log status + P&L
```

---

## 🔧 Tuning Parameters

### More conservative (fewer but safer trades)
```
RISK_PER_TRADE=0.005     (0.5%)
LEVERAGE=3
MIN_SCORE=6              (edit strategy.py)
DAILY_LOSS_LIMIT=0.03
```

### More aggressive (more trades, more risk)
```
RISK_PER_TRADE=0.02      (2%)
LEVERAGE=10
SCAN_PERIOD_SECONDS=300  (5 min)
MAX_OPEN_TRADES=8
```

---

## 📜 Disclaimer

Educational purposes only. Cryptocurrency derivatives trading involves extreme risk of loss.
Past performance does not guarantee future results. Never risk money you cannot afford to lose entirely.
