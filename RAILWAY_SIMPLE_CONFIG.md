# 🎯 CONFIGURACIÓN MÍNIMA PARA RAILWAY - 100% FUNCIONAL

## ⚡ LA FORMA MÁS SIMPLE

Solo necesitas **3 archivos** para que funcione:

### 1️⃣ `requirements.txt`
```txt
requests==2.31.0
python-dotenv==1.0.0
numpy==1.24.3
scikit-learn==1.3.0
```

### 2️⃣ `Procfile`
```
worker: python main.py
```

### 3️⃣ Todos los archivos `.py`
```
main.py
config.py
bingx_client.py
technical_analysis.py
ml_predictor.py
risk_manager.py
statistics.py
```

---

## 🗑️ ARCHIVOS QUE PUEDES BORRAR SI DAN PROBLEMAS

```
❌ runtime.txt          → Railway auto-detecta Python
❌ .env                 → Usar variables en Railway Settings
❌ trading_bot.db       → Se crea automáticamente
❌ *.log                → Logs se ven en Railway
```

---

## 🚀 DESPLIEGUE EN 3 PASOS

### **PASO 1: Organiza tu repo**

```
tu-repo/
├── main.py                    ✅ NECESARIO
├── config.py                  ✅ NECESARIO
├── bingx_client.py           ✅ NECESARIO
├── technical_analysis.py     ✅ NECESARIO
├── ml_predictor.py           ✅ NECESARIO
├── risk_manager.py           ✅ NECESARIO
├── statistics.py             ✅ NECESARIO
├── requirements.txt          ✅ NECESARIO
├── Procfile                  ✅ NECESARIO
├── README.md                 📄 Opcional
└── .gitignore                📄 Opcional
```

**NO incluir**:
- ❌ .env
- ❌ runtime.txt (solo si da problemas)
- ❌ *.db
- ❌ *.log
- ❌ __pycache__

---

### **PASO 2: Push a GitHub**

```bash
git add .
git commit -m "Bot mejorado - configuración mínima"
git push
```

---

### **PASO 3: Variables en Railway**

En Railway → Settings → Variables:

```
BINGX_API_KEY=your_key_here
BINGX_API_SECRET=your_secret_here
AUTO_TRADING_ENABLED=true
MAX_POSITION_SIZE=100
LEVERAGE=2
ML_ENABLED=true
```

**¡Y YA! Railway desplegará automáticamente** ✅

---

## 📊 LO QUE VERÁS EN LOS LOGS

### ✅ Build exitoso:
```
Installing requirements...
Collecting requests==2.31.0
Collecting python-dotenv==1.0.0
Collecting numpy==1.24.3
Collecting scikit-learn==1.3.0
Successfully installed!
```

### ✅ Bot ejecutándose:
```
================================================================================
🚀 INICIANDO BOT DE TRADING PROFESIONAL
================================================================================
✅ Bot inicializado correctamente

📊 Actualizando lista de símbolos...
✅ 50 símbolos activos
```

---

## ⚠️ ERRORES COMUNES Y SOLUCIONES

### Error: "Could not install Python 3.11.0"
**Solución**: Borra `runtime.txt`

### Error: "ModuleNotFoundError: No module named X"
**Solución**: Verifica `requirements.txt` tiene las 4 dependencias

### Error: "BINGX_API_KEY not found"
**Solución**: Añade variables en Railway Settings

### Error: "Process failed with exit code 1"
**Solución**: Revisa los logs completos, probablemente faltan variables de entorno

---

## 🎯 CONFIGURACIÓN RECOMENDADA

### Para **TESTEO** (empieza aquí):
```env
AUTO_TRADING_ENABLED=false     # Solo señales
ML_ENABLED=true
MAX_SYMBOLS_TO_TRADE=20
CHECK_INTERVAL=120
```

### Para **TRADING REAL** (después de testear):
```env
AUTO_TRADING_ENABLED=true      # Trading real
MAX_POSITION_SIZE=100
MAX_OPEN_TRADES=5
TAKE_PROFIT_PCT=2.0
STOP_LOSS_PCT=1.0
ML_ENABLED=true
ML_CONFIDENCE_THRESHOLD=0.65
```

---

## 🔍 VERIFICAR QUE FUNCIONA

### 1. Logs de Railway deben mostrar:
```
🚀 INICIANDO BOT DE TRADING PROFESIONAL
✅ Bot inicializado
📊 STATUS - Iteración #1
💰 Balance: $X
📈 Trades abiertos: 0/5
🔍 Analizando 50 símbolos...
```

### 2. Si tienes Telegram configurado:
```
🤖 BOT PROFESIONAL INICIADO
✅ AUTO-TRADING: ON
🤖 ML/IA: ACTIVADO
```

---

## 💡 TIPS PRO

1. **Empieza con AUTO_TRADING_ENABLED=false**
   - Observa las señales
   - Verifica que todo funciona
   - Activa trading después de 1-2 días

2. **Usa cantidades pequeñas al principio**
   - MAX_POSITION_SIZE=10 o 20
   - MAX_OPEN_TRADES=2
   - Escala gradualmente

3. **Monitorea los logs regularmente**
   - Primeros 2-3 días: cada hora
   - Después: 1-2 veces al día

4. **Descarga trading_bot.db periódicamente**
   - Contiene todo tu histórico
   - Backup de estadísticas

---

## 📱 MONITOREO

### Railway Dashboard:
- **Build Logs**: Ver si compila bien
- **Deploy Logs**: Ver ejecución en tiempo real
- **Metrics**: CPU, RAM usage

### Telegram:
- Notificaciones de trades
- Estados del bot
- Errores importantes

---

## 🆘 SOPORTE RÁPIDO

**¿No arranca?**
1. Verifica variables de entorno
2. Revisa logs completos
3. Borra runtime.txt
4. Redeploy

**¿No genera señales?**
1. Baja ML_CONFIDENCE_THRESHOLD a 0.5
2. Aumenta MAX_SYMBOLS_TO_TRADE a 50
3. Espera 2-3 iteraciones (5-10 minutos)

**¿Demasiadas señales?**
1. Sube ML_CONFIDENCE_THRESHOLD a 0.75
2. Reduce MAX_SYMBOLS_TO_TRADE a 20

---

**¡CONFIGURACIÓN LISTA! 🚀**
