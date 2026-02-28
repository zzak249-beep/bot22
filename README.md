# 🔥 SAIYAN AURA FUSION BOT

**TradingView Pine Script → Webhook → BingX Perpetual Futures + Telegram**

Railway 24/7 · Auto TP1/TP2/TP3 · Break-Even automático · Circuit Breaker

---

## 📁 Archivos del repo

```
saiyan-fusion-bot/
├── bot.py            ← servidor Flask webhook + lógica BingX
├── requirements.txt
├── Procfile          ← comando de inicio para Railway
├── nixpacks.toml     ← fix pip en Railway/Nix
├── railway.toml      ← config Railway
└── README.md
```

---

## 🚀 DEPLOY — 3 pasos

### 1) Subir a GitHub (repo PRIVADO)

```bash
cd saiyan-fusion-bot
git init
git add .
git commit -m "SAIYAN AURA FUSION BOT"

# Crear repo PRIVADO en https://github.com/new
git remote add origin https://github.com/TU_USUARIO/saiyan-fusion-bot.git
git branch -M main
git push -u origin main
```

### 2) Crear proyecto en Railway

1. Ve a **[railway.app](https://railway.app)** → **New Project**
2. **Deploy from GitHub repo** → selecciona `saiyan-fusion-bot`
3. Railway despliega automáticamente (detecta `Procfile`)
4. En tu servicio → **Settings** → **Domains** → **Generate Domain**
   - Anota tu URL: `https://XXXX.railway.app`

### 3) Variables de entorno en Railway

Settings → **Variables** → Add Variable:

| Variable | Valor | Obligatoria |
|---|---|---|
| `BINGX_API_KEY` | tu API Key de BingX | ✅ |
| `BINGX_API_SECRET` | tu API Secret de BingX | ✅ |
| `TELEGRAM_BOT_TOKEN` | token de tu bot de Telegram | ✅ |
| `TELEGRAM_CHAT_ID` | tu chat ID | ✅ |
| `WEBHOOK_SECRET` | clave secreta (ej: `MiClave2024`) | ✅ |
| `FIXED_USDT` | `20` | ⬜ def:20 |
| `LEVERAGE` | `5` | ⬜ def:5 |
| `MAX_OPEN_TRADES` | `5` | ⬜ def:5 |
| `TP1_PCT` | `1.0` | ⬜ def:1.0 |
| `TP2_PCT` | `1.8` | ⬜ def:1.8 |
| `TP3_PCT` | `3.0` | ⬜ def:3.0 |
| `SL_PCT` | `0.8` | ⬜ def:0.8 |
| `MAX_DRAWDOWN` | `15` | ⬜ def:15 |
| `DAILY_LOSS_LIMIT` | `8` | ⬜ def:8 |

---

## 📡 CONFIGURAR TRADINGVIEW

### En el Pine Script → sección "■ Webhook Message"

Configura cada campo con el JSON exacto (cambia `MiClave2024` por tu `WEBHOOK_SECRET`):

**Long Entry:**
```json
{"secret":"MiClave2024","action":"Long Entry","symbol":"{{ticker}}"}
```

**Short Entry:**
```json
{"secret":"MiClave2024","action":"Short Entry","symbol":"{{ticker}}"}
```

**Long Exit:**
```json
{"secret":"MiClave2024","action":"Long Exit","symbol":"{{ticker}}"}
```

**Short Exit:**
```json
{"secret":"MiClave2024","action":"Short Exit","symbol":"{{ticker}}"}
```

**Stop Loss Hit:**
```json
{"secret":"MiClave2024","action":"Stop Loss Hit","symbol":"{{ticker}}"}
```

**TP1 Hit:**
```json
{"secret":"MiClave2024","action":"TP1 Hit","symbol":"{{ticker}}"}
```

**TP2 Hit:**
```json
{"secret":"MiClave2024","action":"TP2 Hit","symbol":"{{ticker}}"}
```

**TP3 Hit:**
```json
{"secret":"MiClave2024","action":"TP3 Hit","symbol":"{{ticker}}"}
```

### Crear cada alerta en TradingView:

1. Con el gráfico abierto y el indicador aplicado
2. Click derecho en el gráfico → **Add Alert** (o `Alt+A`)
3. **Condition** → selecciona `SAIYAN AURA FUSION` → la condición correspondiente
4. Pestaña **Notifications** → activa **Webhook URL**
5. URL: `https://XXXX.railway.app/webhook`
6. **Message**: pega el JSON de arriba
7. Activa **Once Per Bar Close** (evita repaint)
8. Guarda → repite para cada condición

> ⚠️ Necesitas cuenta TradingView **Pro** o superior para webhooks.

---

## 🔄 Flujo completo de una operación

```
Pine Script detecta señal LONG (OCC + AURA confluencia)
              ↓
  Alerta webhook → https://XXXX.railway.app/webhook
              ↓
  Bot valida secret → verifica guards:
    · max trades, circuit breaker, límite diario
              ↓
  Abre LONG en BingX (orden market)
              ↓
  Coloca automáticamente:
    TP1 50% de la posición  @ entrada + TP1_PCT%
    TP2 30% de la posición  @ entrada + TP2_PCT%
    TP3 20% de la posición  @ entrada + TP3_PCT%
    SL  100% stop-market    @ entrada - SL_PCT%
              ↓
  Telegram: alerta con todos los niveles y balance
              ↓
  Cuando Pine alerta "TP1 Hit":
    → Bot mueve SL a Break-Even
    → Telegram: TP1 alcanzado
              ↓
  Cuando Pine alerta "TP2 Hit":
    → Telegram: TP2 alcanzado
              ↓
  Cuando Pine alerta "TP3 Hit":
    → Bot cierra posición restante
    → Telegram: cierre final con PnL total
```

---

## 🛡 Protecciones automáticas

| Protección | Umbral | Acción |
|---|---|---|
| Circuit Breaker | `MAX_DRAWDOWN` % drawdown | Bloquea entradas + avisa Telegram |
| Límite diario | `DAILY_LOSS_LIMIT` % | Bloquea entradas + avisa Telegram |
| Max posiciones | `MAX_OPEN_TRADES` | Rechaza nuevas entradas |
| Posición duplicada | mismo símbolo | Ignora señal |
| Webhook falso | `WEBHOOK_SECRET` | Rechaza 401 |

---

## 🔍 Endpoints de monitoreo

| URL | Descripción |
|---|---|
| `GET /` | Health check + stats globales |
| `GET /positions` | Posiciones abiertas en detalle |
| `POST /webhook` | Recibe alertas de TradingView |

Ejemplo respuesta de `GET /`:
```json
{
  "status": "alive",
  "open_trades": 2,
  "wins": 8,
  "losses": 3,
  "win_rate": 72.7,
  "profit_factor": 2.4,
  "total_pnl": 45.20,
  "daily_pnl": 12.30,
  "circuit_breaker": false
}
```

---

## 💰 Cálculo de riesgo

Con `FIXED_USDT=20` y `LEVERAGE=5`:
- Notional por trade: **$100** (20 × 5x)
- Riesgo real máximo: **$0.80** por $100 notional (SL 0.8%)
- Riesgo en USDT de margen: ~**$0.16** sobre los $20

Ajusta `FIXED_USDT`, `LEVERAGE` y los `_PCT` según tu tolerancia.

---

## ⚠️ Importante

- Repo **PRIVADO** siempre
- Empieza con `FIXED_USDT=5` y `LEVERAGE=1` para probar
- Railway plan **Hobby ($5/mes)** para uptime 24/7
- BingX API Key: activa **Read + Trade**, nunca **Withdraw**
