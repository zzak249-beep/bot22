# 🤖 EMA Institutional Hunter Bot — BingX Futures

Bot de trading automático basado en la estrategia **EMA Institutional Hunter 2H** implementada en Python para **BingX Perpetual Futures**. Listo para desplegarse en Railway con notificaciones Telegram.

---

## 📁 Estructura del proyecto

```
bingx-ema-bot/
├── bot.py          # Orquestador principal — loop, ciclo, lógica
├── strategy.py     # Detección de señales LONG/SHORT
├── indicators.py   # EMA, RSI, ATR, VWAP semanal
├── exchange.py     # Wrapper BingX (ccxt) — órdenes, posiciones
├── risk.py         # Gestión de riesgo — límites, cooldown, estado
├── notifier.py     # Alertas Telegram con mensajes formateados
├── config.py       # Toda la configuración desde .env
├── requirements.txt
├── railway.toml
├── Procfile
├── .env.example
└── README.md
```

---

## 🧠 Estrategia

### Condición LONG
| Filtro | Condición |
|---|---|
| Tendencia | EMA50 > EMA120 > EMA200 |
| Estructura | Precio > EMA120 AND EMA200 |
| Pullback | Low ≤ EMA21 o Low ≤ EMA50 |
| Cruce micro | EMA9 cruza arriba EMA21 |
| RSI | RSI > 45 (filtro extra) |
| Volumen | Vol > media 20 velas (filtro extra) |

**SHORT**: exactamente el espejo.

### Gestión del trade
```
Entry ──────────────────────────────────────────►
  │
  ├── SL = min(EMA50, low) o mínimo 0.5×ATR
  │
  ├── TP1 = Entry + Risk × 1R  (cierra 50%)
  │         → SL se mueve a BREAKEVEN automáticamente
  │
  └── TP2 = Entry + Risk × 2R  (cierra restante)
```

### Mejoras vs Pine Script original
- ✅ Filtro RSI (evita entrar en zonas sobreextendidas)
- ✅ Filtro volumen (confirma interés real)
- ✅ SL mínimo por ATR (evita ruido en baja volatilidad)
- ✅ Cooldown tras SL (evita revenge trading)
- ✅ Position sizing dinámico (% fijo del balance)
- ✅ Breakeven automático tras TP1
- ✅ Señal solo en vela cerrada (nunca en vela abierta)

---

## 🚀 Deploy en Railway

### 1. GitHub

```bash
git init
git add .
git commit -m "feat: ema institutional bot"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/bingx-ema-bot.git
git push -u origin main
```

### 2. Railway

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Selecciona `bingx-ema-bot`
3. Railway detecta el `railway.toml` automáticamente

### 3. Variables de entorno en Railway

| Variable | Valor |
|---|---|
| `BINGX_API_KEY` | tu key |
| `BINGX_API_SECRET` | tu secret |
| `DRY_RUN` | `true` para empezar |
| `SYMBOL` | `BTC/USDT:USDT` |
| `TIMEFRAME` | `2h` |
| `LEVERAGE` | `5` |
| `RISK_PCT` | `1.0` |
| `MAX_DAILY_LOSS_PCT` | `3.0` |
| `TELEGRAM_TOKEN` | (opcional) |
| `TELEGRAM_CHAT_ID` | (opcional) |

---

## 🔑 API BingX

1. Ve a [BingX → Gestión de API](https://bingx.com/es-es/account/api)
2. Crea una key nueva
3. Activa: **Lectura** + **Futuros perpetuos**
4. **NO actives retiros**
5. Restringe la IP a la de Railway si es posible

---

## ⚠️ Gestión de riesgo

| Protección | Descripción |
|---|---|
| Pérdida diaria máx | Bot se pausa al alcanzar `MAX_DAILY_LOSS_PCT` |
| Límite diario | Máximo `MAX_TRADES_DAY` operaciones al día |
| Cooldown | `COOLDOWN_BARS` velas de espera tras un SL |
| Breakeven | SL se mueve al entry automáticamente tras TP1 |
| 1 posición | Nunca abre una segunda posición con otra abierta |

---

## 🛠️ Desarrollo local

```bash
git clone https://github.com/TU_USUARIO/bingx-ema-bot
cd bingx-ema-bot
pip install -r requirements.txt
cp .env.example .env    # edita con tus keys
python bot.py
```

---

## ⚠️ Disclaimer

Este bot opera con **dinero real** en mercados de futuros con apalancamiento. Los futuros conllevan riesgo de pérdida total del capital. Empieza **siempre** con `DRY_RUN=true`, revisa los logs durante al menos 1 semana, y pasa a live solo cuando estés seguro. Nunca arriesgues dinero que no puedas permitirte perder.
