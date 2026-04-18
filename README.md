# AEGIS GEX APEX QUANT v6.1

## Archivos del proyecto

| Archivo | Rol |
|---|---|
| `app.py` | **Bot principal** — Flask + Scanner + HMM integrado |
| `hmm_regime.py` | **Detector de régimen** — TRENDING / RANGING / VOLATILE |
| `requirements.txt` | Dependencias Python |
| `Procfile` | Arranque en Railway (gunicorn) |
| `.env.example` | Variables de entorno (copia como `.env` en local) |
| `bot.py` | ~~Versión antigua~~ — NO USAR |

---

## Deploy en Railway — checklist

### 1. Variables obligatorias

En Railway → tu servicio → **Variables**, añade exactamente estos nombres:

```
BINGX_API_KEY      = (tu API key de BingX)
BINGX_SECRET_KEY   = (tu API secret de BingX)  ← nombre exacto
MODE               = paper
```

> ⚠️ Empieza siempre con `MODE=paper`. Solo cambia a `MODE=live` después de
> verificar que el bot funciona correctamente durante al menos 1 semana.

### 2. Variables opcionales (recomendadas)

```
TELEGRAM_TOKEN     = (token de tu bot de Telegram)
TELEGRAM_CHAT_ID   = (tu chat ID)
ORDER_SIZE         = 10
LEVERAGE           = 3
```

### 3. Verificar que funciona

```bash
# Health check
curl https://tu-servicio.railway.app/

# Ver regímenes HMM activos
curl https://tu-servicio.railway.app/status

# Inteligencia de mercado
curl https://tu-servicio.railway.app/intel

# Escaneo manual
curl https://tu-servicio.railway.app/scan
```

---

## Arquitectura de señales

```
SCAN (cada 60s)
    │
    ├─► get_intel()          → F&G, SPX, OI, liquidaciones, BTC.D
    │
    └─► analyze_symbol() ──► get_regime() [HMM]
                │                 ├── RANGING  → SKIP (no operar)
                │                 ├── VOLATILE → tamaño ×0.5
                │                 └── TRENDING → normal
                │
                ├── Señal técnica (Z-Score / Absorción / Whale / BB / ...)
                │
                └── process_signal()
                        ├── Filtros (CB / Sesión / F&G / SPX / OI / Funding)
                        └── execute_order() → BingX
```

---

## Errores comunes

### "bingx requires secret credential"
→ La variable se llama `BINGX_SECRET_KEY` (no `BINGX_API_SECRET` ni `BINGX_SECRET`)

### "amount must be greater than minimum precision"
→ Corregido en v6.1. `usd_to_contracts()` ahora respeta el mínimo de BingX por símbolo.

### Bot en paper sin querer
→ Verifica que `MODE=live` esté configurado en Railway.

### HMM no disponible
→ El bot funciona igual con fallback ADX+BB. Para activar HMM:
   `pip install hmmlearn` debe estar en `requirements.txt` (ya está).
