"""
bot.py — DEPRECATED (v1, no usar)
══════════════════════════════════════════════════════════════
Este archivo es la versión original del bot (UltimateQuantBot).
Fue reemplazado por app.py (AEGIS GEX APEX QUANT v6.1).

PROBLEMAS QUE TENÍA:
  ❌ Nunca operaba SHORT (solo BUY)
  ❌ IA (RandomForest) inicializada pero nunca entrenada ni usada
  ❌ ORDER_SIZE=0.001 → demasiado pequeño para ETH (mínimo BingX: 0.01)
  ❌ Sin filtros institucionales (F&G, SPX, OI, funding...)
  ❌ Sin circuit breaker
  ❌ Sin trailing stop inteligente
  ❌ Sin HMM regime detector
  ❌ BINGX_SECRET_KEY nombre incorrecto

USA app.py EN SU LUGAR.
══════════════════════════════════════════════════════════════
"""

raise SystemExit(
    "bot.py está DEPRECATED. El bot activo es app.py. "
    "Revisa el README o el .env.example."
)
