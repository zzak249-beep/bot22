"""
exchange.py — BingX Futuros ELITE v2
Mejoras v2:
- API keys leídas desde variables de entorno (Railway) con fallback a config.py
- Validación explícita de keys al arrancar
- Retry automático (3 intentos) en errores de red
- Verificación de mínimos de cantidad y coste por par
- Gestión de SL por orden separada (más compatible con BingX)
- Logs más detallados para debugging
"""

import logging
import os
import time
import ccxt
import config as cfg

log = logging.getLogger("exchange")
_exchange = None

PAPER_MODE = False   # True = simula sin dinero real


# ──────────────────────────────────────────────
# CONEXIÓN
# ──────────────────────────────────────────────

def get_exchange():
    """
    Devuelve la instancia ccxt de BingX.
    Lee las keys primero de variables de entorno (Railway),
    y si no existen las busca en config.py como fallback.
    """
    global _exchange
    if _exchange is None:
        api_key = os.environ.get("BINGX_API_KEY") or getattr(cfg, "BINGX_API_KEY", "")
        secret  = os.environ.get("BINGX_SECRET")  or getattr(cfg, "BINGX_SECRET", "")

        if not api_key or not secret:
            log.error("❌ BINGX_API_KEY o BINGX_SECRET no están configurados. "
                      "Añádelos en Railway → Variables.")
            return None

        _exchange = ccxt.bingx({
            "apiKey":  api_key,
            "secret":  secret,
            "options": {"defaultType": "swap"},
            "timeout": 15000,
            "enableRateLimit": True,
        })
        log.info("✅ Conexión BingX iniciada correctamente")
    return _exchange


def _retry(fn, retries=3, delay=2):
    """Ejecuta fn hasta 'retries' veces ante errores de red."""
    for attempt in range(retries):
        try:
            return fn()
        except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
            if attempt < retries - 1:
                log.warning(f"Error de red (intento {attempt+1}/{retries}): {e} — reintentando...")
                time.sleep(delay)
            else:
                raise
        except Exception:
            raise


# ──────────────────────────────────────────────
# BALANCE
# ──────────────────────────────────────────────

def get_balance():
    if PAPER_MODE:
        return 100.0
    try:
        ex  = get_exchange()
        if ex is None:
            return 0.0
        bal = _retry(lambda: ex.fetch_balance({"type": "swap"}))
        return float(bal.get("USDT", {}).get("free") or 0)
    except Exception as e:
        log.error(f"Error balance: {e}")
        return 0.0


def has_enough_balance(min_usdt=None):
    if min_usdt is None:
        min_usdt = getattr(cfg, "MIN_USDT_BALANCE", 5.0)
    bal = get_balance()
    if bal < min_usdt:
        log.warning(f"Balance insuficiente: ${bal:.2f} < ${min_usdt:.2f} mínimo")
        return False
    return True


# ──────────────────────────────────────────────
# APALANCAMIENTO
# ──────────────────────────────────────────────

def set_leverage(symbol, side="BOTH"):
    if PAPER_MODE:
        return True
    ex = get_exchange()
    if ex is None:
        return False
    try:
        try:
            ex.set_leverage(cfg.LEVERAGE, symbol, params={"side": side})
        except Exception:
            ex.set_leverage(cfg.LEVERAGE, symbol, params={"side": "BOTH"})
        log.debug(f"Leverage {cfg.LEVERAGE}x aplicado a {symbol}")
        return True
    except Exception as e:
        log.warning(f"Leverage no aplicado {symbol}: {e}")
        return False


# ──────────────────────────────────────────────
# CANTIDAD MÍNIMA POR PAR
# ──────────────────────────────────────────────

