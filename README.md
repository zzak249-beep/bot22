# 🤖 BingX Trading Bot

Bot de trading automático para **BingX Futuros Perpetuos** con señales en Telegram.  
Deploy listo para **Railway**. Estrategias de reversión a la media y scalping.

---

## 📊 Estrategias Implementadas

| # | Estrategia | Timeframe | Tipo |
|---|-----------|-----------|------|
| 1 | **VWAP + Bandas SD** | 15m | Mean Reversion |
| 2 | **Bollinger Bands + RSI** | 15m | Mean Reversion |
| 3 | **EMA Ribbon 9/15** | 5m | Scalping |

### Estrategia 1: VWAP Mean Reversion
- **SHORT**: precio toca +2SD o +3SD con vela bajista → TP en VWAP
- **LONG**: precio toca -2SD o -3SD con vela alcista → TP en VWAP
- Filtro: volatilidad mínima y vela de rechazo confirmada

### Estrategia 2: BB + RSI
- **LONG**: BB inferior + RSI < 30 + vela alcista → TP en BB Media (SMA20)
- **SHORT**: BB superior + RSI > 70 + vela bajista → TP en BB Media
- R:R de 0.75 para maximizar winrate (Bulkowski style)

### Estrategia 3: EMA Ribbon Scalping (5m)
- Filtro macro: MA200 define dirección
- **LONG**: EMA9 > EMA15 + pullback a EMA + RSI > 50 + precio > MA200
- **SHORT**: EMA9 < EMA15 + pullback a EMA + RSI < 50 + precio < MA200

---

## 🚀 Setup Rápido

### 1. Clonar el repositorio
```bash
git clone https://github.com/tu-usuario/bingx-trading-bot.git
cd bingx-trading-bot
```

### 2. Configurar variables de entorno
```bash
cp .env.example .env
# Edita .env con tus credenciales
nano .env
```

### 3. Obtener API Key de BingX
1. Ir a [BingX](https://bingx.com) → Cuenta → Gestión de API
2. Crear nueva API Key con permisos de **Futuros**
3. Guardar `API Key` y `Secret Key`
4. ⚠️ Whitelist de IPs si usas Railway: añade la IP de tu servicio

### 4. Crear Bot de Telegram
1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copia el **Token**
3. Obtén tu **Chat ID**: habla con [@userinfobot](https://t.me/userinfobot)

---

## ☁️ Deploy en Railway

### Opción A: Desde GitHub (recomendado)
1. Push del proyecto a GitHub
2. Ve a [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
3. Selecciona el repositorio
4. En **Variables**, añade todas las del `.env.example`:

```
BINGX_API_KEY=...
BINGX_API_SECRET=...
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
TRADING_PAIRS=BTC-USDT,ETH-USDT,SOL-USDT
LEVERAGE=5
RISK_PER_TRADE=1.0
MAX_POSITIONS=3
MAX_DAILY_LOSS=3.0
TESTNET=false
```

5. Railway detecta el `Dockerfile` automáticamente → Deploy

### Opción B: Railway CLI
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

---

## 🏃 Ejecución Local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Configurar .env
cp .env.example .env
# editar .env...

# Ejecutar
python main.py
```

---

## 📱 Comandos de Telegram

| Comando | Descripción |
|---------|-------------|
| `/start` | Información del bot |
| `/status` | Estado actual (pares, leverage, posiciones) |
| `/balance` | Balance en BingX Futuros |
| `/trades` | Posiciones abiertas actualmente |
| `/pairs` | Lista de pares monitoreados |
| `/stop` | Señal de parada |

---

## ⚙️ Variables de Configuración

| Variable | Default | Descripción |
|----------|---------|-------------|
| `TESTNET` | `false` | `true` = sin trades reales |
| `LEVERAGE` | `5` | Apalancamiento (recomendado: 3-10x) |
| `RISK_PER_TRADE` | `1.0` | % del balance por trade |
| `MAX_POSITIONS` | `3` | Máx posiciones simultáneas |
| `MAX_DAILY_LOSS` | `3.0` | % pérdida diaria para parar |
| `MEAN_REV_TIMEFRAME` | `15m` | TF para VWAP y BB+RSI |
| `SCALPING_TIMEFRAME` | `5m` | TF para EMA Ribbon |
| `ANALYSIS_INTERVAL` | `60` | Segundos entre análisis |
| `VWAP_MIN_BAND` | `2` | Banda mínima VWAP para señal |
| `RSI_OVERSOLD` | `30` | RSI nivel sobreventa |
| `RSI_OVERBOUGHT` | `70` | RSI nivel sobrecompra |

---

## 🗂 Estructura del Proyecto

```
bingx-trading-bot/
├── main.py                      # Punto de entrada
├── exchange/
│   └── bingx_client.py         # Cliente API BingX (con firma HMAC)
├── strategies/
│   ├── vwap_strategy.py        # VWAP + Bandas SD
│   └── bb_rsi_strategy.py      # BB+RSI + EMA Ribbon
├── bot/
│   ├── trader.py               # Lógica de trading y ejecución
│   └── telegram_bot.py         # Bot Telegram + comandos
├── utils/
│   ├── risk_manager.py         # Gestión de riesgo
│   └── logger.py               # Logging
├── .env.example                # Plantilla de variables
├── Dockerfile                  # Para Railway/Docker
├── railway.toml                # Config Railway
└── requirements.txt
```

---

## ⚠️ Advertencias Importantes

> **DINERO REAL**: Este bot opera con fondos reales en BingX. Entiende el riesgo antes de activarlo.

- Empieza siempre con **`TESTNET=true`** para verificar que todo funciona
- Usa un leverage **conservador** (3-5x máximo al comenzar)
- El `RISK_PER_TRADE=1.0%` es la configuración segura estándar
- Ninguna estrategia garantiza ganancias. El trading conlleva riesgo de pérdida total
- Las estrategias funcionan mejor en **mercados laterales/ranging**, no en tendencias fuertes
- Revisa los logs regularmente en Railway

---

## 📋 Checklist Antes de Activar

- [ ] API Key de BingX configurada con permisos de Futuros
- [ ] Bot de Telegram funcionando (probado con `/start`)
- [ ] Probado con `TESTNET=true` al menos 24h
- [ ] Balance suficiente en cuenta de Futuros BingX
- [ ] Entiendes las estrategias y sus condiciones de entrada/salida
- [ ] Leverage y riesgo configurados conservadoramente
