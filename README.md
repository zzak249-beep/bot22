# Bellsz Bot v2.0 — Liquidez Lateral [BingX Perpetual Futures]

Bot de trading automático 24/7 para **BingX Perpetual Futures** basado en la estrategia de **Liquidez Lateral [Bellsz]**.

---

## Archivos del proyecto

| Archivo | Descripción |
|---|---|
| `main_bellsz.py` | **Loop principal** — arrancar este en Railway |
| `analizar_bellsz.py` | Motor de señales (purga BSL/SSL + EMA + RSI + confluencias) |
| `config.py` | Configuración completa via variables de entorno |
| `exchange.py` | API BingX Perpetual Futures v4.3 |
| `memoria.py` | Compounding, historial y aprendizaje |
| `scanner_pares.py` | Escáner de pares por volumen |
| `config_pares.py` | Lista fija de pares (fallback) |
| `liquidez.py` | Módulo de análisis de liquidez |
| `metaclaw.py` | Agente IA opcional (requiere Anthropic API) |
| `optimizador.py` | Auto-optimización en segundo plano |
| `requirements.txt` | Dependencias Python |
| `env.railway` | Variables de entorno para Railway |

---

## Estrategia

### Núcleo — Purgas BSL/SSL
Detecta cuando el precio barre niveles de liquidez (máximos/mínimos de H1, H4 y Diario) y revierte:

- **LONG**: precio barró SSL (mínimos) y cerró por encima → trampa bajista
- **SHORT**: precio barró BSL (máximos) y cerró por debajo → trampa alcista

### Confirmaciones
1. **EMA 9/21** — alineadas con la dirección de la señal
2. **RSI** — entre 30-70, con momentum
3. **Score de confluencias** — OB, FVG, CHoCH, BOS, Sweep, VWAP, KZ...

### Parámetros óptimos (backtest 60 días)
```
TP_DIST_MULT = 3.0   → Profit Factor 47.77
SCORE_MIN    = 5     → WR 50%, MaxDD $0.30
```

---

## Instalación en Railway

### 1. Crear nuevo proyecto en Railway
- Conectar este repositorio GitHub
- Railway detecta Python automáticamente

### 2. Configurar Variables de Entorno
Copiar el contenido de `env.railway` en Railway → Variables, rellenando:
```
BINGX_API_KEY=tu_api_key
BINGX_SECRET_KEY=tu_secret_key
TELEGRAM_TOKEN=tu_bot_token
TELEGRAM_CHAT_ID=tu_chat_id
```

### 3. Comando de inicio
En Railway → Settings → Deploy → Start Command:
```
python main_bellsz.py
```

### 4. Volumen persistente (recomendado)
En Railway → Add Volume → Mount Path: `/app/data`

---

## Variables Railway más importantes

| Variable | Valor recomendado | Descripción |
|---|---|---|
| `TRADE_USDT_BASE` | `10` | USDT por trade |
| `LEVERAGE` | `10` | Apalancamiento |
| `SCORE_MIN` | `5` | Score mínimo (backtest óptimo) |
| `TP_DIST_MULT` | `3.0` | TP = dist_SL × 3 (backtest óptimo) |
| `LIQ_MARGEN` | `0.15` | Margen de purga en % |
| `LIQ_LOOKBACK` | `30` | Velas HTF para BSL/SSL |
| `MAX_PARES_SCAN` | `30` | Máximo pares a escanear |
| `LOOP_SECONDS` | `90` | Ciclo cada 90 segundos |
| `ADX_MIN` | `8` | ADX mínimo (no bloquear demasiado) |
| `BINGX_MODE` | `auto` | Detecta hedge/oneway automáticamente |

---

## Notas importantes

- La **purga es obligatoria** — sin purga no hay señal
- Los niveles HTF se cachean 15 minutos para no saturar la API
- El bot gestiona TP1 parcial (50%), break-even y trailing stop automáticamente
- Las posiciones se recuperan al reiniciar
- El compounding sube el tamaño del trade cada $50 ganados (+$5)
