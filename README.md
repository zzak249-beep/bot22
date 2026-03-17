# 🤖 Trading Bot - Liquidity Day + Linear Regression

Bot de trading automático que combina dos estrategias técnicas para ejecutar trades en **BingX** con notificaciones en **Telegram**.

## 📊 Estrategia

### Indicadores Utilizados
1. **Linear Regression Channel**: Detecta cambios de tendencia usando regresión lineal y desviación estándar
2. **Liquidity Day Series**: Identifica niveles clave de soporte/resistencia (PDH/PDL)

### Lógica de Señales

#### 🟢 COMPRA (LONG)
- Precio cruza **arriba del PDH** (Previous Day High)
- Linear Regression indica tendencia **alcista**
- Hay **cambio positivo de pendiente**

#### 🔴 VENTA (SHORT)
- Precio cruza **abajo del PDL** (Previous Day Low)
- Linear Regression indica tendencia **bajista**
- Hay **cambio negativo de pendiente**

#### 💰 TAKE PROFIT
- Automático cuando se alcanza el **+2.5%** (configurable)

#### ⛔ STOP LOSS
- Automático cuando se toca el **-1.5%** (configurable)

## 🚀 Instalación Rápida

### 1. Clonar o descargar repositorio
```bash
git clone https://github.com/tu-usuario/trading-bot.git
cd trading-bot
```

### 2. Crear archivo `.env`
```bash
cp .env.example .env
```

Edita `.env` con tus credenciales:
```env
BINGX_API_KEY=tu_api_key
BINGX_SECRET_KEY=tu_secret_key
TELEGRAM_BOT_TOKEN=tu_bot_token
TELEGRAM_CHAT_ID=tu_chat_id
SYMBOL=BTC/USDT
TIMEFRAME=15m
DRY_RUN=False
```

### 3. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 4. Ejecutar localmente
```bash
python main.py
```

## 🚢 Deploy en Railway

### Paso 1: Preparar GitHub
1. Sube todos los archivos a un repositorio de GitHub
2. Asegúrate que NO incluyas el archivo `.env`

### Paso 2: Conectar a Railway
1. Ve a https://railway.app
2. Login con GitHub
3. **New Project** → **Deploy from GitHub**
4. Selecciona tu repositorio `trading-bot`

### Paso 3: Agregar Variables de Entorno
En Railway Dashboard, ve a **Variables** y agrega cada una:

```
BINGX_API_KEY = tu_api_key
BINGX_SECRET_KEY = tu_secret_key
TELEGRAM_BOT_TOKEN = tu_bot_token
TELEGRAM_CHAT_ID = tu_chat_id
SYMBOL = BTC/USDT
TIMEFRAME = 15m
POSITION_SIZE = 0.01
TAKE_PROFIT_PERCENT = 2.5
STOP_LOSS_PERCENT = 1.5
LINREG_LENGTH = 50
LINREG_MULT = 2.0
CHECK_INTERVAL = 300
ENABLE_TRADING = True
DRY_RUN = False
```

### Paso 4: Deploy
Railway desplegará automáticamente. El bot se ejecutará 24/7.

## 🔐 Obtener Credenciales

### BingX API
1. Ve a https://bingx.com
2. Account → API Management
3. Create API Key
4. Habilita: **Trading**, **Position Management**
5. Copia **API Key** y **Secret Key**

### Telegram Bot
1. Abre @BotFather en Telegram
2. /start → /newbot
3. Escribe nombre del bot
4. Escribe username único
5. Copia el **TOKEN** recibido
6. Abre @userinfobot
7. Copia tu **CHAT_ID**

## 📊 Parámetros Recomendados por Timeframe

### 15 MINUTOS (Alta volatilidad)
```
POSITION_SIZE=0.005
TAKE_PROFIT_PERCENT=1.5
STOP_LOSS_PERCENT=1.0
LINREG_LENGTH=30
TIMEFRAME=15m
```

### 1 HORA (Balance)
```
POSITION_SIZE=0.01
TAKE_PROFIT_PERCENT=2.5
STOP_LOSS_PERCENT=1.5
LINREG_LENGTH=50
TIMEFRAME=1h
```

### 4 HORAS (Tendencia)
```
POSITION_SIZE=0.02
TAKE_PROFIT_PERCENT=4.0
STOP_LOSS_PERCENT=2.0
LINREG_LENGTH=100
TIMEFRAME=4h
```

## ⚙️ Configuración Avanzada

