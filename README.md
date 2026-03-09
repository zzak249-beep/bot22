# 🤖 Binance Arbitrage Bot v2.0

Bot de **arbitraje triangular dinámico** para Binance Spot. Detecta y ejecuta oportunidades en 56 combinaciones de 8 monedas, con gestión de riesgo, notificaciones Telegram y estadísticas. Listo para correr 24/7 en Railway.

---

## 📁 Archivos del proyecto

```
binance-arb-bot/
├── bot.py            ← lógica completa del bot
├── requirements.txt  ← dependencias Python
├── railway.toml      ← configuración de deploy
├── Procfile          ← comando de inicio
├── .env.example      ← template de variables (renombrar a .env)
├── .gitignore        ← protege .env y logs
└── README.md
```

---

## 🧠 Cómo funciona

```
USDT ──► BTC ──► ETH ──► USDT
         fee×3 descontados en cada paso
         Si resultado > USDT inicial → ejecuta
```

El bot escanea **56 triángulos** cada 3 segundos usando los precios en tiempo real de Binance. Si detecta ganancia neta ≥ `MIN_PROFIT_PCT` después de todos los fees, ejecuta las 3 órdenes de mercado en secuencia.

### Monedas monitoreadas
`BTC · ETH · BNB · SOL · XRP · ADA · AVAX · MATIC`

---

## 🚀 Deploy en Railway — paso a paso

### 1. Clona y sube a GitHub

```bash
git init
git add .
git commit -m "arbitrage bot v2"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/binance-arb-bot.git
git push -u origin main
```

### 2. Conecta Railway

1. Ve a [railway.app](https://railway.app) → **New Project**
2. Haz click en **Deploy from GitHub repo**
3. Selecciona tu repositorio `binance-arb-bot`
4. Railway detecta el `railway.toml` automáticamente ✅

### 3. Variables de entorno en Railway

Ve a tu proyecto → pestaña **Variables** → añade:

| Variable | Valor |
|---|---|
| `BINANCE_API_KEY` | tu API key |
| `BINANCE_API_SECRET` | tu API secret |
| `DRY_RUN` | `true` (para pruebas) |
| `MIN_PROFIT_PCT` | `0.25` |
| `TRADE_AMOUNT` | `20` |
| `MAX_DAILY_LOSS_USDT` | `50` |
| `MAX_TRADES_PER_HOUR` | `10` |
| `FEE_RATE` | `0.001` |
| `TELEGRAM_TOKEN` | (opcional) |
| `TELEGRAM_CHAT_ID` | (opcional) |

### 4. Deploy

Railway desplegará automáticamente. Haz click en **View Logs** para ver el bot en acción.

---

## 🔑 Crear API Keys en Binance

1. Ve a [Binance → Gestión de API](https://www.binance.com/es/my/settings/api-management)
2. Haz click en **Crear API**
3. Activa **solo**: `Enable Spot & Margin Trading`
4. **NO actives** permisos de retiro (seguridad crítica)
5. Guarda la API Key y el Secret — no los verás de nuevo

---

## 📱 Configurar Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → sigue los pasos
2. Copia el token → `TELEGRAM_TOKEN`
3. Envía cualquier mensaje a tu nuevo bot
4. Abre en el navegador: `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Copia el número dentro de `"id"` en el objeto `"chat"` → `TELEGRAM_CHAT_ID`

Recibirás:
- ✅ Alerta por cada trade ejecutado
- 📊 Resumen de estadísticas cada 30 minutos
- 🛑 Notificación cuando el bot para o es pausado por riesgo

---

## ⚙️ Parámetros explicados

| Variable | Descripción | Default |
|---|---|---|
| `DRY_RUN` | Simulación sin dinero real | `true` |
| `MIN_PROFIT_PCT` | Ganancia mínima para ejecutar | `0.25%` |
| `TRADE_AMOUNT` | USDT movidos en cada triángulo | `20` |
| `LOOP_INTERVAL` | Segundos entre ciclos de escaneo | `3` |
| `FEE_RATE` | Fee por orden (0.075% con BNB) | `0.001` |
| `MAX_DAILY_LOSS_USDT` | Pérdida máxima diaria antes de pausa | `$50` |
| `MAX_TRADES_PER_HOUR` | Límite de trades por hora | `10` |

---

## ⚠️ Gestión de riesgo

- Bot se **pausa automáticamente** al alcanzar `MAX_DAILY_LOSS_USDT`
- Límite de `MAX_TRADES_PER_HOUR` evita over-trading
- `DRY_RUN=true` por defecto — nunca opera dinero real sin confirmar
- Si Railway reinicia el servicio, el bot retoma sin riesgo

---

## 🛠️ Probar en local

```bash
git clone https://github.com/TU_USUARIO/binance-arb-bot
cd binance-arb-bot
pip install -r requirements.txt
cp .env.example .env
# Edita .env con tus datos
python bot.py
```

---

## ⚠️ Disclaimer

El arbitraje triangular en Binance es altamente competitivo. Los spreads son pequeños y se cierran en milisegundos. Empieza **siempre** con `DRY_RUN=true` durante varios días antes de usar dinero real. Nunca inviertas más de lo que puedas permitirte perder.
