# 🚀 Bot de Trading Profesional con ML/IA

Bot de trading avanzado para BingX con Machine Learning, análisis técnico y gestión de riesgo robusta.

## ✨ Características Principales

### 🤖 Machine Learning / IA
- ✅ Predicción de dirección del precio con Random Forest
- ✅ Entrenamiento continuo con datos reales
- ✅ Combinación de señales técnicas y ML
- ✅ Confidence threshold configurable

### 📊 Análisis Técnico Avanzado
- ✅ RSI (Relative Strength Index)
- ✅ MACD (Moving Average Convergence Divergence)
- ✅ Bollinger Bands
- ✅ Análisis de volumen
- ✅ Multiple timeframes
- ✅ Support/Resistance detection

### 🎯 Análisis de TODAS las Monedas
- ✅ Obtención dinámica de todos los pares disponibles en BingX
- ✅ Filtrado por volumen y liquidez
- ✅ Top símbolos por volumen 24h
- ✅ Hasta 50+ pares simultáneos

### 🛡️ Gestión de Riesgo Robusta
- ✅ Position sizing dinámico basado en volatilidad
- ✅ Trailing stop loss
- ✅ Max drawdown protection
- ✅ Daily loss limits
- ✅ Kelly Criterion
- ✅ Sharpe Ratio calculation

### 📈 Estadísticas y Rentabilidad
- ✅ Base de datos SQLite para histórico completo
- ✅ Win rate, profit factor, Sharpe ratio
- ✅ Performance por símbolo
- ✅ Equity curve
- ✅ Reportes diarios/semanales/mensuales

### ⚡ Auto-Trading Completo
- ✅ Apertura automática de trades
- ✅ Take Profit y Stop Loss automáticos
- ✅ Cierre automático al tocar TP/SL
- ✅ Gestión completa del ciclo de vida

## 📋 Requisitos

- Python 3.8+
- Cuenta en BingX con API keys
- Balance mínimo recomendado: $100 USDT

## 🚀 Instalación

### 1. Clonar o descargar el proyecto

```bash
git clone <tu-repo>
cd trading-bot
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus credenciales
```

### 4. Ejecutar el bot

```bash
python main.py
```

## ⚙️ Configuración

Edita el archivo `.env` con tus parámetros:

### Configuración Básica

```env
# API Keys de BingX
BINGX_API_KEY=your_api_key
BINGX_API_SECRET=your_secret

# Auto-trading
AUTO_TRADING_ENABLED=true  # false para solo señales
MAX_POSITION_SIZE=100      # Tamaño en USDT
LEVERAGE=2                  # Apalancamiento
```

### Gestión de Riesgo

```env
TAKE_PROFIT_PCT=2.0        # 2% TP
STOP_LOSS_PCT=1.0          # 1% SL
MAX_OPEN_TRADES=5          # Máx. trades simultáneos
MAX_DAILY_LOSS=500         # Máx. pérdida diaria
```

### Machine Learning

```env
ML_ENABLED=true                    # Activar ML
ML_CONFIDENCE_THRESHOLD=0.65       # Confianza mínima (0-1)
ML_RETRAIN_INTERVAL=3600           # Re-entrenamiento cada hora
```

## 📊 Estructura del Proyecto

```
trading-bot/
├── main.py                 # Bot principal
├── config.py               # Configuración centralizada
├── bingx_client.py        # Cliente BingX API
├── technical_analysis.py  # Indicadores técnicos
├── ml_predictor.py        # Machine Learning
├── risk_manager.py        # Gestión de riesgo
├── statistics.py          # Estadísticas y DB
├── requirements.txt       # Dependencias
├── .env                   # Variables de entorno (no commitear)
├── .env.example           # Template de configuración
└── README.md             # Este archivo
```

## 📈 Uso

### Modo Solo Señales (Sin Trading Real)

```env
AUTO_TRADING_ENABLED=false
```

El bot analizará el mercado y mostrará señales sin ejecutar trades reales.

### Modo Auto-Trading (Trading Real)

```env
AUTO_TRADING_ENABLED=true
```

⚠️ **IMPORTANTE**: El bot ejecutará trades reales. Empieza con cantidades pequeñas.

## 🔍 Indicadores y Señales

El bot combina múltiples fuentes de información:

1. **Análisis Técnico**: RSI, MACD, Bollinger Bands
2. **Machine Learning**: Predicción basada en patrones históricos
3. **Gestión de Riesgo**: Validaciones antes de cada trade

### Condiciones para Abrir Trade

```python
✓ Señal técnica + ML en acuerdo
✓ Confianza > threshold configurado
✓ No hay trade abierto en ese símbolo
✓ No se excede max trades simultáneos
✓ No se ha alcanzado daily loss limit
✓ Drawdown < max drawdown
```

## 📊 Estadísticas

El bot registra todas las operaciones en SQLite:

- ✅ Histórico completo de trades
- ✅ Señales generadas
- ✅ Performance por símbolo
- ✅ Balance diario
- ✅ Equity curve

### Consultar Estadísticas

```python
from statistics import StatisticsTracker

stats = StatisticsTracker()

# Performance últimos 30 días
summary = stats.get_performance_summary(days=30)
print(f"Win Rate: {summary['win_rate']}%")
print(f"Profit Factor: {summary['profit_factor']}")

# Por símbolo
by_symbol = stats.get_performance_by_symbol(days=30)
```

## 🛡️ Seguridad

- ✅ API keys en variables de entorno
- ✅ No commitear archivo `.env`
- ✅ Validación de todas las operaciones
- ✅ Límites de riesgo configurables

## ⚠️ Advertencias

- El trading de criptomonedas es de ALTO RIESGO
- Puedes perder TODO tu capital
- Este bot NO garantiza ganancias
- Úsalo bajo tu propio riesgo
- Empieza con cantidades pequeñas
- Testea en modo señales primero
- Monitorea constantemente el bot

## 🔧 Troubleshooting

### Error: "BINGX_API_KEY no configurada"

```bash
# Verifica que el archivo .env existe y tiene las keys
cat .env
```

### Error: "scikit-learn no disponible"

```bash
# Instalar scikit-learn
pip install scikit-learn
```

### Error: "Max daily loss alcanzado"

El bot se detuvo por protección. Revisa tu configuración de riesgo.

## 📞 Soporte

Para reportar bugs o sugerencias, abre un issue en GitHub.

## 📄 Licencia

MIT License - Úsalo libremente bajo tu propio riesgo.

## 🙏 Agradecimientos

- BingX API
- scikit-learn
- Comunidad de trading algorítmico

---

**⚠️ DISCLAIMER**: Este software se proporciona "tal cual" sin garantías de ningún tipo. El trading de criptomonedas implica riesgo de pérdida de capital. Los desarrolladores no son responsables de pérdidas incurridas al usar este software.
