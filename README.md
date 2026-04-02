# 🤖 Bot de Trading Multi-Moneda - Zero Lag Scalping

Bot de trading automatizado para BingX con soporte para múltiples criptomonedas simultáneas.

## ✨ Características v3

### ✅ Correcciones Implementadas

1. **FIX-6**: Corrección del error API 109400
   - Eliminados parámetros conflictivos en órdenes
   - Método de cierre optimizado
   - Mejor validación de parámetros

2. **FIX-7**: Soporte Multi-Moneda
   - Analiza hasta 20+ pares simultáneamente
   - Gestión independiente por símbolo
   - Control de posiciones máximas

3. **FIX-8**: Validación de Cantidades
   - Consulta límites mínimos por símbolo
   - Redondeo correcto según `qtyStep`
   - Previene órdenes rechazadas

4. **FIX-9**: Mensajería Mejorada
   - Errores detallados con contexto completo
   - Logs informativos por símbolo
   - Notificaciones Telegram optimizadas

### 🎯 Estrategia

- **Zero Lag EMA**: Media móvil sin retardo para señales tempranas
- **Bandas Dinámicas**: Detección de sobrecompra/sobreventa
- **Probabilidad de Reversión**: Modelo multi-factor para timing
- **Oscilador Estocástico**: Confirmación de señales

## 📋 Requisitos

### Software

```bash
Python 3.8+
pip install pandas numpy requests python-dotenv
```

### Cuenta BingX

