# 🚀 GUÍA DE INICIO RÁPIDO

## Configuración en 5 minutos

### 1️⃣ Instalar Dependencias

```bash
pip install pandas numpy aiohttp python-dotenv
```

### 2️⃣ Configurar Credenciales

Crea archivo `.env` con tus claves:

```bash
# Copiar plantilla
cp .env.example .env

# Editar con tus datos
nano .env
```

**Mínimo requerido:**
```bash
BINGX_API_KEY=tu_key_aqui
BINGX_SECRET=tu_secret_aqui
TG_TOKEN=tu_telegram_token
TG_CHAT_ID=tu_chat_id
```

### 3️⃣ Verificar Sistema

```bash
chmod +x diagnose.py
python diagnose.py
```

Debe mostrar: `✅ ¡Todos los checks pasaron!`

### 4️⃣ Modo Prueba (Solo Señales)

```bash
# En .env, asegurar:
SIGNALS_ONLY=true

# Ejecutar
python bot_v2.py
```

**Recibirás señales por Telegram, pero NO se ejecutarán trades.**

### 5️⃣ Activar Trading Real (Cuando estés listo)

```bash
# En .env, cambiar:
SIGNALS_ONLY=false

# Ejecutar
python bot_v2.py
```

⚠️ **ADVERTENCIA: Esto ejecutará trades reales con dinero real.**

---

## 🎯 Configuraciones Recomendadas

### Para Principiantes

```bash
SYMBOL=BTC-USDT
TIMEFRAME=15m
LEVERAGE=5
RISK_PCT=0.5
STRATEGY=hybrid
SIGNALS_ONLY=true  # ¡IMPORTANTE!
```

### Para Trading Conservador

```bash
LEVERAGE=5
RISK_PCT=1.0
ATR_MULTIPLIER=2.0
STRATEGY=hybrid
MIN_SCORE_DIFF=50.0
```

### Para Trading Agresivo

```bash
LEVERAGE=10
RISK_PCT=2.0
ATR_MULTIPLIER=1.5
STRATEGY=sniper
MIN_SCORE_DIFF=30.0
```

---

## 🔧 Solución Rápida de Problemas

### Error: "BINGX_SECRET not found"

```bash
# Verificar que .env existe
ls -la .env

# Cargar variables manualmente
export $(cat .env | xargs)
python bot_v2.py
```

### No recibo notificaciones de Telegram

1. Verificar TG_TOKEN es correcto
2. Verificar TG_CHAT_ID es correcto
3. Iniciar chat con el bot primero
4. Usar `/start` en el bot

### El bot no ejecuta trades

Revisar:
- `SIGNALS_ONLY=false` (para trading real)
- Balance suficiente en BingX
- API keys tienen permisos de trading
- Leverage configurado en BingX

### Demasiadas comisiones

```bash
# Reducir frecuencia
POLL_SECONDS=300

# Aumentar distancia TPs
ATR_MULTIPLIER=2.0
```

---

## 📊 Monitoreo

### Ver Estado Actual

```bash
cat /tmp/bot_state.json
```

### Ver Métricas de Rendimiento

```bash
python -c "
from trade_analyzer import TradeAnalyzer
a = TradeAnalyzer('/tmp/trades_history.json', '/tmp/performance_metrics.json')
print(a.get_performance_report())
"
```

### Ver Logs en Tiempo Real

```bash
tail -f /tmp/bot.log
```

---

## 🛑 Detener el Bot

```bash
# Presionar Ctrl+C en la terminal
# O
pkill -f bot_v2.py
```

---

## 📱 Comandos Útiles

### Verificar si está corriendo

```bash
ps aux | grep bot_v2
```

### Ejecutar en background

```bash
nohup python bot_v2.py > /tmp/bot_output.log 2>&1 &
```

### Ver proceso

```bash
tail -f /tmp/bot_output.log
```

---

## ✅ Checklist Pre-Trading

Antes de activar trading real:

- [ ] Probado en SIGNALS_ONLY durante al menos 1 semana
- [ ] Win rate > 50% en señales
- [ ] Entiendes cómo funcionan los TPs
- [ ] Balance de prueba en BingX (empezar pequeño)
- [ ] Leverage bajo (5-10x máximo)
- [ ] Risk % conservador (0.5-1.0%)
- [ ] Monitoreo activo las primeras 24h

---

## 🆘 Soporte

Si tienes problemas:

1. Ejecutar `python diagnose.py`
2. Revisar `/tmp/bot.log`
3. Verificar configuración en `.env`
4. Comprobar balance en BingX
5. Revisar permisos de API

---

**¡Buena suerte! 🚀**
