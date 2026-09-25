# EP5 v4.1 · Entrada Precisa 5m — bot BingX

Estrategia EP5 (cruce EMA/SMA + pullback a la EMA 15m + filtros ER/wavelet/RVOL/VWAP/sesión,
confluencias sweep/FVG/killzone/dominancia) reparada y llevada a Python.

## Archivos
| archivo | qué hace |
|---|---|
| `main.py` | bucle cada vela 5m: datos → gestión → entradas. PAPER o REAL |
| `strategy.py` | indicadores, señal y gestión de posición (compartidos con el backtest) |
| `bingx.py` | cliente BingX (firma ordenada, POST en body, sin reintentos en órdenes) |
| `state.py` | estado JSON atómico en `/data` + diario `ep5_journal.csv` |
| `notify.py` | Telegram |
| `backtest.py` | backtest con velas reales de BingX, misma lógica (caché en `bt_cache/`) |
| `walkforward.py` | ajuste en 1ª mitad, validación en 2ª, robustez y VEREDICTO |
| `test_offline.py` | pruebas sin red (firma, no-lookahead, bot PAPER y REAL contra exchange simulado) |

## Railway
1. Repo nuevo en GitHub con estos archivos → proyecto Railway propio, servicio worker.
2. Volumen montado en `/data`.
3. Variables: pegar `.env.example` en el Raw Editor y rellenar claves (subcuenta BingX propia).
4. Arranca en `DRY_RUN=true`. Log de arranque muestra `CODE_VERSION=EP5-v4.1.0`.

## Comandos Telegram (solo desde TELEGRAM_CHAT_ID)
`/estado` · `/pausa` · `/reanudar` · `/cerrar_todo` · `/ayuda`

## Protecciones
filtro régimen BTC 1h · límite diario (R) · pausa por drawdown total (`MAX_DD_R`) · exposición máx
(`MAX_EXPOSURE_X` × equity) · evita funding en contra (`FUNDING_AVOID_MIN`) · watchdog (reinicia si se cuelga) ·
PnL real desde BingX y slippage en el diario (`r_real`, `slip_bps`).

## Antes de pasar a real
```
python walkforward.py --days 60 --top 30
```
Solo si imprime `→ PASA`: 2 semanas en PAPER, luego real con `RISK_PCT=0.1` en subcuenta propia.