1. Crear cuenta en [BingX](https://bingx.com)
2. Completar KYC si es necesario
3. Depositar fondos (mínimo 30-50 USDT recomendado)
4. Crear API Key:
   - Ir a API Management
   - Crear nueva API Key
   - **IMPORTANTE**: Habilitar permisos de trading
   - Guardar API Key y Secret Key

### Telegram (Opcional)

1. Crear bot con [@BotFather](https://t.me/botfather)
2. Obtener token del bot
3. Obtener tu Chat ID:
   - Enviar mensaje a [@userinfobot](https://t.me/userinfobot)
   - Copiar tu Chat ID

## 🚀 Instalación

### 1. Clonar archivos

```bash
# Descargar todos los archivos:
# - bot_fixed.py
# - bingx_client_fixed.py
# - strategy.py
# - .env.example
```

### 2. Configurar variables

```bash
# Copiar ejemplo y editar
cp .env.example .env
nano .env
```

**Configuración mínima:**

```env
BINGX_API_KEY=tu_api_key
BINGX_SECRET_KEY=tu_secret_key

# Símbolos a tradear (separados por coma)
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT

# Configuración básica
LEVERAGE=5
MAX_POSITIONS=2
DEMO_MODE=false
```

### 3. Instalar dependencias

```bash
pip install pandas numpy requests python-dotenv
```

### 4. Probar conexión

```bash
python test_connection.py
```

### 5. Ejecutar bot

```bash
# Modo demo (testnet)
DEMO_MODE=true python bot_fixed.py

# Modo real (¡DINERO REAL!)
DEMO_MODE=false python bot_fixed.py
```

## ⚙️ Configuración Avanzada

### Selección de Símbolos

```env
# Estrategia conservadora (majors)
SYMBOLS=BTC-USDT,ETH-USDT,BNB-USDT

# Estrategia balanceada
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT,AVAX-USDT,MATIC-USDT

# Estrategia agresiva (incluye altcoins)
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT,AVAX-USDT,MATIC-USDT,ADA-USDT,DOT-USDT,LINK-USDT,UNI-USDT
```

### Gestión de Riesgo

| Perfil | LEVERAGE | RISK_PCT | MAX_POSITIONS | Balance Mín. |
|--------|----------|----------|---------------|--------------|
| Conservador | 3-5x | 3% | 1-2 | 30 USDT |
| Moderado | 5-10x | 5% | 2-4 | 50 USDT |
| Agresivo | 10-20x | 5-10% | 3-6 | 100 USDT |

**Fórmula de exposición:**
```
Exposición por trade = RISK_PCT × LEVERAGE
Exposición total = Exposición por trade × MAX_POSITIONS

Ejemplo: 5% × 5x × 3 posiciones = 75% del capital expuesto
```

### Optimización de Estrategia

```env
# Scalping agresivo (más señales)
TIMEFRAME=5m
ZLEMA_LENGTH=50
ENTRY_MAX_PROB=0.70
CHECK_INTERVAL=30

# Swing trading conservador (menos señales)
TIMEFRAME=1h
ZLEMA_LENGTH=100
ENTRY_MAX_PROB=0.50
CHECK_INTERVAL=300
```

## 📊 Monitoreo

### Logs en Consola

```
════════════════════════════════════════════════════════════════
CICLO #42 | Balance: $156.23 USDT | Posiciones: 2/3
════════════════════════════════════════════════════════════════
BTC-USDT: $67890.50 | ALCISTA | Prob: 45.2% | Pos: long
ETH-USDT: $3456.78 | BAJISTA | Prob: 67.8% | Pos: short
SOL-USDT: $123.45 | ALCISTA | Prob: 52.1% | Pos: —
```

### Notificaciones Telegram

**Apertura:**
```
🟢 ABIERTO LONG — BTC-USDT
TF: 15m | Leverage: 5x
Precio: $67890.5000
Cantidad: 0.015 | positionSide: LONG
Tendencia: ALCISTA | Prob: 45.2%
Balance: $156.23 USDT
Posiciones abiertas: 2/3
```

**Cierre:**
```
✅ CERRADO LONG — BTC-USDT
Razón: Prob 84.5% >= 84.0%
Entrada: $67890.5000 | Salida: $68123.2000
PnL: +0.34% ($3.4872 USDT)
PnL símbolo: $12.8770 | PnL total: $45.6234
```

## 🔧 Solución de Problemas

### Error 109400: Invalid Parameters

**Causa**: Parámetros incorrectos en la orden

**Solución** (ya implementada en v3):
- ✅ Removidos parámetros `positionSide` conflictivos
- ✅ Validación de cantidades mínimas
- ✅ Uso de `reduceOnly` en cierres

### Sin Señales de Entrada

**Causa**: `ENTRY_MAX_PROB` muy bajo

**Solución**:
```env
# Cambiar de 0.30 a 0.65
ENTRY_MAX_PROB=0.65
```

### Órdenes Rechazadas

**Causas comunes**:
1. Cantidad menor al mínimo
2. Balance insuficiente
3. Apalancamiento no configurado

**Solución**:
- Verificar `MIN_BALANCE`
- Aumentar `RISK_PCT` si las cantidades son muy pequeñas
- Revisar logs: `Error calculando cantidad`

### Bot Se Detiene

**Verificar**:
```bash
# Logs de Railway/servidor
tail -f logs/bot.log

# Conexión a BingX
python test_connection.py

# Variables de entorno
env | grep BINGX
```

## 📈 Mejores Prácticas

### Antes de Empezar

1. ✅ Probar en DEMO_MODE primero
2. ✅ Empezar con 1-2 símbolos
3. ✅ Usar apalancamiento bajo (3-5x)
4. ✅ Configurar Telegram para monitoreo
5. ✅ Revisar logs durante las primeras horas

### Durante la Operación

- Monitorear reportes cada hora
- Ajustar `MAX_POSITIONS` según volatilidad
- No cambiar configuración durante posiciones abiertas
- Mantener buffer de balance (no usar 100%)

### Optimización

```bash
# Generar reporte cada 30 ciclos (30 minutos si CHECK_INTERVAL=60)
REPORT_EVERY=30

# Ver estadísticas
grep "PnL total" logs/*.log | tail -20
```

## 🎓 Estrategias Recomendadas

### 1. Scalping en Majors
```env
SYMBOLS=BTC-USDT,ETH-USDT
TIMEFRAME=15m
LEVERAGE=5
MAX_POSITIONS=2
RISK_PCT=0.05
```

### 2. Swing Multi-Altcoin
```env
SYMBOLS=SOL-USDT,AVAX-USDT,MATIC-USDT,ADA-USDT,DOT-USDT
TIMEFRAME=1h
LEVERAGE=3
MAX_POSITIONS=3
RISK_PCT=0.03
```

### 3. Day Trading Mixto
```env
SYMBOLS=BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT
TIMEFRAME=30m
LEVERAGE=7
MAX_POSITIONS=4
RISK_PCT=0.05
```

## ⚠️ Advertencias

- **Trading con apalancamiento es de ALTO RIESGO**
- Puedes perder TODO tu capital
- Nunca inviertas más de lo que puedes perder
- Monitorea el bot regularmente
- Ten un plan de salida

## 📝 Registro de Cambios

### v3.0 (Actual)
- ✅ Corrección error API 109400
- ✅ Soporte multi-moneda
- ✅ Validación de cantidades
- ✅ Mejores mensajes de error

### v2.0
- Corrección hedge mode
- Notificaciones Telegram
- Reportes periódicos

### v1.0
- Versión inicial

## 🤝 Soporte

Para problemas:
1. Revisar logs detallados
2. Verificar configuración en `.env`
3. Probar `test_connection.py`
4. Revisar documentación de BingX API

## 📄 Licencia

Uso bajo tu propio riesgo. El autor no se responsabiliza por pérdidas.

---

**¡Happy Trading! 🚀**
