# ⚡ QUICK START GUIDE - Bot Institucional v2.0

## 🚀 Inicio Rápido en 5 Minutos

### Paso 1: Obtener API Keys de BingX (2 min)

1. **Login** a [BingX](https://bingx.com)
2. Ve a **API Management** (Gestión de API)
3. Click **Create API Key**
4. **IMPORTANTE**: 
   - ✅ Activa **Futures Trading**
   - ✅ Activa **Whitelist IP** (IP de Railway)
   - ❌ NO actives **Withdrawals**
5. Copia tu **API Key** y **Secret Key**

---

### Paso 2: Configurar Bot (2 min)

Renombra `.env.institutional` a `.env`:
```bash
mv .env.institutional .env
```

Edita `.env` y pega tus keys:
```bash
# Pega aquí tus keys de BingX
BINGX_API_KEY="tu_api_key_de_bingx"
BINGX_API_SECRET="tu_secret_key_de_bingx"

# CONFIGURACIÓN INICIAL RECOMENDADA (NO CAMBIAR)
AUTO_TRADING_ENABLED=true
POSITION_SIZE_USD=10           # Empieza con $10
LEVERAGE=2                      # Conservador
MAX_POSITIONS=2                 # Solo 2 posiciones al inicio
ACCOUNT_EQUITY=100
```

---

### Paso 3: Telegram (Opcional - 1 min)

Para recibir notificaciones en tu móvil:

1. Habla con [@BotFather](https://t.me/BotFather)
2. Envía `/newbot` y sigue instrucciones
3. Copia el **token** que te da
4. Envía un mensaje a tu bot
5. Ve a `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
6. Busca tu `chat_id` (es un número)
7. Pega en `.env`:

```bash
TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
TELEGRAM_CHAT_ID="123456789"
```

---

### Paso 4: Ejecutar Bot

```bash
python institutional_bot_v2.py
```

O renombrar a `main.py` para Railway:
```bash
mv institutional_bot_v2.py main.py
python main.py
```

---

## 📊 ¿Qué Verás?

### Output Inicial
```
═══════════════════════════════════════════════════════════════════
🏆 INSTITUTIONAL BOT v2.0
═══════════════════════════════════════════════════════════════════
Capital: $10 × 2 posiciones | 2×
Filtros: Funding=✓ | OI=✓ | Session=✓
TPs: 35%@1.2RR | 35%@2.2RR | 30%@trail
Min Edge: 3.0× costes | Circuit Breaker: 6.0%
═══════════════════════════════════════════════════════════════════
✓ BingX conectado | Equity: $105.34
✓ Contratos cargados: 247
✓ Símbolos activos: 50
✓ Posiciones recuperadas: 0
═══════════════════════════════════════════════════════════════════

🚀 Institutional Bot v2.0 RUNNING
```

### Durante Operación
```
═══════════════════════════════════════════════════════════════════
#1 14:23:15 UTC | Positions: 0/2
PnL: $+0.00 | Today: $+0.00 | WR: 0%
═══════════════════════════════════════════════════════════════════

Scanning 50 symbols...
💡 ETH-USDT | Score: 85 | Edge: 4.2×
✓ Scan complete | Signals: 1

═══════════════════════════════════════════════════════════════════
🎯 OPENING LONG: ETH-USDT
Score: 85 | Edge: 4.2×
Entry: $2543.21 | SL: $2512.34 (-1.21%)
TP1: $2573.45 (35%) | TP2: $2618.92 (35%)
Session: us_session | Funding: 0.021%
CVD: bullish_cvd
═══════════════════════════════════════════════════════════════════
```

### En Telegram (si configurado)
```
🟢 LONG OPENED

ETH-USDT
Score: 85 | Edge: 4.2×

📍 Entry: $2543.21
🎯 TP1 (35%): $2573.45
🎯 TP2 (35%): $2618.92
🛑 SL: $2512.34 (-1.21%)

📊 Funding: 0.021%
📊 CVD: bullish_cvd
🕐 Session: us_session

✅ SL Placed
```

---

## 🎮 Primeras 24 Horas - Qué Esperar

### ✅ NORMAL:

**Pocas señales (2-4 por día)**
- Es BUENO - calidad > cantidad
- Session filter está trabajando
- Solo opera US/London hours

**Algunas señales rechazadas**
```
ETH-USDT: ❌ Funding=0.048% (funding_high)
BTC-USDT: ❌ OI divergence (oi_divergence_weak)
```
- Filtros protegiendo tu capital
- Evitando trampas

**No todas las posiciones ganan**
- Win Rate objetivo: 60-68%
- Significa 3-4 de cada 10 pierden
- Es estadística, no fallo

---

### ⚠️ REVISAR SI:

**"❌ Sin conexión BingX"**
→ API keys incorrectas o permisos faltantes

**"Circuit breaker activado"**
→ Normal si pérdidas >6% en un día
→ Protección funcionando

**"Funding rate muy alto siempre"**
→ Normal en mercados alcistas fuertes
→ Bot esperando mejores condiciones

**"No encuentra señales"**
→ Verifica que estás en US/London session (13-22 UTC)

---

## 📊 Primeros Resultados (Semana 1)

### Expectativa realista con $100 capital:

```
Día 1-2:  1-2 trades, ±$0-2
Día 3-5:  2-4 trades, +$1-4
Día 6-7:  1-3 trades, +$0-3
─────────────────────────────
Semana 1: ~6-10 trades
Win Rate: ~55-65%
PnL:      +$2-8 (+2-8%)
```

**Es NORMAL tener:**
- Días sin trades (horarios, filtros)
- Racha de 2-3 pérdidas (estadística)
- Variación día a día

**NO es normal:**
- Win Rate <45% → revisar configuración
- Circuit breaker cada 2 días → bajar LEVERAGE
- 0 trades en 3 días → revisar filtros

---

## ⚙️ Ajustes Según Resultados

### Si Win Rate <50% después de 20 trades:

**Aumentar selectividad:**
```bash
MIN_ENTRY_SCORE=75        # Era 70
MIN_EDGE_RATIO=3.5        # Era 3.0
```

**Verificar filtros activos:**
```bash
FUNDING_FILTER=true       # ✓
OI_FILTER=true           # ✓
SESSION_FILTER=true      # ✓
```

---

### Si muy pocas señales (<2/día):

**Aumentar alcance:**
```bash
MAX_SYMBOLS=70            # Era 50
MIN_ENTRY_SCORE=65        # Era 70
```

**O desactivar 1 filtro (no recomendado):**
```bash
OI_FILTER=false          # Solo si necesario
```

---

### Si demasiadas señales pero baja calidad:

**Aumentar exigencia:**
```bash
MIN_ENTRY_SCORE=80        # Era 70
CVD_THRESHOLD=2.0         # Era 1.5
OI_BREAKOUT_MIN=2.0       # Era 1.5
```

---

## 🔧 Comandos Útiles

### Ver logs en tiempo real:
```bash
tail -f /tmp/bot.log      # Si rediriges output
# O simplemente:
python main.py
```

### Detener bot:
```
Ctrl + C
```

### Verificar posiciones en BingX:
1. Login a BingX
2. Futures → Positions
3. Verificar que coinciden con bot

### Cerrar todas las posiciones manualmente:
```
BingX → Positions → Close All
```
(Solo en emergencias)

---

## 🆘 Solución Rápida de Problemas

### Problema: "API keys no configuradas"
```bash
# Verificar .env
cat .env | grep BINGX_API_KEY

# Debe mostrar tu key, no "your_api_key_here"
```

### Problema: "No se ejecutan órdenes"
```bash
# 1. Verifica fondos en Futures
BingX → Futures → Transfer from Spot to Futures

# 2. Verifica API permissions
BingX → API Management → Edit → Futures Trading ✓
```

### Problema: "Funding rate siempre alto"
```bash
# Es normal en bull markets
# El bot está protegiendo tu capital
# Solución: esperar o bajar threshold
FUNDING_LONG_SKIP=0.08     # Era 0.05
```

### Problema: "Circuit breaker muy frecuente"
```bash
# Bajar riesgo:
LEVERAGE=2                  # Era 3
POSITION_SIZE_USD=8         # Era 10
CIRCUIT_BREAKER_PCT=8.0     # Era 6.0
```

---

## 📱 Siguiente Nivel: Optimización

Una vez funcionando 1 semana:

1. **Revisar estadísticas** (win rate, avg RR)
2. **Ajustar config** basado en datos reales
3. **Aumentar capital** gradualmente
4. **Añadir más posiciones** (max 4)
5. **Experimentar con leverage** (max 3×)

---

## ⚠️ RECORDATORIOS CRÍTICOS

🔴 **Solo opera con dinero que puedes perder**
🔴 **Empieza con $10-20 por posición**
🔴 **No modifiques posiciones manualmente**
🔴 **Monitorea daily PnL**
🔴 **Retira profits cada semana**

---

## 🎯 Meta Realista Primer Mes

Con $100 capital inicial:

```
Semana 1: +$2-8    (aprendizaje)
Semana 2: +$5-12   (ajustando)
Semana 3: +$8-15   (optimizado)
Semana 4: +$10-18  (consistente)
─────────────────────────────────
Mes 1:    +$25-53  (+25-53%)
```

**Esto NO es garantizado**. Es expectativa estadística basada en:
- Win Rate 60-68%
- RR 1.8-2.5×
- 4-8 señales/día
- Filtros activos

Resultados reales varían según mercado.

---

## 📚 Recursos

- **README completo**: `README_INSTITUTIONAL.md`
- **Configuración BingX**: `BINGX_OPTIMAL_CONFIG.md`
- **Código**: `institutional_bot_v2.py`
- **.env ejemplo**: `.env.institutional`

---

## ✅ Checklist Final

Antes de operar con dinero real:

- [ ] API Keys configuradas y verificadas
- [ ] Fondos transferidos a Futures wallet
- [ ] `.env` editado con tus valores
- [ ] Bot ejecutándose sin errores
- [ ] Telegram funcionando (opcional)
- [ ] Entiendes qué hace cada filtro
- [ ] Sabes cómo detener el bot
- [ ] Conoces los riesgos
- [ ] Capital que puedes perder

**Si todos ✓ → ¡Estás listo!**

---

**🏆 ¡Bienvenido al trading institucional!**

*Remember: El edge está en los filtros, no en la cantidad de trades.*

---

*Last updated: 2026-04-10*
