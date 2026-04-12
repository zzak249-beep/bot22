# 🚂 INSTITUTIONAL BOT v3.1 — RAILWAY DEPLOYMENT GUIDE

## 🚨 PASO 0: DETÉN EL BOT ACTUAL (URGENTE)

**ANTES DE HACER NADA, DETÉN EL BOT QUE ESTÁ CORRIENDO:**

1. Ve a Railway → Tu proyecto
2. Click en el servicio "bot22" (o como se llame)
3. Click en **"Settings"** (⚙️)
4. Scroll hasta abajo
5. Click en **"Delete Service"** o **"Stop Deployment"**
6. Confirma

**¿Por qué?** El bot v3.0 tiene bugs críticos que causan pérdidas. Cada minuto cuenta.

---

## 📋 PASO 1: PREPARAR ARCHIVOS

Tienes que subir estos 6 archivos a Railway:

```
institutional_bot_v3_fixed.py  ← El bot corregido
requirements.txt               ← Dependencias de Python
Procfile                       ← Le dice a Railway cómo ejecutar el bot
runtime.txt                    ← Versión de Python
.gitignore                     ← Protege archivos sensibles
.env.example                   ← Template de variables (NO subir con claves reales)
```

**⚠️ NO SUBAS NINGÚN ARCHIVO .env CON TUS CLAVES REALES**

---

## 📦 PASO 2: SUBIR ARCHIVOS A RAILWAY

### Opción A: Via GitHub (RECOMENDADO)

1. **Crear repositorio GitHub:**
   ```bash
   # En tu computadora, en una carpeta nueva:
   git init
   git add institutional_bot_v3_fixed.py requirements.txt Procfile runtime.txt .gitignore
   git commit -m "Bot v3.1 fixed - Railway deployment"
   ```

2. **Crear repo en GitHub:**
   - Ve a https://github.com/new
   - Nombre: `institutional-bot-v3`
   - Privado: ✅ (recomendado)
   - Click "Create repository"

3. **Push al repo:**
   ```bash
   git remote add origin https://github.com/TU_USUARIO/institutional-bot-v3.git
   git branch -M main
   git push -u origin main
   ```

4. **Conectar a Railway:**
   - Ve a Railway → New Project
   - "Deploy from GitHub repo"
   - Selecciona `institutional-bot-v3`
   - Click "Deploy"

### Opción B: Via Railway CLI

1. **Instalar Railway CLI:**
   ```bash
   npm i -g @railway/cli
   ```

2. **Login:**
   ```bash
   railway login
   ```

3. **Inicializar proyecto:**
   ```bash
   railway init
   ```

4. **Deploy:**
   ```bash
   railway up
   ```

### Opción C: Arrastra y suelta (MÁS SIMPLE)

1. Ve a Railway → New Project
2. "Empty Project"
3. Click en el proyecto
4. "New" → "Empty Service"
5. Ve a "Settings" → "Source"
6. Click "Connect Repo" → "Local Directory"
7. Arrastra la carpeta con los archivos

---

## ⚙️ PASO 3: CONFIGURAR VARIABLES DE ENTORNO

**CRÍTICO:** Sin estas variables, el bot NO funcionará.

1. En Railway, ve a tu proyecto
2. Click en el servicio
3. Click en **"Variables"** tab
4. Click **"Add Variable"** o **"Raw Editor"**

### Variables OBLIGATORIAS:

```bash
# API Keys (cópialas de BingX)
BINGX_API_KEY=tu_api_key_real_aqui
BINGX_API_SECRET=tu_api_secret_real_aqui

# ⚠️ MODO PAPER (no tocar hasta confirmar que funciona)
AUTO_TRADING_ENABLED=false
```

### Variables RECOMENDADAS (configuración conservadora):

```bash
# Capital
POSITION_SIZE_USD=10
LEVERAGE=2
MAX_POSITIONS=2
ACCOUNT_EQUITY=100
RISK_PCT_PER_TRADE=1.0

# Stop Loss & TP
SL_ATR_MULTIPLIER=1.5
SL_MIN_PCT=0.8
TP1_RISK_REWARD=1.5
TP2_RISK_REWARD=2.5
RUNNER_TRAIL_ATR=2.0

# Filtros
MIN_ENTRY_SCORE=75
MIN_EDGE_RATIO=4.0
VOLUME_BREAKOUT_MULT=1.8

# Circuit Breaker
CIRCUIT_BREAKER_PCT=3.0
MAX_LOSING_STREAK=3
MAX_DAILY_TRADES=8

# Telegram (opcional)
TELEGRAM_BOT_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
```

