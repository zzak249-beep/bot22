# 🏆 INSTITUTIONAL BOT v3.1 — QUICK START

## 🔴 PROBLEMAS CORREGIDOS

Tu bot tenía **3 bugs críticos** que causaban pérdidas:

1. ❌ **KeyError 'highest'**: Crash al monitorear posiciones → ✅ CORREGIDO
2. ❌ **API error 109400**: Falta positionSide → ✅ CORREGIDO  
3. ❌ **PnL -$2234**: Configuración demasiado agresiva → ✅ CORREGIDO

**Resultado**: -$2234 PnL con 45% WR en 31 trades 💸

## ✅ SOLUCIÓN v3.1

**Configuración conservadora**:
- Leverage: 3× → **2×** (-33%)
- Position: $15 → **$10** (-33%)
- Max positions: 4 → **2** (-50%)
- Circuit breaker: 6% → **3%** (-50%)
- Min score: 72 → **75** (+4%)

**+ 8 mejoras adicionales** (ver CHANGELOG)

---

## 🚀 INSTALACIÓN

### 1. Configurar Variables de Entorno

```bash
# API BingX (REQUERIDO)
export BINGX_API_KEY="tu_api_key_aqui"
export BINGX_API_SECRET="tu_secret_aqui"

# Telegram (OPCIONAL pero recomendado)
export TELEGRAM_BOT_TOKEN="tu_bot_token"
export TELEGRAM_CHAT_ID="tu_chat_id"

# ⚠️ IMPORTANTE: Empieza en PAPER MODE
export AUTO_TRADING_ENABLED=false

# Capital (usa defaults si no especificas)
export POSITION_SIZE_USD=10
export LEVERAGE=2
export MAX_POSITIONS=2
```

### 2. Instalar Dependencias

```bash
pip install requests asyncio
```

### 3. Ejecutar Tests (RECOMENDADO)

```bash
python test_bot_v3.py
```

Deberías ver:
```
✅ PASS | test_position_initialization
✅ PASS | test_position_side_parameter
...
TOTAL: 7/7 tests passed (100%)
```

### 4. Ejecutar Bot

```bash
# PAPER MODE (simulación)
python institutional_bot_v3_fixed.py

# O si ya hiciste testing y estás listo para REAL MONEY:
export AUTO_TRADING_ENABLED=true
python institutional_bot_v3_fixed.py
```

---

## 📊 MONITOREO

### Ver logs en tiempo real
```bash
tail -f /tmp/bot.log
```

### Buscar errores
```bash
grep ERROR /tmp/bot.log
```

### Ver trades cerrados
```bash
grep "CLOSED" /tmp/bot.log
```

### Stats del día
```bash
grep "PnL:" /tmp/bot.log | tail -20
```

---

## ⚠️ ANTES DE ACTIVAR REAL MONEY

1. ✅ Ejecutar `test_bot_v3.py` (todos los tests deben pasar)
2. ✅ Correr en PAPER MODE mínimo **1 semana**
3. ✅ Revisar logs cada día para verificar que no hay errores
4. ✅ Verificar que las API keys tienen permisos de futures
5. ✅ Empezar con capital MUY PEQUEÑO ($50-100)
6. ✅ Leer COMPLETO el CHANGELOG_v3.1.md

---

## 🎯 EXPECTATIVAS REALISTAS

| Métrica | Objetivo v3.1 | Tu resultado v3.0 |
|---------|---------------|-------------------|
| Win Rate | 55-65% | 45.2% ❌ |
| R:R | 2.0-3.0× | ~1.5× ❌ |
| Trades/día | 2-5 | ~10+ ❌ |
| Drawdown | 10-15% | >95% ❌ |
| Crecimiento mensual | 5-15% | -95% ❌ |

**NO esperes**:
- ❌ Recuperar los $2234 perdidos en 1 semana
- ❌ Win rate 80%+
- ❌ 10× capital en un mes
- ❌ Cero pérdidas

**SÍ espera**:
- ✅ Algunos días SIN trades (filtros estrictos)
- ✅ Rachas perdedoras ocasionales (3-4 trades)
- ✅ Circuit breakers activados (protección)
- ✅ Crecimiento gradual y sostenible

---

## 🆘 TROUBLESHOOTING

### Bot no encuentra símbolos para operar
→ **Normal con filtros estrictos**. Puede pasar horas sin señales.
→ Si quieres más trades, baja `MIN_SCORE=75` a `73`

### "KeyError: 'highest'"
→ **Corregido en v3.1**. Si ves esto, estás usando v3.0

### "API error 109400: positionSide required"
→ **Corregido en v3.1**. Si ves esto, estás usando v3.0

### Circuit breaker se activa mucho
→ **Buena señal**, te está protegiendo
→ Si pasa todos los días, el mercado está muy volátil

### PnL sigue siendo negativo
→ **Dale tiempo**: mínimo 50 trades para validar estrategia
→ Si después de 50 trades WR < 50%, ajusta filtros

---

## 📁 ARCHIVOS INCLUIDOS

1. **institutional_bot_v3_fixed.py** - Bot corregido ✅
2. **CHANGELOG_v3.1.md** - Cambios detallados
3. **test_bot_v3.py** - Suite de tests
4. **README.md** - Este archivo

---

## 🔄 COMPARACIÓN v3.0 vs v3.1

### v3.0 (TU BOT ORIGINAL)
```
Balance: $50.98
PnL: -$2234.26 ❌
WR: 45.2% (14W/17L) ❌
Leverage: 3× 
Position: $15
Max positions: 4
Circuit: 6%
```

### v3.1 (CORREGIDO)
```
Balance: $??? (depende de ti)
PnL: ??? (esperamos positivo) ✅
WR: 55-65% (objetivo) ✅
Leverage: 2× ⬇️
Position: $10 ⬇️
Max positions: 2 ⬇️
Circuit: 3% ⬇️
```

---

## 💡 TIPS PRO

1. **Empieza pequeño**: $10/trade es suficiente para aprender
2. **Sé paciente**: No fuerces trades, los filtros saben lo que hacen
3. **Confía en el circuit breaker**: Si se activa, es por tu bien
4. **Revisa los logs**: Entiende por qué cada trade ganó o perdió
5. **No cambies parámetros después de 2-3 trades malos**: Dale mínimo 50 trades
6. **Paper mode NO MIENTE**: Si pierdes en paper, perderás en real

---

## 📞 SOPORTE

Si tienes problemas:

1. Revisa `/tmp/bot.log` para errores
2. Ejecuta `python test_bot_v3.py`
3. Lee CHANGELOG_v3.1.md completo
4. Verifica que tienes la v3.1 (debe decir "FIXED" en los logs)

---

## ⚖️ DISCLAIMER

**Este bot es para uso educativo**.

- ✅ Los bugs técnicos están corregidos
- ✅ La configuración es más conservadora
- ❌ NO hay garantía de ganancias
- ❌ Trading de futuros es MUY arriesgado
- ❌ Puedes perder TODO tu capital

**Usa a tu propio riesgo** 🎲

---

## 🎉 CONCLUSIÓN

Tu bot v3.0 tenía bugs graves que causaron **$2234 de pérdidas**.

La v3.1 corrige:
- ✅ 3 bugs críticos
- ✅ Configuración mucho más conservadora
- ✅ 8 mejoras adicionales

**Está listo para usar, PERO**:
1. Empieza en PAPER MODE
2. Testea 1 semana mínimo
3. Real money con capital pequeño
4. Escala gradualmente

**¡Buena suerte! 🚀**
