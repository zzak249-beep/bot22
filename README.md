# SATY Elite v11 — BingX + Telegram en Railway

Bot de trading para BingX Perpetual Futures con alertas completas en Telegram.
Desplegado en Railway para ejecución 24/7 en la nube.

---

## 🚀 INSTALACIÓN PASO A PASO

### PASO 1 — Subir a GitHub

1. Ve a **github.com** e inicia sesión (crea cuenta si no tienes)
2. Click en **"New repository"** (botón verde)
3. Nombre: `saty-trading-bot`
4. Visibilidad: **Private** ⚠️ (nunca público con código de trading)
5. Click **"Create repository"**
6. En la página del repo vacío, click **"uploading an existing file"**
7. **Arrastra estos 6 archivos** al área de subida:
   - `main.py`
   - `saty_elite_v11.py`
   - `requirements.txt`
   - `Procfile`
   - `railway.json`
   - `.gitignore`
8. Click **"Commit changes"**

✅ Tu código ya está en GitHub

---

### PASO 2 — Desplegar en Railway

1. Ve a **railway.app** e inicia sesión con tu cuenta de GitHub
2. Click **"New Project"**
3. Selecciona **"Deploy from GitHub repo"**
4. Elige tu repo `saty-trading-bot`
5. Railway detectará el `Procfile` automáticamente
6. **NO hagas deploy todavía** — primero configura las variables

---

### PASO 3 — Configurar Variables de Entorno en Railway

En tu proyecto Railway:
1. Click en el servicio creado
2. Click en la pestaña **"Variables"**
3. Click **"Add Variable"** y añade CADA UNA de estas:

```
BINGX_API_KEY          → tu API Key de BingX
BINGX_API_SECRET       → tu API Secret de BingX
TELEGRAM_BOT_TOKEN     → token de tu bot Telegram (de @BotFather)
TELEGRAM_CHAT_ID       → tu Chat ID (de @userinfobot)
FIXED_USDT             → 8
MAX_OPEN_TRADES        → 12
MIN_SCORE              → 4
MAX_DRAWDOWN           → 15
DAILY_LOSS_LIMIT       → 8
BTC_FILTER             → true
COOLDOWN_MIN           → 20
MAX_SPREAD_PCT         → 1.0
MIN_VOLUME_USDT        → 100000
TOP_N_SYMBOLS          → 300
POLL_SECONDS           → 60
```

4. Tras añadir todas, click **"Deploy"**

---

### PASO 4 — Verificar que funciona

1. En Railway, click en la pestaña **"Logs"**
2. Deberías ver:
   ```
   SATY ELITE v11 — BingX Real Money + Telegram
   Variables: OK
   BingX conectado ✓
   Universo: XXX pares
   ```
3. En **Telegram** recibirás el mensaje de inicio:
   ```
   🚀 SATY ELITE v11 — ONLINE
   Balance: $XXX.XX USDT
   ...
   ```

---

## 📱 MENSAJES QUE RECIBIRÁS EN TELEGRAM

| Evento | Cuándo |
|--------|--------|
| 🚀 **ONLINE** | Al arrancar el bot |
| 🟢/🔴 **LONG/SHORT** | Al abrir cada trade (con score, TP1, TP2, SL, RSI, ADX) |
| 🟡 **TP1 + BREAK-EVEN** | Cuando el precio toca TP1 y el SL se mueve a entrada |
| 🏃/⚡/🔒 **TRAILING** | Cambio de fase del trailing stop |
| ✅/❌ **CERRADO** | Al cerrar cualquier trade (con PnL, W/L, totales) |
| 🔔 **RSI EXTREMO** | RSI entre 10-25 o 78-90 (alerta, no entrada) |
| ₿ **BTC FLIP** | Cuando BTC cambia de alcista a bajista o viceversa |
| 💓 **HEARTBEAT** | Cada hora (estado del bot, balance, posiciones) |
| 📡 **RESUMEN** | Cada 20 scans (top señales, posiciones, estadísticas) |
| 🚨 **CIRCUIT BREAKER** | Si el drawdown supera MAX_DRAWDOWN% |
| 🚨 **LÍMITE DIARIO** | Si las pérdidas del día superan DAILY_LOSS_LIMIT% |
| 🔥 **ERROR** | Si hay algún error crítico |

---

## 💰 COSTES RAILWAY

- **Hobby Plan**: $5/mes — suficiente para este bot
- Incluye 512MB RAM y CPU compartida
- El bot usa ~150MB RAM

---

## ⚙️ OBTENER API KEYS BINGX

1. bingx.com → tu cuenta → **API Management**
2. **Create API** → nombre: `saty-bot`
3. Permisos: ✅ Read, ✅ Futures Trading, ❌ Withdrawal
4. Restringe IP si puedes (Railway IPs: variables)
5. Guarda Key y Secret

---

## 📲 CREAR BOT TELEGRAM

1. Abre Telegram → busca **@BotFather**
2. Escribe `/newbot`
3. Nombre: `Saty Trading`
4. Username: `satymitradingbot` (o cualquiera disponible)
5. Guarda el **TOKEN** que te da
6. Busca **@userinfobot** → escríbele cualquier cosa → guarda tu **Chat ID**

---

## ⚠️ AVISO DE RIESGO

Este bot opera con dinero real usando futuros apalancados.
Puede generar pérdidas. Úsalo bajo tu propia responsabilidad.
Empieza con `FIXED_USDT=5` y `MAX_OPEN_TRADES=3` para probar.