**Cómo añadirlas:**

Opción 1 - Una por una:
1. Click "+ Add Variable"
2. Key: `BINGX_API_KEY`
3. Value: `tu_api_key`
4. Repeat para cada variable

Opción 2 - Raw Editor (más rápido):
1. Click "Raw Editor"
2. Pega TODAS las variables de arriba
3. Reemplaza los valores con tus datos reales
4. Click "Update Variables"

---

## 🚀 PASO 4: DEPLOY & VERIFICAR

1. **Deploy automático:**
   - Railway debería deployar automáticamente al detectar cambios
   - Si no, click "Deploy" manualmente

2. **Ver logs en tiempo real:**
   ```
   Railway → Tu servicio → Click en el deployment → "View Logs"
   ```

3. **Verificar inicio correcto:**
   
   Deberías ver en los logs:
   ```
   ════════════════════════════════════════════════════════════════════
   🏆 INSTITUTIONAL BOT v3.1 — Phoenix Trader Edition (FIXED)
   ════════════════════════════════════════════════════════════════════
   ⚠️  CRITICAL FIXES APPLIED:
      ✅ Fixed 'highest' KeyError
      ✅ Fixed positionSide parameter
      ✅ Improved error handling
      ✅ More conservative risk settings
   ════════════════════════════════════════════════════════════════════
   ```

4. **Buscar errores:**
   - Si ves "ERROR" o "❌" en los logs → hay un problema
   - Si ves "v3.0" en lugar de "v3.1 FIXED" → archivo incorrecto

---

## 🔍 PASO 5: TESTING (MUY IMPORTANTE)

### Durante las primeras 24 horas:

1. **Revisa logs cada 2-3 horas:**
   ```bash
   # Buscar errores
   grep ERROR en logs de Railway
   
   # Deberías ver:
   ✓ BingX conectado
   ✓ Contratos cargados
   ✓ Símbolos activos
   ✓ Posiciones recuperadas (si las hay)
   ```

2. **Verifica que NO abre posiciones reales:**
   - `AUTO_TRADING_ENABLED=false` → solo simula
   - En logs verás: "📝 PAPER MODE: Would open BTC-USDT"
   - En BingX NO deberían aparecer nuevas posiciones

3. **Monitorea Telegram:**
   - Si configuraste Telegram, deberías recibir:
     - "🏆 BOT v3.1 STARTED (FIXED)"
     - Reportes cada pocas horas
     - NO deberías ver errores repetitivos

### Errores comunes y soluciones:

| Error en logs | Causa | Solución |
|---------------|-------|----------|
| `API keys no configuradas` | Falta `BINGX_API_KEY` | Añadir en Variables |
| `ModuleNotFoundError: requests` | Falta requirements.txt | Verificar archivo |
| `Error monitoring X: 'highest'` | Usando v3.0 en lugar de v3.1 | Resubir v3.1 |
| `API error 109400` | Usando v3.0 | Resubir v3.1 |
| Service crashed | Variables mal configuradas | Revisar Variables tab |

---

## 📊 PASO 6: MONITOREO CONTINUO

### Herramientas de Railway:

1. **Logs en vivo:**
   - Railway → Service → Click en deployment → "View Logs"
   - Actualiza automáticamente

2. **Métricas:**
   - Railway → Service → "Metrics"
   - CPU, RAM, Network usage
   - Si CPU >80% constante → problema

3. **Deployments:**
   - Railway → Service → "Deployments"
   - Historial de deploys
   - Puedes rollback a versión anterior si falla

### Comandos útiles:

```bash
# Ver logs desde CLI
railway logs

# Ver logs con follow
railway logs --follow

# Ver variables configuradas
railway variables

# Redeploy
railway up
```

---

## 🎯 PASO 7: ACTIVAR REAL MONEY (SOLO DESPUÉS DE 1 SEMANA)

