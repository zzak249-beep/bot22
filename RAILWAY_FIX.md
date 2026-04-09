# 🔴 FIX URGENTE - ModuleNotFoundError: No module named 'requests'

## El Problema

Railway dice:
```
ModuleNotFoundError: No module named 'requests'
File "/app/main.py", line 20, in <module>
import requests
```

**Causa:** El archivo `requirements.txt` está incompleto o no se actualizó en Railway.

---

## ✅ Solución (2 pasos, 5 minutos)

### Paso 1: Actualizar requirements.txt

```bash
# En tu proyecto local, crea/actualiza requirements.txt:

cat > requirements.txt << 'EOF'
fastapi==0.104.1
uvicorn==0.24.0
requests==2.31.0
httpx==0.25.0
pandas==2.1.3
numpy==1.26.2
python-dotenv==1.0.0
pydantic==2.5.0
EOF
```

O simplemente reemplaza con el archivo que te di: `requirements.txt`

### Paso 2: Subir a Railway

```bash
git add requirements.txt
git commit -m "fix: Update requirements.txt with all dependencies"
git push

# Railway auto-detectará el cambio y hará re-deployment
# Espera 2-3 minutos
```

---

## ✅ Verificar que todo está bien

Después de que Railway termine el deployment (estado = "Active"), verifica los logs:

```
[INFO] TradingBot: 🚀 Bot iniciado
[INFO] TelegramBot: ✅ Token válido
[INFO] Successfully started server process
```

Si ves esto, ¡está funcionando! 🚀

---

## 🆘 Si sigue fallando

### Debug Step 1: Listar dependencias
```bash
# Desde local:
pip list | grep -E "requests|pandas|fastapi|uvicorn"

# Deberías ver:
# fastapi           0.104.1
# requests          2.31.0
# pandas            2.1.3
# uvicorn           0.24.0
```

### Debug Step 2: Validar requirements.txt
```bash
# Verifica que el archivo existe y es válido:
cat requirements.txt

# Debe tener (como mínimo):
# requests
# fastapi
# uvicorn
# pandas
# numpy
# python-dotenv
# pydantic
# httpx
```

### Debug Step 3: Forzar reinstalación en Railway
```bash
# A veces Railway cachea los paquetes. Fuerza rebuild:

# En Railway dashboard:
# 1. Ve a "Settings" → "Build"
# 2. Haz click en "Trigger Deploy" con la bandera de "Full rebuild"

# O desde git:
git commit --allow-empty -m "trigger: Force Railway rebuild"
git push
```

---

## 📋 Checklist Antes de Subir a Railway

- [ ] `requirements.txt` existe en la raíz del proyecto
- [ ] `requirements.txt` contiene: fastapi, uvicorn, requests, pandas, numpy, httpx, python-dotenv, pydantic
- [ ] `main.py` o `main_optimized.py` usa imports correctos
- [ ] Archivo `.env` tiene TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
- [ ] Git commit y push hecho

---

## 🎯 Archivos Correctos que Deberías Tener

```
tu-proyecto/
├── main.py                    (o main_optimized.py)
├── requirements.txt           ← ⭐ IMPORTANTE
├── .env
├── bingx_client.py
├── config.py
├── data_fetcher.py
├── risk_manager.py
├── strategy_engine.py
├── telegram_bot.py            (o telegram_bot_v2.py)
├── telegram_diagnostics.py    (NEW)
├── trade_executor.py
├── trade_journal.json
├── sniper_strategy.py         (NEW)
└── sniper_indicator_v3.pine
```

---

## ⏱️ Timeline Esperado Después del Fix

| Tiempo | Qué pasa |
|--------|----------|
| T+0s   | Haces `git push` |
| T+10s  | Railway detecta cambio |
| T+30s  | Inicia build (descarga paquetes) |
| T+90s  | Build completo, deployment inicia |
| T+120s | Bot está "Active" |
| T+130s | Primer scan completo |

---

## 📞 Si Aún No Funciona

Verifica estos archivos están en Railway:

1. Abre Railway dashboard
2. Ve a "Files" 
3. Busca `/app/` y expande
4. Deberías ver: `main.py`, `requirements.txt`, `.env`, etc.

Si faltan archivos:
```bash
# Haz push completo:
git add .
git commit -m "chore: Add all project files"
git push
```

---

## ✅ Success Indicators

Una vez funcione, verás en los logs de Railway:

```
2026-04-09T10:29:47.123 [INFO] TradingBot: 🚀 Bot iniciado
2026-04-09T10:29:48.456 [INFO] TelegramBot: ✅ Token válido
2026-04-09T10:29:49.789 [INFO] TelegramBot: ✅ Chat válido
2026-04-09T10:30:00.000 [INFO] 📊 Scan completado...
```

Y en Telegram recibirás el mensaje de startup. 🎉
