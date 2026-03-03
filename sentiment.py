"""
sentiment.py — Filtro de sentimiento de mercado v6
Inspirado en Intellectia.ai (analisis de noticias con IA)

Fuentes GRATUITAS usadas:
  1. CryptoPanic API  — noticias crypto con votos bullish/bearish
  2. Alternative.me   — Fear & Greed Index (0-100)

Como funciona:
  - Antes de abrir un trade, consulta si hay noticias "panic" recientes
    para ese par especifico (las ultimas 2 horas)
  - Consulta el Fear & Greed Index global: evita entrar en Extreme Fear
    (< 20) o Extreme Greed (> 80) donde el mercado es irracional
  - Resultados cacheados para no spammear las APIs

Uso en main.py:
  from sentiment import sentiment_ok
  if not sentiment_ok(symbol):
      continue  # saltar esta señal
"""
import logging
import time
import requests
from datetime import datetime, timedelta
from collections import defaultdict
import config as cfg

log = logging.getLogger("sentiment")

# ── Caches ────────────────────────────────────────────────
_news_cache: dict  = {}   # symbol -> {"ts": datetime, "panic": bool, "votes": dict}
_fg_cache: dict    = {}   # {"ts": datetime, "value": int, "label": str}


# ═══════════════════════════════════════════════════════════
# FEAR & GREED INDEX
# ═══════════════════════════════════════════════════════════

def get_fear_greed() -> dict:
    """
    Obtiene el Fear & Greed Index de alternative.me (API gratuita).
    Retorna {"value": int, "label": str} o None si falla.
    """
    if not cfg.FEAR_GREED_ENABLED:
        return {"value": 50, "label": "Neutral"}

    # Usar cache si es reciente
    if _fg_cache.get("ts"):
        age_min = (datetime.now() - _fg_cache["ts"]).total_seconds() / 60
        if age_min < cfg.FEAR_GREED_CACHE_MIN:
            return _fg_cache

    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=5
        )
        data = resp.json()
        val  = int(data["data"][0]["value"])
        lbl  = data["data"][0]["value_classification"]
        result = {"value": val, "label": lbl, "ts": datetime.now()}
        _fg_cache.update(result)
        log.info(f"Fear & Greed Index: {val} ({lbl})")
        return result
    except Exception as e:
        log.warning(f"Fear & Greed no disponible: {e}")
        return {"value": 50, "label": "Neutral"}


def fear_greed_ok() -> tuple:
    """
    Retorna (ok, razon).
    Bloquea si FGI < MIN o > MAX (mercado irracional).
    En Extreme Fear no abrir LONG. En Extreme Greed no abrir SHORT.
    """
    if not cfg.FEAR_GREED_ENABLED:
        return True, ""

    fg = get_fear_greed()
    val = fg.get("value", 50)
    lbl = fg.get("label", "Neutral")

    if val < cfg.FEAR_GREED_MIN:
        return False, f"Fear & Greed muy bajo ({val} — {lbl}): mercado en panico"
    if val > cfg.FEAR_GREED_MAX:
        return False, f"Fear & Greed muy alto ({val} — {lbl}): euforia extrema"

    return True, ""


# ═══════════════════════════════════════════════════════════
# CRYPTOPANIC NEWS SENTIMENT
# ═══════════════════════════════════════════════════════════

def _extract_base(symbol: str) -> str:
    """BTC/USDT:USDT → BTC"""
    return symbol.split("/")[0].split(":")[0].upper()


def get_news_sentiment(symbol: str) -> dict:
    """
    Consulta CryptoPanic para el par dado.
    Retorna {"panic": bool, "bullish": int, "bearish": int, "total": int}

    Si CRYPTOPANIC_API_KEY esta vacio, usa el endpoint publico
    (sin auth) que tiene limite de peticiones pero funciona para pocos pares.
    """
    base  = _extract_base(symbol)
    cache = _news_cache.get(base)

    if cache:
        age_min = (datetime.now() - cache["ts"]).total_seconds() / 60
        if age_min < cfg.SENTIMENT_CACHE_MIN:
            return cache

    default = {"panic": False, "bullish": 0, "bearish": 0, "total": 0, "ts": datetime.now()}

    if not cfg.SENTIMENT_ENABLED:
        return default

    try:
        params = {
            "currencies": base,
            "filter":     "hot",
            "public":     "true",
        }
        if cfg.CRYPTOPANIC_API_KEY:
            params["auth_token"] = cfg.CRYPTOPANIC_API_KEY

        resp = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params=params,
            timeout=6
        )
        if resp.status_code != 200:
            return default

        posts   = resp.json().get("results", [])
        now     = datetime.utcnow()
        cutoff  = now - timedelta(hours=2)  # solo noticias de las ultimas 2h

        bullish = 0
        bearish = 0
        panic   = False

        for post in posts:
            try:
                pub = datetime.strptime(
                    post.get("published_at", "")[:19], "%Y-%m-%dT%H:%M:%S"
                )
            except Exception:
                continue

            if pub < cutoff:
                continue

            votes = post.get("votes", {})
            bullish += int(votes.get("positive", 0) or 0)
            bearish += int(votes.get("negative", 0) or 0)

            # Noticias con tag "negative" o muchos votos negativos = panic
            kind = post.get("kind", "")
            if kind in ("negative",) or int(votes.get("negative", 0) or 0) > 10:
                panic = True

        result = {
            "panic":   panic,
            "bullish": bullish,
            "bearish": bearish,
            "total":   bullish + bearish,
            "ts":      datetime.now(),
        }
        _news_cache[base] = result

        if panic or bearish > bullish * 2:
            log.warning(
                f"Sentimiento negativo {base}: "
                f"bullish={bullish} bearish={bearish} panic={panic}"
            )

        return result

    except Exception as e:
        log.debug(f"Sentiment error {symbol}: {e}")
        return default


# ═══════════════════════════════════════════════════════════
# FUNCION PRINCIPAL DE VALIDACION
# ═══════════════════════════════════════════════════════════

def sentiment_ok(symbol: str, action: str = "buy") -> tuple:
    """
    Valida si el sentimiento del mercado permite abrir este trade.
    Retorna (ok: bool, razon: str).

    action: "buy" (LONG) o "sell_short" (SHORT)
    """
    reasons = []

    # ── Fear & Greed global ───────────────────────────────
    fg_ok, fg_reason = fear_greed_ok()
    if not fg_ok:
        # En extreme fear bloquear solo LONG
        # En extreme greed bloquear solo SHORT
        fg = get_fear_greed()
        val = fg.get("value", 50)
        if action == "buy" and val < cfg.FEAR_GREED_MIN:
            return False, fg_reason
        if action == "sell_short" and val > cfg.FEAR_GREED_MAX:
            return False, fg_reason

    # ── Noticias CryptoPanic ──────────────────────────────
    if cfg.SENTIMENT_BLOCK_PANIC:
        news = get_news_sentiment(symbol)
        if news.get("panic"):
            return False, f"Noticias panic detectadas en {_extract_base(symbol)}"
        # Sentimiento muy negativo: bearish > 3x bullish
        bull = news.get("bullish", 0)
        bear = news.get("bearish", 0)
        if action == "buy" and bear > 0 and bull == 0:
            return False, f"Sin noticias bullish para {_extract_base(symbol)}"

    return True, ""


def get_market_mood() -> str:
    """
    Resumen del estado del mercado para logs y Telegram.
    """
    fg  = get_fear_greed()
    val = fg.get("value", 50)
    lbl = fg.get("label", "Neutral")
    return f"FGI={val} ({lbl})"