**⚠️ NO HAGAS ESTO HASTA:**
- ✅ Paper mode corrió 7+ días sin errores
- ✅ Revisaste logs diariamente
- ✅ Win rate en paper >50%
- ✅ Entiendes cómo funciona cada parámetro

**Cuando estés listo:**

1. Ve a Railway → Variables
2. Encuentra `AUTO_TRADING_ENABLED`
3. Cambia de `false` a `true`
4. **Añade estas variables de seguridad:**
   ```bash
   POSITION_SIZE_USD=10  # Empieza PEQUEÑO
   MAX_POSITIONS=1       # Solo 1 posición al inicio
   CIRCUIT_BREAKER_PCT=2.0  # Circuit breaker MÁS estricto
   ```
5. Click "Update Variables"
6. Railway redeployará automáticamente
7. **MONITOREA CONSTANTEMENTE las primeras horas**

---

## 🆘 TROUBLESHOOTING

### El bot no inicia:

1. Revisa "Build Logs" en Railway
2. Busca errores de sintaxis en Python
3. Verifica que `runtime.txt` tiene `python-3.11.0`
4. Verifica que `Procfile` tiene `worker: python institutional_bot_v3_fixed.py`

### El bot inicia pero no hace nada:

1. Revisa que `AUTO_TRADING_ENABLED=false` (paper mode)
2. En logs deberías ver: "Scanning X symbols..."
3. Si no encuentra señales es NORMAL (filtros estrictos)
4. Espera al menos 1 hora antes de preocuparte

### Sigue viendo errores de v3.0:

1. **CRÍTICO:** Estás usando el archivo incorrecto
2. Borra el servicio en Railway
3. Vuelve a subir asegurándote que es `institutional_bot_v3_fixed.py`
4. Verifica en los logs que dice "v3.1 FIXED"

### Pérdidas en real money:

1. **DETÉN EL BOT INMEDIATAMENTE**
2. Cambia `AUTO_TRADING_ENABLED=true` → `false`
3. Revisa los logs completos
4. Analiza cada trade en BingX
5. NO reactives hasta entender qué pasó

---

## 📞 SOPORTE RAILWAY

Si tienes problemas con Railway específicamente:

- **Discord:** https://discord.gg/railway
- **Docs:** https://docs.railway.app
- **Status:** https://status.railway.app

---

## ✅ CHECKLIST FINAL

Antes de dar por finalizado el deployment:

- [ ] Bot v3.1 FIXED subido correctamente
- [ ] Logs muestran "v3.1 FIXED" y NO "v3.0"
- [ ] Variables de entorno configuradas (mínimo API keys)
- [ ] `AUTO_TRADING_ENABLED=false` (paper mode)
- [ ] No hay errores "KeyError: 'highest'" en logs
- [ ] No hay errores "API 109400: positionSide" en logs
- [ ] Telegram bot enviando notificaciones (si configurado)
- [ ] Service está "Active" en Railway
- [ ] Revisaste logs 2-3 veces en las primeras horas

---

## 🎉 ¡LISTO!

Si completaste todos los pasos:

✅ Bot v3.1 corregido deployado
✅ Bugs críticos eliminados
✅ Configuración conservadora aplicada
✅ Paper mode activado (sin riesgo)
✅ Monitoreo configurado

**Próximos 7 días:**
- Revisa logs diariamente
- NO toques configuración
- NO actives real money
- Solo observa y aprende

**Después de 7 días sin errores:**
- Considera activar real money con $20-30
- Monitorea cada trade manualmente
- Escala gradualmente si todo va bien

---

## 📈 EXPECTATIVAS REALISTAS

Con v3.1 configuración conservadora:

| Métrica | Objetivo | Tu v3.0 | Mejora |
|---------|----------|---------|--------|
| Win Rate | 55-65% | 42.9% ❌ | +12-22% |
| Trades/día | 2-5 | 10+ ❌ | -50% |
| Drawdown | <15% | >95% ❌ | -80% |
| R:R | 2.0-3.0× | ~1.5× ❌ | +33% |

**Recuerda:** No esperes recuperar las pérdidas pasadas rápido. El objetivo es crecer sostenible, no lambo en 1 mes.

---

**¿Dudas? Revisa los logs, lee el CHANGELOG, y testea en paper mode mínimo 1 semana.** 🚀
