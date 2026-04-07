# 🚀 Advanced Trading Bot V2 - BingX Edition

Bot de trading automatizado con **estrategias duales**, gestión inteligente de comisiones y sistema de aprendizaje.

## ✨ Características Principales

### 🎯 Estrategias Duales
- **Sniper Strategy**: Cruces de EMA 9/21 con scoring multi-indicador
- **VWAP Volatility Bands**: Sistema T3-smoothed con bandas de volatilidad ATR
- **Modo Híbrido**: Confluencia de ambas estrategias para mayor precisión

### 💰 Optimización de Comisiones
- ✅ Cálculo automático de breakeven incluyendo fees
- ✅ Ajuste dinámico de TPs para garantizar profit después de comisiones
- ✅ Tracking detallado de costos por trade

### 📊 Sistema de Aprendizaje
- ✅ Análisis automático de trades pasados
- ✅ Detección de patrones de pérdida
- ✅ Recomendaciones de optimización
- ✅ Métricas de rendimiento por estrategia

### 🛡️ Gestión de Riesgo
- ✅ Position sizing basado en % de balance
- ✅ 5 niveles de Take Profit (TP1-TP5)
- ✅ Stop Loss dinámico basado en ATR
- ✅ Máximo de posiciones simultáneas

---

## 📋 Requisitos

### Sistema
- Python 3.9 o superior
- Cuenta en BingX con API habilitada
- Bot de Telegram (opcional, pero recomendado)

### API Keys Requeridas
1. **BingX API**: https://bingx.com/en-us/account/api/
2. **Telegram Bot**: @BotFather en Telegram

---

## 🔧 Instalación

### 1. Clonar o descargar archivos

```bash
# Estructura de archivos necesaria:
bot_v2.py
strategy_vwap.py
strategy_sniper.py
trade_analyzer.py
bingx_client.py        # (tu implementación existente)
telegram_bot.py        # (tu implementación existente)
risk_manager.py        # (tu implementación existente)
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

```bash
# Copiar archivo de ejemplo
cp .env.example .env

# Editar con tus credenciales
nano .env  # o tu editor favorito
```

**Configuración mínima requerida:**

```bash
# API Keys
BINGX_API_KEY=tu_api_key_aqui
BINGX_SECRET=tu_secret_key_aqui
TG_TOKEN=tu_telegram_bot_token
TG_CHAT_ID=tu_chat_id

# Trading
SYMBOL=BTC-USDT
TIMEFRAME=15m
LEVERAGE=10
RISK_PCT=1.0

# IMPORTANTE: Empieza en modo prueba
SIGNALS_ONLY=true
```

---

## 🚀 Uso

### Modo Señales (Recomendado para empezar)

```bash
# Solo envía señales por Telegram, NO ejecuta trades
export SIGNALS_ONLY=true
python bot_v2.py
```

### Modo Trading en Vivo

```bash
# ⚠️ CUIDADO: Ejecuta trades reales con dinero real
export SIGNALS_ONLY=false
python bot_v2.py
```

---

## ⚙️ Configuración de Estrategias

### Estrategia Sniper (EMA)
Basada en cruces de EMA 9/21 con sistema de scoring:

```bash
STRATEGY=sniper
MIN_SCORE_DIFF=40.0  # Diferencia mínima para señal STRONG
```

**Componentes del Score:**
- Posición vs VWAP
- RSI > 50 (bull) o < 50 (bear)
- MACD vs Signal
- EMA 9 vs EMA 21
- ADX > 25 con dirección
- Volumen > promedio
- RSI 5m confirmación

### Estrategia VWAP Volatility Bands
Sistema T3-smoothed con bandas de volatilidad:

```bash
STRATEGY=vwap
```

**Características:**
- VWAP anclado por sesión
- Smoothing T3 (6-stage EMA cascade)
- 4 bandas de volatilidad (0.5x, 1.0x, 1.5x, 2.2x ATR)
- Señales en cruces de pendiente T3

### Modo Híbrido (Recomendado)
Requiere confluencia de ambas estrategias:

```bash
STRATEGY=hybrid
```

**Señal BUY:** Sniper BUY + VWAP BUY  
**Señal SELL:** Sniper SELL + VWAP SELL

---

## 📊 Análisis de Rendimiento

### Métricas en Tiempo Real

El bot genera automáticamente:

```bash
/tmp/bot_state.json           # Estado actual del trade
/tmp/trades_history.json      # Historial de trades
/tmp/performance_metrics.json # Métricas calculadas
```

### Ver Métricas

```python
from trade_analyzer import TradeAnalyzer

analyzer = TradeAnalyzer(
    "/tmp/trades_history.json",
    "/tmp/performance_metrics.json"
)

# Reporte completo
print(analyzer.get_performance_report())

