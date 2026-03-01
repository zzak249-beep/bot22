# SATY ELITE v14 — Proyecto Independiente

Bot de trading automático para BingX Futuros con **6 módulos** Pine Script traducidos a Python, **aprendizaje automático** (Brain) y **backtesting** integrado.

---

## 📦 Archivos del proyecto

| Archivo | Descripción |
|---------|-------------|
| `saty_v14.py` | Bot principal — 6 módulos + consenso + gestión de trades |
| `brain.py` | Motor de aprendizaje — ajusta parámetros automáticamente |
| `backtest.py` | Backtesting sobre datos históricos reales de BingX |
| `requirements.txt` | Dependencias Python |
| `Dockerfile` | Imagen Docker para Railway |
| `railway.toml` | Configuración Railway (sin healthcheck — worker) |

---

## 🧠 6 Módulos integrados

| # | Módulo | Pine Script original | Señal |
|---|--------|---------------------|-------|
| 1 | **ConfPRO** | Confirmación Simple PRO v5 | BB + Squeeze + Volumen + EMA |
| 2 | **BollingerH** | Bollinger Hunter Pro v5.4 | W/M + %B div + Breakout + Walking |
| 3 | **SMC** | SMC Scalper M1 w/ M5 Confirm | Order Blocks + Sweep + BOS |
| 4 | **Powertrend** | Powertrend - Volume Range Filter | Volume Range + ADX + HL + VWMA |
| 5 | **BBPCT** | ◭ BBPCT% [AlgoAlpha] | %B extremos + Percentrank volatilidad |
| 6 | **RSI+** | RSI + BOO | Divergencias + OB/OS + Nivel 50 |

**Consenso:** se necesitan ≥2 módulos de acuerdo + score ≥ MIN_SCORE

---

## 🚀 Deploy en Railway

### Paso 1 — Nuevo repositorio GitHub
1. Ir a **github.com** → **New repository**
2. Nombre: `saty-v14` → Create
3. Subir los 6 archivos: **Add file → Upload files**
4. Commit: `SATY v14 inicial`

### Paso 2 — Nuevo proyecto Railway
1. Ir a **railway.app** → **New Project**
2. **Deploy from GitHub repo** → seleccionar `saty-v14`
3. Railway detecta el Dockerfile automáticamente

### Paso 3 — Variables de entorno (Railway → Variables)
```
BINGX_API_KEY=tu_api_key
BINGX_API_SECRET=tu_api_secret
TELEGRAM_BOT_TOKEN=123456:ABCdef...
TELEGRAM_CHAT_ID=-100xxxxxxxxxx
FIXED_USDT=8
LEVERAGE=10
MAX_OPEN_TRADES=10
MIN_SCORE=5
MIN_MODULES=2
MAX_DRAWDOWN=15
DAILY_LOSS_LIMIT=8
BTC_FILTER=true
COOLDOWN_MIN=30
TIMEFRAME=5m
HTF1=15m
HTF2=1h
```

### Paso 4 — Deploy
Railway despliega automáticamente al hacer commit en GitHub.

---

## 📊 Backtesting (antes de operar en real)

```bash
# Instalar dependencias
pip install -r requirements.txt

# Configurar API keys (solo lectura de datos históricos)
export BINGX_API_KEY=tu_key
export BINGX_API_SECRET=tu_secret

# Backtest top 20 pares, 90 días
python backtest.py

# Backtest solo BTC, 180 días
python backtest.py --symbol BTC/USDT:USDT --days 180

# Backtest y alimentar el Brain (recomiendado antes de operar en real)
python backtest.py --days 90 --feedbrain

# Ver todas las opciones
python backtest.py --help
```

**Salida del backtest:**
- `backtest_results.csv` → todos los trades con métricas
- `backtest_report.html` → equity curve + tablas interactivas (abrir en navegador)
- `brain_data.json` → Brain pre-entrenado (si usas `--feedbrain`)

---

## 🧠 Sistema de aprendizaje (Brain)

El Brain aprende automáticamente de cada trade cerrado:

| Qué aprende | Cómo actúa |
|-------------|------------|
| WR por combinación de módulos | Ajusta el `boost` multiplicador de score |
| Combinaciones con WR < 30% | Blacklist temporal 24h |
| Score mínimo óptimo | Sube/baja `MIN_SCORE` efectivo |
| Horas del día con peor WR | Reduce el tamaño de posición 50% |
| Horas con mejor WR | Aumenta el tamaño de posición 20% |

Cada 50 scans (~50 min) envía un reporte a Telegram con el estado del aprendizaje.

---

## 📱 Mensajes Telegram

| Mensaje | Cuándo |
|---------|--------|
| 🚀 ONLINE | Al arrancar |
| 🟢/🔴 LONG/SHORT | Nueva posición |
| 🟡 TP1+BE | Primer objetivo alcanzado |
| 🟠 TP2 | Segundo objetivo |
| ✅/❌ CERRADO | Trade cerrado con PnL |
| 💓 HEARTBEAT | Cada hora |
| 📡 SCAN | Cada 20 scans |
| 🧠 BRAIN | Cada 50 scans |
| ₿ BTC cambió | Cambio de bias BTC |
| 🚨 CIRCUIT BREAKER | Drawdown > MAX_DRAWDOWN |

---

## ⚠️ Anti-señales falsas

- **Consenso:** ≥2 módulos deben coincidir en dirección
- **Contradicción:** si 2+ long Y 2+ short → no operar
- **Squeeze bloqueante:** Módulo 2 no opera durante squeeze
- **BTC filter:** bloquea longs en bear market fuerte y viceversa
- **Cooldown:** 30 min mínimo entre trades del mismo símbolo
- **Spread filter:** descarta pares con spread > 0.8%
- **Brain override:** bloquea combinaciones con WR histórico < 30%
- **HTF confirmation:** señales débiles requieren confirmar en TF superior

---

## 📈 Parámetros recomendados según capital

| Capital | FIXED_USDT | LEVERAGE | MAX_OPEN |
|---------|-----------|---------|---------|
| < $200 | 5 | 5 | 5 |
| $200-500 | 8 | 10 | 8 |
| $500-1000 | 10 | 10 | 10 |
| > $1000 | 15 | 10 | 12 |
