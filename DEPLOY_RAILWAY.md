# 🚂 GUÍA DE DESPLIEGUE EN RAILWAY

## 📦 Archivos del Bot Mejorado

Has recibido el bot mejorado con los siguientes archivos:

```
✅ main.py                 - Bot principal con ML/IA
✅ config.py               - Configuración centralizada
✅ bingx_client.py        - Cliente BingX optimizado
✅ technical_analysis.py  - Análisis técnico (RSI, MACD, BB)
✅ ml_predictor.py        - Machine Learning predictor
✅ risk_manager.py        - Gestión de riesgo avanzada
✅ statistics.py          - Estadísticas y base de datos
✅ requirements.txt       - Dependencias
✅ .env.example           - Template de configuración
✅ README.md             - Documentación completa
```

## 🚀 DESPLIEGUE EN RAILWAY

### Opción 1: Actualizar Repositorio Existente

Si ya tienes el bot en GitHub:

```bash
# 1. Reemplazar archivos
git rm *.py
git add main.py config.py bingx_client.py technical_analysis.py ml_predictor.py risk_manager.py statistics.py
git add requirements.txt README.md .env.example

# 2. Commit
git commit -m "🚀 Bot mejorado con ML/IA + análisis todas las monedas"

# 3. Push
git push origin main
```

Railway detectará los cambios y redesplegará automáticamente.

### Opción 2: Nuevo Proyecto

```bash
# 1. Crear nuevo directorio
mkdir trading-bot-pro
cd trading-bot-pro

# 2. Copiar todos los archivos descargados

# 3. Inicializar git
git init
git add .
git commit -m "Initial commit - Bot pro con ML"

# 4. Crear repo en GitHub y push
git remote add origin <tu-repo-url>
git push -u origin main

# 5. Conectar en Railway
# - New Project → Deploy from GitHub
# - Seleccionar tu repo
```

## ⚙️ CONFIGURAR VARIABLES DE ENTORNO EN RAILWAY

En Railway → Tu proyecto → Variables:

### **Esenciales** (mínimo requerido):

```
BINGX_API_KEY=your_api_key_here
BINGX_API_SECRET=your_secret_here
AUTO_TRADING_ENABLED=true
```

### **Recomendadas**:

```
# Trading
MAX_POSITION_SIZE=100
LEVERAGE=2
MAX_OPEN_TRADES=5

# Risk
TAKE_PROFIT_PCT=2.0
STOP_LOSS_PCT=1.0
MAX_DAILY_LOSS=500
MAX_DRAWDOWN_PCT=10

# ML
ML_ENABLED=true
ML_CONFIDENCE_THRESHOLD=0.65

# Intervals
CHECK_INTERVAL=60
MARKET_SCAN_INTERVAL=300

# Telegram (opcional)
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

### **Avanzadas** (opcional):

```
# Technical Analysis
RSI_PERIOD=14
RSI_OVERBOUGHT=70
RSI_OVERSOLD=30
MACD_FAST=12
MACD_SLOW=26
MACD_SIGNAL=9
BB_PERIOD=20
BB_STD_DEV=2.0

# Trailing Stop
TRAILING_STOP_ENABLED=true
TRAILING_STOP_ACTIVATION=1.0
TRAILING_STOP_DISTANCE=0.5

# Market
MIN_VOLUME_24H=1000000
MAX_SYMBOLS_TO_TRADE=50

# Logging
LOG_LEVEL=INFO
```

## 📊 DIFERENCIAS CON LA VERSIÓN ANTERIOR

### ✅ MEJORAS IMPLEMENTADAS:

#### 1. **Análisis de TODAS las Monedas** 🎯
```python
# ANTES: Lista estática de 10 pares
symbols = ['BTC-USDT', 'ETH-USDT', ...]

# AHORA: Dinámico hasta 50+ pares top por volumen
client.get_top_symbols_by_volume(limit=50)
```

#### 2. **Machine Learning / IA** 🤖
```python
# Random Forest Classifier
# Entrenamiento continuo
# Combina señales técnicas + ML
# Confidence threshold
```

#### 3. **Análisis Técnico Avanzado** 📊
```python
# RSI (Relative Strength Index)
# MACD (Moving Average Convergence Divergence)
# Bollinger Bands
# Volume analysis
# Support/Resistance
```

#### 4. **Gestión de Riesgo Robusta** 🛡️
```python
# Position sizing dinámico (basado en volatilidad)
# Trailing stop loss
# Max drawdown protection
# Daily loss limits
# Kelly Criterion
# Sharpe Ratio
```

#### 5. **Estadísticas Completas** 📈
```python
# Base de datos SQLite
# Win rate, profit factor
# Performance por símbolo
# Equity curve
# Histórico completo
```

## 🔧 TESTING ANTES DE PRODUCCIÓN

### 1. Modo Solo Señales (Sin Trading Real)

```env
AUTO_TRADING_ENABLED=false
```

Observa los logs:
- ¿Genera señales lógicas?
- ¿El ML funciona correctamente?
- ¿Los indicadores técnicos son precisos?

### 2. Testeo con Cantidades Pequeñas

```env
AUTO_TRADING_ENABLED=true
MAX_POSITION_SIZE=10  # Empezar con $10
MAX_OPEN_TRADES=2
```

Monitorea:
- Ejecución de trades
- TP/SL funcionando
- Cierre automático
- Notificaciones Telegram

### 3. Producción

Cuando estés confiado:

```env
MAX_POSITION_SIZE=100  # O tu tamaño deseado
MAX_OPEN_TRADES=5
```

## 📱 MONITOREO

### Logs en Railway

```
Settings → Deployments → Ver logs en tiempo real
```

### Telegram

Recibirás notificaciones de:
- ✅ Trades abiertos
- ✅ Trades cerrados
- 📊 Razones (TP/SL)
- 💰 PnL

### Base de Datos

El archivo `trading_bot.db` contiene:
- Histórico completo
- Estadísticas
- Performance

## ⚠️ IMPORTANTE

1. **Empieza Pequeño**: $10-50 position size
2. **Monitorea Constantemente**: Primeros días
3. **Ajusta Parámetros**: Según resultados
4. **Backup**: Descarga trading_bot.db periódicamente
5. **Stop Loss Global**: MAX_DAILY_LOSS protege tu capital

## 🆘 TROUBLESHOOTING

### "No genera señales"
- Revisa `ML_CONFIDENCE_THRESHOLD` (bájalo a 0.5)
- Verifica `MIN_VOLUME_24H` (bájalo si es muy alto)
- Check logs: ¿Está analizando símbolos?

### "Demasiadas señales"
- Sube `ML_CONFIDENCE_THRESHOLD` a 0.75
- Reduce `MAX_SYMBOLS_TO_TRADE`
- Ajusta `RSI_OVERBOUGHT/OVERSOLD`

### "ML no funciona"
```bash
# Instalar scikit-learn
pip install scikit-learn==1.3.0
```

### "Errores de API"
- Verifica API keys
- Check balance en BingX
- Límites de rate limit

## 📞 SOPORTE

Si necesitas ayuda:
1. Revisa README.md
2. Check logs en Railway
3. Verifica variables de entorno
4. Testea localmente primero

## 🎯 PRÓXIMOS PASOS

1. ✅ Desplegar en Railway
2. ✅ Configurar variables
3. ✅ Testear modo señales (1 día)
4. ✅ Testear con $10 (2-3 días)
5. ✅ Ajustar parámetros
6. ✅ Escalar gradualmente
7. ✅ Monitorear y optimizar

---

**¡ÉXITO CON TU BOT! 🚀**
