"""
exchange.py — BingX Futuros con soporte LONG y SHORT
Activa PAPER_MODE = True para simular sin dinero real.
"""
import logging
import ccxt
import config as cfg

log = logging.getLogger("exchange")
_exchange = None

PAPER_MODE = False  # Cambia a True para simular sin dinero real


def get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = ccxt.bingx({
            "apiKey":  cfg.BINGX_API_KEY,
            "secret":  cfg.BINGX_SECRET,
            "options": {"defaultType": "swap"},
        })
    return _exchange


def get_balance():
    if PAPER_MODE:
        return 100.0
    try:
        bal = get_exchange().fetch_balance({"type": "swap"})
        return float(bal.get("USDT", {}).get("free", 0))
    except Exception as e:
        log.error(f"Error balance: {e}")
        return 0.0


def has_enough_balance(min_usdt=None):
    if min_usdt is None:
        min_usdt = cfg.MIN_USDT_BALANCE
    return get_balance() >= min_usdt


def set_leverage(symbol):
    if PAPER_MODE:
        return True
    try:
        get_exchange().set_leverage(cfg.LEVERAGE, symbol)
        return True
    except Exception as e:
        log.warning(f"Leverage no aplicado {symbol}: {e}")
        return False


def get_open_positions():
    if PAPER_MODE:
        return []
    try:
        ex        = get_exchange()
        positions = ex.fetch_positions()
        open_pos  = []
        for p in positions:
            if float(p.get("contracts", 0)) != 0:
                open_pos.append({
                    "symbol":  p["symbol"],
                    "side":    p["side"],
                    "entry":   float(p.get("entryPrice", 0)),
                    "current": float(p.get("markPrice", 0)),
                    "qty":     float(p.get("contracts", 0)),
                    "pnl":     float(p.get("unrealizedPnl", 0)),
                    "sl":      float(p.get("stopLossPrice", 0)),
                })
        return open_pos
    except Exception as e:
        log.error(f"Error posiciones: {e}")
        return []


def open_long(symbol, signal):
    if PAPER_MODE:
        balance = get_balance()
        price   = signal["entry"]
        qty     = round((balance * cfg.RISK_PCT * cfg.LEVERAGE) / price, 4) or 0.001
        log.info(f"[PAPER] LONG {symbol} qty={qty} @ {price}")
        return {"symbol": symbol, "qty": qty, "entry": price, "sl": signal["sl"],
                "tp": signal["tp"], "tp_partial": signal.get("tp_partial"), "side": "long"}
    try:
        ex      = get_exchange()
        balance = get_balance()
        price   = signal["entry"]
        sl      = signal["sl"]
        if price - sl <= 0:
            log.error(f"SL invalido {symbol}")
            return None
        qty = round((balance * cfg.RISK_PCT * cfg.LEVERAGE) / price, 4)
        if qty <= 0:
            log.error(f"Qty=0 {symbol} balance=${balance:.2f}")
            return None
        set_leverage(symbol)
        ex.create_order(symbol=symbol, type="market", side="buy", amount=qty)
        log.info(f"LONG abierto: {symbol} qty={qty} @ ~{price}")
        try:
            ex.create_order(symbol=symbol, type="stop_market", side="sell",
                            amount=qty, params={"stopPrice": sl, "reduceOnly": True})
            log.info(f"SL fijado: {sl}")
        except Exception as e:
            log.warning(f"SL no aplicado (fijalo manual en {sl}): {e}")
        return {"symbol": symbol, "qty": qty, "entry": price, "sl": sl,
                "tp": signal["tp"], "tp_partial": signal.get("tp_partial"), "side": "long"}
    except Exception as e:
        log.error(f"Error LONG {symbol}: {e}")
        return None


def open_short(symbol, signal):
    if PAPER_MODE:
        balance = get_balance()
        price   = signal["entry"]
        qty     = round((balance * cfg.RISK_PCT * cfg.LEVERAGE) / price, 4) or 0.001
        log.info(f"[PAPER] SHORT {symbol} qty={qty} @ {price}")
        return {"symbol": symbol, "qty": qty, "entry": price, "sl": signal["sl"],
                "tp": signal["tp"], "tp_partial": signal.get("tp_partial"), "side": "short"}
    try:
        ex      = get_exchange()
        balance = get_balance()
        price   = signal["entry"]
        sl      = signal["sl"]
        if sl - price <= 0:
            log.error(f"SL invalido SHORT {symbol}")
            return None
        qty = round((balance * cfg.RISK_PCT * cfg.LEVERAGE) / price, 4)
        if qty <= 0:
            return None
        set_leverage(symbol)
        ex.create_order(symbol=symbol, type="market", side="sell", amount=qty)
        log.info(f"SHORT abierto: {symbol} qty={qty} @ ~{price}")
        try:
            ex.create_order(symbol=symbol, type="stop_market", side="buy",
                            amount=qty, params={"stopPrice": sl, "reduceOnly": True})
            log.info(f"SL SHORT fijado: {sl}")
        except Exception as e:
            log.warning(f"SL SHORT no aplicado: {e}")
        return {"symbol": symbol, "qty": qty, "entry": price, "sl": sl,
                "tp": signal["tp"], "tp_partial": signal.get("tp_partial"), "side": "short"}
    except Exception as e:
        log.error(f"Error SHORT {symbol}: {e}")
        return None


def close_long(symbol, qty):
    if PAPER_MODE:
        log.info(f"[PAPER] LONG cerrado: {symbol} qty={qty}")
        return True
    try:
        get_exchange().create_order(symbol=symbol, type="market", side="sell",
                                    amount=qty, params={"reduceOnly": True})
        log.info(f"LONG cerrado: {symbol}")
        return True
    except Exception as e:
        log.error(f"Error cerrando LONG {symbol}: {e}")
        return False


def close_short(symbol, qty):
    if PAPER_MODE:
        log.info(f"[PAPER] SHORT cerrado: {symbol} qty={qty}")
        return True
    try:
        get_exchange().create_order(symbol=symbol, type="market", side="buy",
                                    amount=qty, params={"reduceOnly": True})
        log.info(f"SHORT cerrado: {symbol}")
        return True
    except Exception as e:
        log.error(f"Error cerrando SHORT {symbol}: {e}")
        return False