"""
exchange.py — Wrapper para BingX Futuros (USDT-M Perpetuos)
"""
import logging
import ccxt
import config as cfg

log = logging.getLogger("exchange")

_exchange: ccxt.Exchange | None = None


def get_exchange() -> ccxt.Exchange:
    global _exchange
    if _exchange is None:
        _exchange = ccxt.bingx({
            "apiKey":  cfg.BINGX_API_KEY,
            "secret":  cfg.BINGX_SECRET,
            "options": {"defaultType": "swap"},  # futuros perpetuos
        })
    return _exchange


def get_balance() -> float:
    """Retorna el balance libre en USDT."""
    try:
        ex  = get_exchange()
        bal = ex.fetch_balance({"type": "swap"})
        return float(bal.get("USDT", {}).get("free", 0))
    except Exception as e:
        log.error(f"Error obteniendo balance: {e}")
        return 0.0


def has_enough_balance(min_usdt: float = None) -> bool:
    if min_usdt is None:
        min_usdt = cfg.MIN_USDT_BALANCE
    return get_balance() >= min_usdt


def set_leverage(symbol: str) -> bool:
    try:
        get_exchange().set_leverage(cfg.LEVERAGE, symbol)
        return True
    except Exception as e:
        log.warning(f"No se pudo fijar leverage {symbol}: {e}")
        return False


def get_open_positions() -> list:
    """Retorna lista de posiciones abiertas."""
    try:
        ex        = get_exchange()
        positions = ex.fetch_positions(cfg.SYMBOLS)
        open_pos  = []
        for p in positions:
            if float(p.get("contracts", 0)) != 0:
                open_pos.append({
                    "symbol":   p["symbol"],
                    "side":     p["side"],
                    "entry":    float(p.get("entryPrice", 0)),
                    "current":  float(p.get("markPrice", 0)),
                    "qty":      float(p.get("contracts", 0)),
                    "pnl":      float(p.get("unrealizedPnl", 0)),
                    "sl":       float(p.get("stopLossPrice", 0)),
                })
        return open_pos
    except Exception as e:
        log.error(f"Error obteniendo posiciones: {e}")
        return []


def open_long(symbol: str, signal: dict) -> dict | None:
    """
    Abre una posicion LONG con orden de mercado + stop loss.
    Calcula el tamanio basado en RISK_PCT del balance actual.
    """
    try:
        ex      = get_exchange()
        balance = get_balance()
        price   = signal["entry"]
        sl      = signal["sl"]
        sl_dist = price - sl

        if sl_dist <= 0:
            log.error(f"SL inválido para {symbol}: price={price} sl={sl}")
            return None

        risk_usdt = balance * cfg.RISK_PCT
        qty       = round((risk_usdt * cfg.LEVERAGE) / price, 4)

        if qty <= 0:
            log.error(f"Cantidad calculada 0 para {symbol} (balance=${balance:.2f})")
            return None

        set_leverage(symbol)

        # Orden principal LONG
        order = ex.create_order(
            symbol=symbol,
            type="market",
            side="buy",
            amount=qty,
        )
        log.info(f"LONG abierto: {symbol} qty={qty} @ ~{price}")

        # Stop Loss
        try:
            ex.create_order(
                symbol=symbol,
                type="stop_market",
                side="sell",
                amount=qty,
                params={"stopPrice": sl, "reduceOnly": True}
            )
            log.info(f"Stop loss fijado: {sl}")
        except Exception as e:
            log.warning(f"Stop loss no aplicado (fijalo manualmente en {sl}): {e}")

        return {"symbol": symbol, "qty": qty, "entry": price, "sl": sl, "tp": signal["tp"]}

    except Exception as e:
        log.error(f"Error abriendo LONG {symbol}: {e}")
        return None


def close_long(symbol: str, qty: float) -> bool:
    """Cierra una posicion LONG existente."""
    try:
        get_exchange().create_order(
            symbol=symbol,
            type="market",
            side="sell",
            amount=qty,
            params={"reduceOnly": True}
        )
        log.info(f"Posicion cerrada: {symbol}")
        return True
    except Exception as e:
        log.error(f"Error cerrando {symbol}: {e}")
        return False
