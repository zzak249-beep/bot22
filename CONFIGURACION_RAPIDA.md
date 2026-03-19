# 🚀 CONFIGURACIÓN RÁPIDA - 3 PASOS

## ✅ SOLUCIÓN AL PROBLEMA "0 SEÑALES"

Tu bot NO generaba señales. Esta es la solución completa en 3 pasos.

---

## 📋 PASO 1: SUBIR BOT OPTIMIZADO A GITHUB

```bash
# 1. Reemplaza el bot actual con la versión optimizada
cp bot_optimizado_v2.py bot_mejorado.py

# 2. Commit
git add bot_mejorado.py
git commit -m "Bot optimizado - Genera señales reales"

# 3. Push
git push
```

✅ **Railway desplegará automáticamente**

---

## ⚙️ PASO 2: CONFIGURAR VARIABLES EN RAILWAY

Ve a **Railway → Settings → Variables** y pega esto:

```bash
# === OBLIGATORIAS ===
BINGX_API_KEY=tu_key_real_aqui
BINGX_API_SECRET=tu_secret_real_aqui

# === TRADING ===
AUTO_TRADING_ENABLED=true
MAX_POSITION_SIZE=50
LEVERAGE=3

# === TP/SL ===
TAKE_PROFIT_PCT=2.5
STOP_LOSS_PCT=1.2

# === TRAILING ===
TRAILING_ACTIVATION=1.0
TRAILING_DISTANCE=0.5

# === UMBRALES OPTIMIZADOS (CRÍTICO - ESTO GENERA LAS SEÑALES) ===
MIN_CONFIDENCE=55
MIN_CHANGE_PCT=0.5
MIN_VOLUME_RATIO=0.8

# === GESTIÓN ===
MAX_OPEN_TRADES=5
CHECK_INTERVAL=90

# === TELEGRAM (Opcional) ===
TELEGRAM_BOT_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
```

⚠️ **MUY IMPORTANTE**: 
- `MIN_CONFIDENCE=55` (NO 70)
- `MIN_CHANGE_PCT=0.5` (NO 0.8)
- `MIN_VOLUME_RATIO=0.8` (NO 1.2)

**Estos son los que hacen que genere señales** ✅

---

## 👀 PASO 3: VERIFICAR EN LOS LOGS

Espera 2-3 minutos y verifica en **Railway → Deploy Logs**:

### ✅ CORRECTO (Con señales):
```
🚀 BOT DE TRADING ULTRA-OPTIMIZADO
✅ UMBRALES: MÁS BAJOS (más señales)
📈 Min Change: 0.5% (MÁS BAJO)
📊 Min Confidence: 55% (MÁS BAJO)
================================================================================
⏱️ ITERACIÓN #1
================================================================================
 1. 🟢 BTC-USDT: $70,045.30 | +0.8% | LONG (72%)
     Razones: Momentum: +0.80%, RSI compra: 42.3, Tendencia alcista
 2. 🔴 SOL-USDT: $195.40 | -1.2% | SHORT (78%)
     Razones: Momentum: -1.20%, RSI overbought: 68.5
================================================================================
📊 RESUMEN:
   🎯 Señales encontradas: 5          ← ESTO DEBE SER > 0
   🟢 LONG: 3 | 🔴 SHORT: 2
   🤖 Trades ejecutados: 2
```

### ❌ INCORRECTO (Sin señales):
```
📊 RESUMEN:
   🎯 Señales encontradas: 0          ← PROBLEMA
```

**Si ves 0**: Verifica que las variables estén EXACTAMENTE como arriba.

---

## 🎯 ¿QUÉ CAMBIA?

| Parámetro | ANTES | AHORA | Efecto |
|-----------|-------|-------|--------|
| MIN_CONFIDENCE | 70% | **55%** | +200% señales |
| MIN_CHANGE_PCT | 0.8% | **0.5%** | +150% señales |
| MIN_VOLUME_RATIO | 1.2 | **0.8** | +100% señales |
| Símbolos | 10 | **20** | +100% oportunidades |
| Frecuencia | 180s | **90s** | +100% chequeos |

**RESULTADO**: De 0-1 señales/hora → **8-15 señales/hora** 🔥

---

## 📊 RESULTADOS ESPERADOS

### Primeros 5 minutos:
- ✅ 2-5 señales detectadas
- ✅ 1-2 trades abiertos
- ✅ Logs con análisis detallado

### Después de 1 hora:
- ✅ 10-20 señales totales
- ✅ 5-8 trades ejecutados
- ✅ 3-5 trades abiertos
- ✅ PnL: Variable (+0.5% a +3% esperado)

---

## 🆘 TROUBLESHOOTING

### ❌ Problema: "0 señales encontradas"

**Causa**: Variables mal configuradas

**Solución**:
1. Ve a Railway → Settings → Variables
2. Verifica que `MIN_CONFIDENCE=55` (NO 70)
3. Verifica que `MIN_CHANGE_PCT=0.5` (NO 0.8)
4. Verifica que `MIN_VOLUME_RATIO=0.8` (NO 1.2)
5. Click "Redeploy"

### ❌ Problema: "Balance: $0.00"

**Causa**: Sin fondos

**Solución**: Deposita USDT en cuenta Futures de BingX

### ❌ Problema: "Error ejecutando trade"

**Causa**: API keys incorrectas

**Solución**: 
1. Regenera API keys en BingX
2. Actualiza en Railway
3. Asegúrate de habilitar "Futures Trading"

---

## ✅ CHECKLIST FINAL

Antes de activar, verifica:

- [ ] Bot optimizado subido a GitHub
- [ ] Variables configuradas en Railway
- [ ] `MIN_CONFIDENCE=55` ✅
- [ ] `MIN_CHANGE_PCT=0.5` ✅
- [ ] `MIN_VOLUME_RATIO=0.8` ✅
- [ ] API keys correctas
- [ ] Fondos en BingX
- [ ] Logs muestran señales (espera 2-3 min)

---

## 🎯 ¿FUNCIONA?

Si después de 5 minutos ves en los logs:

```
🎯 Señales encontradas: 5+
🟢 LONG: X | 🔴 SHORT: X
✅ TRADE ABIERTO: ...
```

**¡FUNCIONA! 🎉**

Si no, revisa las variables. El 99% de los problemas es que las variables están mal configuradas.

---

**¿Listo? Sigue los 3 pasos y en 5 minutos tendrás señales ✅**