### DRY RUN (Modo Prueba)
Para probar sin dinero real:
```
DRY_RUN=True
```
El bot simula los trades pero no ejecuta órdenes reales.

### ENABLE_TRADING (Habilitar/Deshabilitar Trades)
```
ENABLE_TRADING=False  # No ejecuta órdenes
ENABLE_TRADING=True   # Ejecuta órdenes (valor por defecto)
```

### CHECK_INTERVAL (Tiempo entre análisis)
```
CHECK_INTERVAL=300    # Analiza cada 5 minutos (en segundos)
CHECK_INTERVAL=60     # Analiza cada 1 minuto
```

## 📈 Ejemplo de Ejecución

```
2024-01-15 10:30:45 - INFO - 🤖 Bot inicializado
2024-01-15 10:30:45 - INFO - Símbolo: BTC/USDT
2024-01-15 10:30:45 - INFO - TP: 2.5% | SL: 1.5%
2024-01-15 10:30:45 - INFO - 🤖 Bot iniciado - Esperando señales...

2024-01-15 10:35:20 - INFO - ✅ Obtenidas 200 velas de BTC/USDT 15m
2024-01-15 10:35:21 - INFO - 🟢 SEÑAL BUY: Precio 42500.50 > PDH 42300.00 + Tendencia UP
2024-01-15 10:35:22 - INFO - 📊 Ejecutando BUY a 42500.50
2024-01-15 10:35:23 - INFO - ✅ Orden BUY creada: 123456789

[Telegram] 🟢 ENTRADA LONG
Símbolo: BTC/USDT
Entrada: $42500.50
TP: $43563.64
SL: $41872.39
Cantidad: 0.01
Risk/Reward: 1:5.18

2024-01-15 10:45:00 - INFO - 💰 TP LONG alcanzado: 43600.00 >= 43563.64
2024-01-15 10:45:01 - INFO - 📊 Cerrando LONG
[Telegram] 💰 TAKE PROFIT LONG
Entrada: $42500.50
Salida: $43600.00
Ganancia: +2.58%
```

## 🛡️ Seguridad

### ✅ Mejores Prácticas
- ✓ Nunca commitees el archivo `.env`
- ✓ USA API keys restringidas en BingX (solo trading)
- ✓ Comienza con `POSITION_SIZE` pequeño
- ✓ Prueba primero en `DRY_RUN=True`
- ✓ Monitorea el bot regularmente

### ❌ NUNCA hagas esto
- ✗ No compartas tu TELEGRAM_BOT_TOKEN
- ✗ No publicues tus API keys en GitHub
- ✗ No uses API keys de mainnet en desarrollo
- ✗ No ejecutes sin revisar el código

## 📞 Troubleshooting

### Error: "API keys not found"
- Verifica que `.env` existe
- Comprueba que las variables están bien escritas
- En Railway, verifica que están en **Variables**

### Error: "Telegram not configured"
- Bot seguirá funcionando sin Telegram
- Agrega TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
- Verifica el token en @BotFather

### Error: "Datos insuficientes"
- Aumenta LINREG_LENGTH (máximo 200)
- Cambia a timeframe mayor (1h en lugar de 15m)
- Espera a que haya más datos históricos

### El bot no hace trades
- Verifica que `ENABLE_TRADING=True`
- Comprueba saldo en BingX
- Revisa que `POSITION_SIZE` no sea muy pequeño
- Usa `DRY_RUN=True` para ver si genera señales

## 📊 Monitoreo

### Ver logs en Railway
```
railway logs
```

### Ver logs locales
```
python main.py > bot.log 2>&1
tail -f bot.log
```

## 🤝 Contribuciones

Las contribuciones son bienvenidas. Por favor:
1. Fork el proyecto
2. Crea una rama para tu feature
3. Commit tus cambios
4. Push a la rama
5. Abre un Pull Request

## ⚠️ Disclaimer

Este bot se proporciona "tal cual". El trading de criptomonedas conlleva riesgo. 

**IMPORTANTE:**
- Comienza con pequeñas cantidades
- Prueba en demo/testnet primero
- No invierta dinero que no pueda perder
- Revisa las leyes fiscales de tu país
- El código puede tener bugs

**Los autores no son responsables de pérdidas financieras.**

## 📄 Licencia

MIT License - Ver LICENSE.md

## 🌟 Soporte

Si te gustó el proyecto, déjame una ⭐ en GitHub!

Para preguntas:
- Abre un Issue
- Escribe a tu email

---

**Hecho con ❤️ para traders**