def _adjust_qty(ex, symbol, qty, price):
    """
    Ajusta qty para respetar los mínimos del par en BingX.
    Devuelve qty ajustada o None si es imposible.
    """
    try:
        market   = ex.market(symbol)
        limits   = market.get("limits", {})
        min_qty  = float(limits.get("amount", {}).get("min") or 0)
        min_cost = float(limits.get("cost",   {}).get("min") or 0)

        if min_qty > 0 and qty < min_qty:
            log.warning(f"Qty {qty:.6f} < mínimo {min_qty:.6f} → ajustando")
            qty = min_qty

        if min_cost > 0 and qty * price < min_cost:
            qty = round(min_cost / price * 1.05, 6)
            log.warning(f"Coste mínimo ${min_cost} → qty ajustada a {qty:.6f}")

        # Redondear al precision del par
        precision = market.get("precision", {}).get("amount")
        if precision:
            qty = round(qty, int(precision))

    except Exception as e:
        log.debug(f"No se pudo verificar mínimos {symbol}: {e}")

    return qty if qty > 0 else None


# ──────────────────────────────────────────────
# POSICIONES ABIERTAS
# ──────────────────────────────────────────────

def get_open_positions():
    if PAPER_MODE:
        return []
    ex = get_exchange()
    if ex is None:
        return []
    try:
        positions = _retry(lambda: ex.fetch_positions())
        open_pos  = []
        for p in positions:
            if float(p.get("contracts") or 0) != 0:
                open_pos.append({
                    "symbol":  p["symbol"],
                    "side":    p["side"],
                    "entry":   float(p.get("entryPrice")    or 0),
                    "current": float(p.get("markPrice")     or 0),
                    "qty":     float(p.get("contracts")     or 0),
                    "pnl":     float(p.get("unrealizedPnl") or 0),
                    "sl":      float(p.get("stopLossPrice") or 0),
                })
        return open_pos
    except Exception as e:
        log.error(f"Error posiciones: {e}")
        return []


# ──────────────────────────────────────────────
# ABRIR LONG
# ──────────────────────────────────────────────

def open_long(symbol, signal):
    price = signal["entry"]
    sl    = signal["sl"]
    tp    = signal["tp"]

    if PAPER_MODE:
        bal = get_balance()
        qty = round((bal * cfg.RISK_PCT * cfg.LEVERAGE) / price, 4) or 0.001
        log.info(f"[PAPER] LONG {symbol} qty={qty} @ {price} | SL={sl} TP={tp}")
        return {"symbol": symbol, "qty": qty, "entry": price,
                "sl": sl, "tp": tp, "tp_partial": signal.get("tp_partial"), "side": "long"}

    ex = get_exchange()
    if ex is None:
        return None

    try:
        if price <= 0 or sl >= price:
            log.error(f"Señal inválida LONG {symbol}: price={price} sl={sl}")
            return None

        bal = get_balance()
        qty = round((bal * cfg.RISK_PCT * cfg.LEVERAGE) / price, 6)
        qty = _adjust_qty(ex, symbol, qty, price)
        if qty is None:
            log.error(f"Qty inválida para {symbol} con balance ${bal:.2f}")
            return None

        set_leverage(symbol, side="LONG")

        # Orden de entrada
        _retry(lambda: ex.create_order(
            symbol=symbol, type="market", side="buy", amount=qty
        ))
        log.info(f"✅ LONG abierto: {symbol} qty={qty} @ ~{price:.4f}")

        # Stop Loss
        try:
            _retry(lambda: ex.create_order(
                symbol=symbol, type="stop_market", side="sell",
                amount=qty, params={"stopPrice": sl, "reduceOnly": True}
            ))
            log.info(f"   SL fijado: {sl:.4f}")
        except Exception as e:
            log.warning(f"   ⚠️ SL no aplicado automáticamente ({sl:.4f}): {e}")

        return {"symbol": symbol, "qty": qty, "entry": price,
                "sl": sl, "tp": tp, "tp_partial": signal.get("tp_partial"), "side": "long"}

    except Exception as e:
        log.error(f"❌ Error abriendo LONG {symbol}: {e}")
        return None


# ──────────────────────────────────────────────
# ABRIR SHORT
# ──────────────────────────────────────────────

