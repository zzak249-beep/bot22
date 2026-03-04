# sentiment.py — módulo de sentimiento de mercado
# Versión stub: devuelve valores neutros si no hay API configurada

import logging
log = logging.getLogger("sentiment")

try:
    import config as cfg
    SENTIMENT_ENABLED  = getattr(cfg, "SENTIMENT_ENABLED",  False)
    FEAR_GREED_ENABLED = getattr(cfg, "FEAR_GREED_ENABLED", False)
except Exception:
    SENTIMENT_ENABLED  = False
    FEAR_GREED_ENABLED = False


def get_market_mood() -> str:
    """Devuelve el estado general del mercado."""
    if not SENTIMENT_ENABLED and not FEAR_GREED_ENABLED:
        return "neutral"
    try:
        return _fetch_fear_greed()
    except Exception as e:
        log.debug(f"sentiment error: {e}")
        return "neutral"


def _fetch_fear_greed() -> str:
    """Fear & Greed Index de alternative.me"""
    import requests
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5).json()
    value = int(r["data"][0]["value"])
    if   value <= 25: return "extreme_fear"
    elif value <= 45: return "fear"
    elif value <= 55: return "neutral"
    elif value <= 75: return "greed"
    else:             return "extreme_greed"


def sentiment_ok(symbol: str, action: str) -> tuple:
    """
    Verifica si el sentimiento permite la operación.
    Retorna (permitido, motivo).
    """
    if not SENTIMENT_ENABLED:
        return True, ""

    try:
        mood = get_market_mood()
        # Bloquear longs en miedo extremo
        if action == "buy" and mood == "extreme_fear":
            return False, f"Sentimiento: {mood} — LONG bloqueado"
        # Bloquear shorts en codicia extrema
        if action == "sell_short" and mood == "extreme_greed":
            return False, f"Sentimiento: {mood} — SHORT bloqueado"
        return True, ""
    except Exception as e:
        log.debug(f"sentiment_ok error: {e}")
        return True, ""
