# QF×JP v3.5 PREDATOR — Multi-Symbol Scanner Bot

Bot autónomo que escanea **todos los perpetuos de BingX**, detecta
rupturas de trendline + score compuesto y abre trades automáticamente.
**No necesita TradingView** — calcula todo desde la API de BingX.

```
BingX OHLCV API → Engine QF×JP → TL Ruptura + Score → Orden BingX + Telegram
```

---

## 📁 Estructura

```
qfjp-scanner/
├── src/
│   ├── bot.py            # Loop principal + orquestación
│   ├── config.py         # Toda la config via env vars
│   ├── engine.py         # Estrategia QF×JP v3.5 completa en Python
│   ├── scanner.py        # Escáner multi-símbolo concurrente
│   ├── bingx_client.py   # API BingX: candles + órdenes
│   ├── telegram_client.py
│   ├── risk_manager.py   # Filtros + sizing
│   └── state.py          # Estado: trades abiertos, circuit breaker
├── .env.example
├── requirements.txt
├── Procfile
└── railway.toml
```

---

## 🚀 Inicio rápido (local)

```bash
git clone https://github.com/TU_USUARIO/qfjp-scanner
cd qfjp-scanner
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # rellena con tus claves
python -m src.bot
```

---

## ☁️ Deploy en Railway (recomendado)

1. Sube el repo a GitHub.
2. [Railway](https://railway.app) → **New Project → GitHub repo**.
3. En **Variables**, añade cada variable de `.env.example` con tus valores reales.
4. Railway ejecuta `python -m src.bot` automáticamente vía `railway.toml`.
5. En **Logs** verás el escáner en marcha en segundos.

> Railway tiene plan gratuito con 500h/mes — suficiente para un worker continuo.

---

## 📊 Lógica de señal — trigger principal: TL RUPTURA

El bot replica el panel **QF×JP v3.5** del Pine Script:

| Condición            | Descripción                                      |
|----------------------|--------------------------------------------------|
| **TL RUPTURA LONG**  | Precio rompe al alza una trendline bajista       |
| **TL RUPTURA SHORT** | Precio rompe a la baja una trendline alcista     |
| Score compuesto ≥ umbral | Suma ponderada de 9 factores (CVD, MFI, VDI…)|
| HTF alineado         | ≥2 de 3 timeframes (15m/1h/4h) en la misma dirección |
| Sesión activa        | LDN / NY / OVL (no OFF)                          |

**Tiers:**
- `STD`  → Score ≥ 55 + TL break
- `FUEL` → Score ≥ 68 + TL break + CVD/Sweep/CHoCH/VDI
- `SUP`  → Score ≥ 80 + todo lo anterior + Dark Pool / divergencias

Por defecto `MIN_TIER=FUEL` — solo opera señales de alta convicción.

---

## 🔔 Mensajes Telegram

**Señal de entrada:**
```
⭐⭐ QF×JP v3.5 — 🟢 LONG FUEL
Par: BTC-USDT
Score L:72/100  S:41/100
ADX: 28.4 (TEND↑)  CVD:0.73
RSI: 38  MFI:32  Sesión:NY
HTF L:3/3  S:0/3  Conv L:11  S:4
Entrada: 67842.50
SL: 67510.30
TP1: 68355.20  TP2: 69108.60
R:R: 1.5  Tamaño:0.15 u
Contexto: 📈 TL RUPTURA  ⚡VDI  💧SWP
ID: 1748291029301
```

**Resumen de scan cada 10 ciclos:**
```
🔍 Scan QF×JP — 80 pares — 3 señal(es)

🟢 BTC-USDT          FUEL L: 72 S: 41 🔥TL
🔴 ETH-USDT          FUEL L: 38 S: 71 🔥TL
🟢 SOL-USDT          STD  L: 58 S: 44
```

---

## ⚙️ Variables clave

| Variable           | Default  | Descripción                              |
|--------------------|----------|------------------------------------------|
| `BINGX_API_KEY`    | —        | API key de BingX (Perpetuals)            |
| `BINGX_SECRET_KEY` | —        | Secret key de BingX                      |
| `TELEGRAM_TOKEN`   | —        | Token del bot (@BotFather)               |
| `TELEGRAM_CHAT_ID` | —        | ID del canal/grupo (negativo = grupo)    |
| `TIMEFRAME`        | `3m`     | Temporalidad de velas                    |
| `SCAN_INTERVAL`    | `60`     | Segundos entre escaneos                  |
| `MAX_SYMBOLS`      | `80`     | Máx pares a escanear                     |
| `MIN_VOLUME_USDT`  | `500000` | Volumen 24h mínimo para incluir par      |
| `CAPITAL`          | `1000`   | USDT a usar                              |
| `RISK_PCT`         | `1.0`    | % riesgo por trade                       |
| `LEVERAGE`         | `10`     | Apalancamiento                           |
| `MIN_TIER`         | `FUEL`   | Tier mínimo para ejecutar               |
| `REQUIRE_TL_BREAK` | `true`   | Exigir ruptura trendline                 |
| `MAX_OPEN_TRADES`  | `5`      | Posiciones simultáneas máximo            |

---

## 🛡️ Gestión de riesgo

- **Circuit breaker**: para el bot si la pérdida diaria supera $50 (edita en `state.py`)
- **Max open trades**: nunca abre más de N posiciones simultáneas
- **Max daily trades**: hard stop diario
- **Sin duplicados**: no abre dos trades en el mismo par
- **R:R check**: rechaza señales con R:R < 1.0
- **Sesión filter**: solo opera en LDN / NY / OVL por defecto

---

## 🔧 Permisos BingX necesarios

En BingX → API Management, activa:
- ✅ **Read**
- ✅ **Perpetuals trading**
- ❌ Withdrawals (NO necesario)

---

## ⚠️ Aviso

Este bot opera con dinero real. Prueba primero en paper trading reduciendo
`CAPITAL` a un valor mínimo y revisando los logs antes de aumentar el tamaño.
