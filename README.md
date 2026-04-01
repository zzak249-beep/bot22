# 🤖 Zero Lag Scalping Bot — BingX + Railway

Bot de trading automático basado en la estrategia del vídeo de Whale Analytics:
**Zero Lag Trend Signals + Trend Reversal Probability** en gráfico de 15 minutos.

---

## 📋 Estrategia implementada

| Indicador | Pine Script original | Configuración |
|-----------|---------------------|---------------|
| Zero Lag Trend Signals | AlgoAlpha | Length=70, Mult=1.2 |
| Trend Reversal Probability | AlgoAlpha | Period=50 |

### Reglas de entrada
- ✅ **LONG**: Close cruza al alza el ZLEMA + Tendencia alcista + Prob. reversión < 30%
- ✅ **SHORT**: Close cruza a la baja el ZLEMA + Tendencia bajista + Prob. reversión < 30%

### Reglas de salida
- 🚪 Prob. reversión ≥ 84% (nivel institucional del indicador)
- 🔄 Cambio de tendencia en contra de la posición

---

## 🚀 Despliegue en Railway

### Paso 1: Configurar BingX
1. Créate una cuenta en [BingX](https://bingx.com)
2. Ve a **Cuenta → API Management → Crear API**
3. Activa permisos: **Leer + Operar en Futuros**
4. Guarda el `API Key` y `Secret Key`

### Paso 2: Preparar el repo en GitHub
```bash
git clone <este-repo>
cd trading-bot
cp .env.example .env
# Edita .env con tus claves y configuración
```

### Paso 3: Desplegar en Railway
1. Ve a [railway.app](https://railway.app) → **New Project → Deploy from GitHub**
2. Selecciona este repositorio
3. Ve a **Variables** y añade todas las del `.env.example`:

| Variable | Valor | Descripción |
|----------|-------|-------------|
| `BINGX_API_KEY` | `xxx` | Tu API Key de BingX |
| `BINGX_SECRET_KEY` | `xxx` | Tu Secret Key |
| `SYMBOL` | `BTC-USDT` | Par a operar |
| `LEVERAGE` | `5` | Apalancamiento |
| `RISK_PCT` | `0.05` | 5% por operación |
| `DEMO_MODE` | `false` | `true` para probar sin dinero |

4. Railway detectará el `Procfile` y lanzará el bot automáticamente.

---

## ⚠️ Advertencias de riesgo

> **IMPORTANTE**: El trading con apalancamiento puede resultar en pérdida total del capital.
> Esta herramienta es educativa. Úsala bajo tu propia responsabilidad.

- Empieza **siempre** con `DEMO_MODE=true` para verificar que todo funciona
- Nunca arriesgues más de lo que puedas permitirte perder
- El backtesting pasado no garantiza resultados futuros
- Comienza con `LEVERAGE=1` o `LEVERAGE=2`

---

## 🧪 Ejecutar localmente

```bash
pip install -r requirements.txt
cp .env.example .env
# Edita .env con tus datos

# Modo demo (recomendado para empezar)
DEMO_MODE=true python bot.py

# Modo real
DEMO_MODE=false python bot.py
```

---

## 📁 Estructura del proyecto

```
trading-bot/
├── bot.py           # Motor principal del bot
├── strategy.py      # Indicadores (ZLEMA + Reversión)
├── bingx_client.py  # Cliente API BingX
├── requirements.txt
├── Procfile         # Para Railway
├── railway.json     # Config Railway
└── .env.example     # Variables de entorno
```

---

## 🔧 Parámetros ajustables

| Parámetro | Defecto | Descripción |
|-----------|---------|-------------|
| `ZLEMA_LENGTH` | 70 | Ventana ZLEMA (Pine: 70) |
| `BAND_MULT` | 1.2 | Grosor de bandas (Pine: 1.2) |
| `OSC_PERIOD` | 50 | Período oscilador (vídeo: 50) |
| `ENTRY_MAX_PROB` | 0.30 | Prob. máx. para entrar |
| `EXIT_PROB` | 0.84 | Prob. mín. para salir |
| `CHECK_INTERVAL` | 60 | Segundos entre ciclos |