# Análisis de errores
errors = analyzer.analyze_errors()
print(errors)
```

### Métricas Incluidas

- **Win Rate**: % de trades ganadores
- **Total PnL**: Ganancia/pérdida total
- **Comisiones**: Fees pagados totales
- **Profit Factor**: Ratio ganancia/pérdida
- **Max Drawdown**: Máxima pérdida consecutiva
- **Performance por Estrategia**: Comparativa VWAP vs Sniper
- **Distribución de TPs**: ¿Qué TPs se alcanzan más?

---

## 🔍 Solución de Problemas

### Error: `KeyError: 'BINGX_SECRET'`

**Causa:** Variables de entorno no configuradas  
**Solución:**

```bash
# Verifica que el archivo .env existe
cat .env

# Carga las variables manualmente
export $(cat .env | xargs)

# O usa python-dotenv
pip install python-dotenv
```

### El bot no ejecuta trades

**Posibles causas:**

1. **SIGNALS_ONLY=true**
   ```bash
   # Cambiar a false para trading real
   export SIGNALS_ONLY=false
   ```

2. **Balance insuficiente**
   - Verifica tu balance en BingX
   - El bot necesita margen disponible

3. **Posición máxima alcanzada**
   - El risk manager bloquea nuevas posiciones
   - Cierra posiciones existentes primero

### Señales pero sin confluencia (Hybrid)

**Causa:** Las estrategias no están alineadas  
**Solución:**

```bash
# Cambiar a una sola estrategia
STRATEGY=sniper  # o STRATEGY=vwap
```

### Comisiones muy altas

**Optimizaciones:**

1. **Reducir frecuencia de trades**
   ```bash
   POLL_SECONDS=300  # 5 minutos en vez de 60
   ```

2. **Aumentar distancia de TPs**
   ```bash
   ATR_MULTIPLIER=2.0  # En vez de 1.5
   ```

3. **Usar órdenes limit** (modificación en código)

---

## 🎯 Mejores Prácticas

### 1. Empezar con Precaución

```bash
# Primero en modo señales
SIGNALS_ONLY=true
LEVERAGE=5
RISK_PCT=0.5
```

### 2. Monitorear Regularmente

```bash
# Heartbeat cada 4 horas
HEARTBEAT_HOURS=4

# Revisar métricas diariamente
python -c "from trade_analyzer import TradeAnalyzer; \
  a = TradeAnalyzer('/tmp/trades_history.json', '/tmp/performance_metrics.json'); \
  print(a.get_performance_report())"
```

### 3. Ajustar Según Resultados

- **Win Rate < 40%**: Cambiar estrategia o parámetros
- **Comisiones > 30% de PnL**: Reducir frecuencia
- **TP1 raramente alcanzado**: Reducir ATR_MULTIPLIER

### 4. Diversificar

```bash
# No todo el capital en un símbolo
RISK_PCT=0.5  # 0.5% por trade
LEVERAGE=5    # Apalancamiento moderado
```

---

## 🚨 Advertencias Importantes

### ⚠️ Riesgo de Pérdida

- Trading con apalancamiento conlleva **alto riesgo**
- Solo opera con capital que puedas **permitirte perder**
- Las estrategias pasadas **no garantizan resultados futuros**

### 🔒 Seguridad

- **NUNCA** compartas tus API keys
- Activa **2FA** en BingX
- Usa **IP Whitelisting** si es posible
- Limita **permisos de API** (solo trading, no withdrawals)

### 📉 Backtest Primero

- Prueba en **paper trading** extensivamente
- Analiza **al menos 100 trades** antes de ir en vivo
- Verifica **win rate > 50%** y **profit factor > 1.5**

---

## 📚 Recursos Adicionales

### Documentación BingX
- API Docs: https://bingx-api.github.io/docs/
- Fees: https://bingx.com/en-us/support/articles/360016768834

### Aprender Trading
- ATR: https://www.investopedia.com/terms/a/atr.asp
- VWAP: https://www.investopedia.com/terms/v/vwap.asp
- Risk Management: https://www.investopedia.com/articles/trading/09/risk-management.asp

---

## 🛠️ Soporte y Desarrollo

### Reportar Bugs

Si encuentras un error:

1. **Revisa los logs**: `tail -f /tmp/bot.log`
2. **Verifica configuración**: `cat .env`
3. **Incluye detalles**: Versión Python, mensaje de error, configuración

### Contribuir

Mejoras bienvenidas:

- Nuevas estrategias
- Optimizaciones de comisiones
- Mejores métricas de análisis
- Tests automatizados

---

## 📜 Licencia

Este software se proporciona "tal cual", sin garantías de ningún tipo.
El uso es bajo tu propia responsabilidad y riesgo.

---

## 🏆 Roadmap Futuro

- [ ] Backtesting engine completo
- [ ] Optimización automática de parámetros
- [ ] Soporte multi-símbolo
- [ ] Dashboard web en tiempo real
- [ ] Machine learning para predicción
- [ ] Grid trading automático

---

**¡Buena suerte en tu trading! 🚀📈**

*Recuerda: La clave del éxito es la disciplina, el análisis constante y la gestión de riesgo.*