def open_short(symbol, signal):
    price = signal["entry"]
    sl    = signal["sl"]
    tp    = signal["tp"]

    if PAPER_MODE:
        bal = get_balance()
        qty = round((bal * cfg.RISK_PCT * cfg.LEVERAGE) / price, 4) or 0.001
        log.info(f"[PAPER] SHORT {symbol} qty={qty} @ {price} | SL={sl} TP={tp}")
        return {"symbol": symbol, "qty": qty, "entry": price,
                "sl": sl, "tp": tp, "tp_partial": signal.get("tp_partial"), "side": "short"}

    ex = get_exchange()
    if ex is None:
        return None

    try:
        if price <= 0 or sl <= price:
            log.error(f"Señal inválida SHORT {symbol}: price={price} sl={sl}")
            return None

        bal = get_balance()
        qty = round((bal * cfg.RISK_PCT * cfg.LEVERAGE) / price, 6)
        qty = _adjust_qty(ex, symbol, qty, price)
        if qty is None:
            log.error(f"Qty inválida SHORT {symbol} con balance ${bal:.2f}")
            return None

        set_leverage(symbol, side="SHORT")

        _retry(lambda: ex.create_order(
            symbol=symbol, type="market", side="sell", amount=qty
        ))
        log.info(f"✅ SHORT abierto: {symbol} qty={qty} @ ~{price:.4f}")

        try:
            _retry(lambda: ex.create_order(
                symbol=symbol, type="stop_market", side="buy",
                amount=qty, params={"stopPrice": sl, "reduceOnly": True}
            ))
            log.info(f"   SL SHORT fijado: {sl:.4f}")
        except Exception as e:
            log.warning(f"   ⚠️ SL SHORT no aplicado ({sl:.4f}): {e}")

        return {"symbol": symbol, "qty": qty, "entry": price,
                "sl": sl, "tp": tp, "tp_partial": signal.get("tp_partial"), "side": "short"}

    except Exception as e:
        log.error(f"❌ Error abriendo SHORT {symbol}: {e}")
        return None


# ──────────────────────────────────────────────
# CERRAR POSICIONES
# ──────────────────────────────────────────────

def close_long(symbol, qty):
    if PAPER_MODE:
        log.info(f"[PAPER] LONG cerrado: {symbol} qty={qty}")
        return True
    ex = get_exchange()
    if ex is None:
        return False
    try:
        _retry(lambda: ex.create_order(
            symbol=symbol, type="market", side="sell",
            amount=qty, params={"reduceOnly": True}
        ))
        log.info(f"✅ LONG cerrado: {symbol} qty={qty}")
        return True
    except Exception as e:
        log.error(f"❌ Error cerrando LONG {symbol}: {e}")
        return False


def close_short(symbol, qty):
    if PAPER_MODE:
        log.info(f"[PAPER] SHORT cerrado: {symbol} qty={qty}")
        return True
    ex = get_exchange()
    if ex is None:
        return False
    try:
        _retry(lambda: ex.create_order(
            symbol=symbol, type="market", side="buy",
            amount=qty, params={"reduceOnly": True}
        ))
        log.info(f"✅ SHORT cerrado: {symbol} qty={qty}")
        return True
    except Exception as e:
        log.error(f"❌ Error cerrando SHORT {symbol}: {e}")
        return False


# ──────────────────────────────────────────────
# CANCELAR ÓRDENES PENDIENTES (SL huérfanos)
# ──────────────────────────────────────────────

def cancel_open_orders(symbol):
    """Cancela órdenes pendientes de un par (útil tras cerrar posición manualmente)."""
    if PAPER_MODE:
        return True
    ex = get_exchange()
    if ex is None:
        return False
    try:
        orders = _retry(lambda: ex.fetch_open_orders(symbol))
        for o in orders:
            try:
                ex.cancel_order(o["id"], symbol)
                log.info(f"Orden cancelada: {o['id']} {symbol}")
            except Exception as e:
                log.warning(f"No se pudo cancelar orden {o['id']}: {e}")
        return True
    except Exception as e:
        log.error(f"Error cancelando órdenes {symbol}: {e}")
        return False
