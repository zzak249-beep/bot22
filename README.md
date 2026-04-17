# AEGIS GEX Bot — Dealer Flow Engine 🤖

Bot de trading automático que recibe señales del indicador **AEGIS GEX** en TradingView, ejecuta órdenes en **BingX Futuros Perpetuos** y notifica cada operación en **Telegram**.

---

## Estructura del repositorio

```
aegis-gex-bot/
├── app.py              ← Cerebro del bot (Flask + ccxt + Telegram)
├── requirements.txt    ← Dependencias Python
├── Procfile            ← Instrucción de arranque para Railway
├── .env.example        ← Plantilla de variables de entorno
├── .gitignore          ← Protege tus claves API
└── README.md           ← Este archivo
```

---

## Guía de instalación paso a paso

### 1. Subir a GitHub

```bash
git init
git add .
git commit -m "feat: AEGIS GEX Bot inicial"
git remote add origin https://github.com/TU_USUARIO/aegis-gex-bot.git
git push -u origin main
```

> Crea el repositorio como **privado** en GitHub antes de hacer el push.

---

### 2. Crear el Bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram.
2. Escribe `/newbot` y sigue las instrucciones.
3. Guarda el **TOKEN** que te da (formato: `123456:ABCDEF...`).
4. Para obtener tu **CHAT_ID**:
   - Habla con [@userinfobot](https://t.me/userinfobot) y te lo dirá.
   - O si es un canal, añade el bot como admin y usa la API para obtenerlo.

---

### 3. Obtener las API Keys de BingX

1. Entra en [BingX](https://bingx.com) → Perfil → API Management.
2. Crea una API Key con permisos de **Trading** (no actives retiros).
3. Si BingX pide una IP fija, Railway usa IPs dinámicas → deja la IP en blanco o usa un proxy fijo.
4. Guarda tu `API Key` y `Secret Key`.

---

### 4. Desplegar en Railway

1. Ve a [Railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**.
2. Selecciona tu repositorio `aegis-gex-bot`.
3. Railway detecta el `Procfile` automáticamente.
4. Ve a **Settings → Variables** y añade estas variables:

| Variable | Valor |
|---|---|
| `BINGX_API_KEY` | Tu API key de BingX |
| `BINGX_SECRET_KEY` | Tu secret key de BingX |
| `TELEGRAM_TOKEN` | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | Tu chat ID o ID del canal |
| `WEBHOOK_SECRET` | Una clave secreta larga (inventada por ti) |
| `ORDER_SIZE` | Ej: `0.01` (tamaño de cada orden) |
| `LEVERAGE` | Ej: `5` (apalancamiento) |
| `MODE` | `paper` para pruebas, `live` para real |

5. Railway te dará una URL pública como `https://tu-bot.up.railway.app`.

---

### 5. Configurar alertas en TradingView

#### Para cada señal del indicador AEGIS GEX:

1. En el gráfico, haz clic en el reloj (Alertas) → **Crear Alerta**.
2. En **Condición**, selecciona el indicador AEGIS GEX y la señal.
3. En **Acciones**, activa **Webhook URL** y pega:

```
https://tu-bot.up.railway.app/webhook
```

4. En el campo **Mensaje** pega el JSON correspondiente:

**Para señal LONG (Wall Break / GEX Flip Cross alcista):**
```json
{
  "signal": "long",
  "ticker": "{{ticker}}",
  "price": "{{close}}",
  "regime": "POSITIVE GAMMA",
  "dgrp_score": "{{plot_0}}",
  "gex_flip": "{{plot_1}}"
}
```

**Para señal SHORT (Wall Break / GEX Flip Cross bajista):**
```json
{
  "signal": "short",
  "ticker": "{{ticker}}",
  "price": "{{close}}",
  "regime": "NEGATIVE GAMMA",
  "dgrp_score": "{{plot_0}}",
  "gex_flip": "{{plot_1}}"
}
```

**Para cerrar posición:**
```json
{
  "signal": "close",
  "ticker": "{{ticker}}",
  "price": "{{close}}"
}
```

5. En **Webhook Headers** añade:
```
X-Webhook-Secret: TU_WEBHOOK_SECRET
```

---

## Señales soportadas

| Señal JSON | Acción |
|---|---|
| `long` / `buy` | Abre posición LONG |
| `short` / `sell` | Abre posición SHORT |
| `wall_break_long` | Ruptura muro alcista → LONG |
| `wall_break_short` | Ruptura muro bajista → SHORT |
| `gex_flip_cross_long` | Cruce GEX Flip + VWMA alcista → LONG |
| `gex_flip_cross_short` | Cruce GEX Flip + VWMA bajista → SHORT |
| `vanna_unwind_long` | Vanna Unwind alcista → LONG |
| `vanna_unwind_short` | Vanna Unwind bajista → SHORT |
| `compression_break_long` | Ruptura compresión alcista → LONG |
| `compression_break_short` | Ruptura compresión bajista → SHORT |
| `close` | Cierra posición abierta |

---

## Endpoints del bot

| Endpoint | Método | Descripción |
|---|---|---|
| `/` | GET | Health check (el bot está online) |
| `/webhook` | POST | Recibe señales de TradingView |
| `/status` | GET | Ver posiciones abiertas actuales |

---

## Lógica de la estrategia AEGIS GEX implementada

El bot opera siguiendo la filosofía del indicador:

- **GEX Positivo** → Mercado en rango, los dealers estabilizan el precio. El bot opera rebotes.
- **GEX Negativo** → Mercado tendencial y volátil. El bot sigue el momentum.
- **Flip Zone** → El bot recibe señal `close` para salir y esperar confirmación.
- **Always-In-Market** → Al recibir señal contraria, el bot cierra la posición actual automáticamente antes de abrir la nueva.

---

## Consejos de seguridad

- Empieza siempre con `MODE=paper` para verificar que todo funciona.
- Usa cantidades mínimas en `ORDER_SIZE` al pasar a `live`.
- El `WEBHOOK_SECRET` evita que terceros disparen órdenes falsas.
- **Nunca** subas tu `.env` real a GitHub (el `.gitignore` ya lo protege).
- Activa solo permisos de **Trading** en la API de BingX, nunca de retiro.

---

## Verificar que funciona

Prueba el webhook manualmente con curl:

```bash
curl -X POST https://tu-bot.up.railway.app/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: TU_WEBHOOK_SECRET" \
  -d '{"signal":"long","ticker":"BTC/USDT:USDT","price":"65000","regime":"POSITIVE GAMMA","dgrp_score":"28"}'
```

Si todo está bien, recibirás una notificación en Telegram.
