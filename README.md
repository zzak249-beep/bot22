# BB+RSI DCA Bot — BingX Futuros

Bot de trading automatico para BingX Futuros.
**Estrategia:** Bollinger Bands + RSI Oversold  
**Resultado backtest 2022-2024:** $15,228 con $200 capital base

---

## Resultado backtest (referencia)

| Año | PnL | Win Rate | Profit Factor |
|-----|-----|----------|---------------|
| 2022 (bajista) | +$5,084 ✅ | 57.3% | 4.02 |
| 2023 | +$5,160 ✅ | 65.4% | 5.67 |
| 2024 (alcista) | +$4,984 ✅ | 59.8% | 4.47 |

---

## Instalacion rapida (Railway + GitHub)

### Paso 1 — Subir a GitHub

```bash
git init
git add .
git commit -m "BB+RSI bot inicial"
git remote add origin https://github.com/TU_USUARIO/bbrsi-bot.git
git push -u origin main
```

### Paso 2 — Crear bot de Telegram

1. Abre Telegram → busca **@BotFather** → `/newbot`
2. Elige nombre y username → copia el **token**
3. Busca **@userinfobot** → copia tu **chat ID**

### Paso 3 — Obtener API Keys BingX

1. Entra a [bingx.com](https://bingx.com)
2. Perfil → **API Management** → Crear API Key
3. Permisos: ✅ Read + ✅ Trade — ❌ SIN retiro
4. Copia API Key y Secret

### Paso 4 — Deploy en Railway

1. Ve a [railway.app](https://railway.app) → New Project → Deploy from GitHub
2. Selecciona tu repositorio `bbrsi-bot`
3. Ve a **Settings → Variables** y agrega:

```
BINGX_API_KEY     = tu_api_key
BINGX_SECRET      = tu_secret
TG_TOKEN          = tu_token_telegram
TG_CHAT_ID        = tu_chat_id
RISK_PCT          = 0.02
LEVERAGE          = 3
MAX_POSITIONS     = 3
```

4. Railway detecta el `Procfile` automaticamente y arranca el bot.

---

## Uso local (pruebas)

```bash
# Instalar dependencias
pip install -r requirements.txt

# Copiar y configurar .env
cp .env.template .env
# edita .env con tus claves

# Ejecutar
python main.py
```

---

## Como funciona

```
Cada 5 minutos para cada par:
  ┌─ ¿Precio < Banda Inferior BB? + RSI < 65?
  │    SI → ¿Hay fondos en BingX?
  │           SI → Abre LONG + Telegram ✅
  │           NO → Señal manual por Telegram ⚠️
  │
  └─ ¿Posicion abierta?
       ¿Precio > Media BB?  → Cerrar (TP)
       ¿Precio < Stop Loss? → Cerrar (SL)
```

**Si no hay fondos suficientes**, el bot envia por Telegram:
- El par y precio de entrada
- El stop loss recomendado
- Instrucciones para entrar manualmente en BingX

---

## Parametros configurables

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `BB_PERIOD` | 30 | Periodo media movil BB |
| `BB_SIGMA` | 1.8 | Desviacion estandar BB |
| `RSI_OB` | 65 | RSI maximo para entrar |
| `RISK_PCT` | 0.02 | 2% del balance por trade |
| `LEVERAGE` | 3 | Apalancamiento (3x conservador) |
| `MAX_POSITIONS` | 3 | Maximo posiciones simultáneas |
| `LOOP_SECONDS` | 300 | Frecuencia de revision (5 min) |

---

## Avisos importantes

- El backtest **no incluye comisiones** (~0.05% BingX). Restar $30-60 por año.
- Los resultados reales seran menores que el backtest por slippage.
- Empieza con el minimo ($200) hasta validar en vivo.
- Nunca arriesgues dinero que no puedas perder.
