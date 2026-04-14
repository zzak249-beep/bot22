"""
BULL TARAMA Bot v2 — 5 Capas + HMM por símbolo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAPA 1  HMA 7/9 cruce (LONG + SHORT)
CAPA 2  Confirmación MTF 5min
CAPA 3  HMM por símbolo → RANGING / TRENDING / VOLATILE
          (fallback a ADX+BB si hmmlearn no está instalado)
CAPA 4  CVD sintético — order flow real
CAPA 5  Liquidity Sweep detector
+       Partial TP 50% + Breakeven + Trailing HMA7
+       Sizing adaptativo por volatilidad
+       Cooldown 10min por par
+       Filtro funding rate ±0.1%

HMM ajusta parámetros por par en tiempo real:
  RANGING  → skip entrada (MAX_POSITIONS efectivo = 0 para ese par)
  TRENDING → parámetros normales
  VOLATILE → TRADE_USDT × 0.5  |  SL_ATR × 1.2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Exchange: BingX Perpetual Futures
Deploy:   Railway (Procfile)
"""

import os
import math
import time
import logging

import bingx
import scanner
import pos_manager
import notifier
import hmm_regime   # ← módulo HMM nuevo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

# ─── Config base ──────────────────────────────────────────────────────────────
LEVERAGE       = int(os.environ.get("LEVERAGE",           10))
TRADE_USDT     = float(os.environ.get("TRADE_USDT",       10))
MAX_POSITIONS  = int(os.environ.get("MAX_POSITIONS",        5))
SCAN_EVERY     = int(os.environ.get("SCAN_INTERVAL_SEC",   60))
STATUS_EVERY   = int(os.environ.get("STATUS_EVERY_SCANS",  30))

# ── Multiplicadores HMM por régimen ───────────────────────────────────────────
# VOLATILE reduce tamaño y amplía SL para gestionar mejor el riesgo
HMM_VOL_SIZE_MULT = float(os.environ.get("HMM_VOL_SIZE_MULT", 0.5))   # TRADE_USDT × 0.5
HMM_VOL_SL_MULT   = float(os.environ.get("HMM_VOL_SL_MULT",   1.2))   # SL_ATR × 1.2


# ─── Helpers ──────────────────────────────────────────────────────────────────

def calc_qty(symbol: str, usdt: float, leverage: int,
             atr_mult: float = 1.0) -> float:
    """usdt × leverage / price × atr_mult (reducido en alta volatilidad)"""
    price = bingx.get_price(symbol)
    if price == 0:
        return 0.0
    notional = usdt * leverage * atr_mult
    qty      = notional / price
    return math.floor(qty * 1000) / 1000


def startup_sync() -> set:
    """Sync posiciones abiertas en Railway restart para evitar duplicados."""
    positions = bingx.get_open_positions()
    symbols   = {p["symbol"] for p in positions}
    if symbols:
        logger.info(f"Startup sync: {len(symbols)} abiertas → {symbols}")
        notifier.send(
            f"🔄 <b>Sincronización startup</b>\n"
            f"Posiciones activas: {', '.join(symbols) or 'ninguna'}"
        )
    return symbols


def apply_hmm_params(regime: str, base_usdt: float, base_sl_atr: float) -> tuple:
    """
    Devuelve (trade_usdt, sl_atr_mult) ajustados según el régimen HMM.

    TRENDING  → sin cambios
    VOLATILE  → tamaño reducido + SL más amplio
    RANGING   → nunca llega aquí (se filtra antes)
    """
    if regime == "VOLATILE":
        return base_usdt * HMM_VOL_SIZE_MULT, base_sl_atr * HMM_VOL_SL_MULT
    return base_usdt, base_sl_atr


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("  BULL TARAMA Bot v2 — 5 Capas + HMM — BingX Perpetuos")
    logger.info(f"  LEV={LEVERAGE}x | TRADE=${TRADE_USDT} | MAX={MAX_POSITIONS}")
    logger.info(f"  HMM disponible: {hmm_regime.HMM_AVAILABLE}")
    logger.info("=" * 60)

    notifier.send(
        "🚀 <b>BULL TARAMA v2 + HMM iniciado</b>\n"
        f"⚙️ Leverage: {LEVERAGE}x | Trade: ${TRADE_USDT} | Max pos: {MAX_POSITIONS}\n"
        f"🧬 HMM: {'activo' if hmm_regime.HMM_AVAILABLE else 'fallback ADX+BB'}\n"
        f"   RANGING → pausa | VOLATILE → ×{HMM_VOL_SIZE_MULT} tamaño\n"
        f"🧠 Capas: HMA + MTF + HMM + CVD + Sweep"
    )

    existing      = startup_sync()
    scan_count    = 0
    total_signals = 0
    candles_cache = {}

    while True:
        try:
            scan_count += 1
            logger.info(f"─── Scan #{scan_count} ───")

            # ── Refresh posiciones abiertas ───────────────────────────────────
            positions    = bingx.get_open_positions()
            open_symbols = {p["symbol"] for p in positions} | pos_manager.active_symbols()
            slots_free   = MAX_POSITIONS - len(open_symbols)
            balance      = bingx.get_balance()

            logger.info(
                f"Balance: ${balance:.2f} | "
                f"Open: {len(open_symbols)}/{MAX_POSITIONS} | "
                f"Slots: {slots_free} | "
                f"HMM activos: {len(hmm_regime.active_regimes())}"
            )

            # ── Gestión de trades existentes (breakeven, partial TP, trailing) ─
            if pos_manager.active_symbols():
                for sym in list(pos_manager.active_symbols()):
                    c = bingx.get_klines(sym, interval="1m", limit=60)
                    if c:
                        candles_cache[sym] = c
                pos_manager.manage_all(candles_cache)

            # ── Escaneo de nuevas entradas ─────────────────────────────────────
            if slots_free > 0:
                signals = scanner.scan_all(open_symbols)
                total_signals += len(signals)
                entered = 0

                for sig in signals:
                    if entered >= slots_free:
                        break

                    sym       = sig["symbol"]
                    direction = sig["signal"]
                    entry     = sig["entry"]
                    sl        = sig["sl"]
                    tp        = sig["tp"]
                    sl_atr    = sig["sl_atr"]
                    regime_s  = sig["regime"]   # régimen de strategy.py (capa 3 original)
                    cvd       = sig["cvd"]
                    sweep     = sig.get("sweep", False)
                    funding   = sig.get("funding", 0.0)
                    atr_mult  = sig.get("atr_mult", 1.0)
                    reason    = sig["reason"]
                    candles_c = sig.get("candles", [])

                    # ── CAPA 3 HMM: régimen por símbolo ──────────────────────
                    # Obtenemos las velas del símbolo (ya cargadas por scanner)
                    # y consultamos el HMM individual de ese par.
                    hmm_candles = candles_c if candles_c else candles_cache.get(sym, [])
                    regime_hmm  = "TRENDING"   # default si no hay velas

                    if hmm_candles:
                        regime_hmm = hmm_regime.get_regime(
                            symbol=sym,
                            candles=hmm_candles,
                            notify_fn=notifier.send    # ← avisa si cambia régimen
                        )

                    # RANGING → skip este par completamente
                    if regime_hmm == "RANGING":
                        logger.info(f"{sym} HMM=RANGING → skip")
                        continue

                    # VOLATILE / TRENDING → ajustar parámetros
                    trade_usdt_adj, sl_atr_adj = apply_hmm_params(
                        regime_hmm, TRADE_USDT, sl_atr
                    )

                    # Recalcular SL y TP si VOLATILE ajustó sl_atr
                    if regime_hmm == "VOLATILE":
                        sl_diff = sl_atr_adj - sl_atr   # diferencia extra
                        if direction == "LONG":
                            sl = entry - sl_atr_adj * 1.0   # re-calcula con SL más amplio
                            tp = entry + (sl_atr_adj * float(os.environ.get("TP_RR", 2.0)))
                        else:
                            sl = entry + sl_atr_adj * 1.0
                            tp = entry - (sl_atr_adj * float(os.environ.get("TP_RR", 2.0)))
                        sl = round(sl, 8); tp = round(tp, 8)

                    order_side = "BUY"  if direction == "LONG"  else "SELL"
                    close_side = "SELL" if direction == "LONG"  else "BUY"

                    qty = calc_qty(sym, trade_usdt_adj, LEVERAGE, atr_mult)
                    if qty <= 0:
                        logger.warning(f"{sym} qty=0, skip")
                        continue

                    logger.info(
                        f"→ {direction} {sym} | "
                        f"HMM={regime_hmm} | "
                        f"entry={entry} sl={sl} tp={tp} qty={qty} usdt={trade_usdt_adj:.1f} | "
                        f"{reason}"
                    )

                    # Orden de mercado
                    res = bingx.place_market_order(sym, order_side, qty, direction, LEVERAGE)
                    if res.get("code") != 0:
                        err = res.get("msg", str(res))
                        logger.error(f"{sym} order error: {err}")
                        notifier.send(notifier.error_msg(sym, err))
                        continue

                    # TP + SL
                    try:
                        bingx.place_tp_sl(sym, close_side, direction, qty, tp, sl)
                    except Exception as e:
                        logger.warning(f"{sym} TP/SL error: {e}")

                    # Registro en pos_manager
                    pos_manager.register(sym, direction, entry, sl, tp, sl_atr_adj, qty)
                    if candles_c:
                        candles_cache[sym] = candles_c
                    scanner.set_cooldown(sym)

                    notifier.send(
                        notifier.entry_msg(
                            sym, direction, qty, entry, sl, tp,
                            f"{regime_s}→HMM:{regime_hmm}",
                            cvd, sweep, funding, balance, reason
                        )
                    )
                    open_symbols.add(sym)
                    entered += 1

            else:
                logger.info("Sin slots. Solo gestionando posiciones.")

            # ── Status periódico ──────────────────────────────────────────────
            if scan_count % STATUS_EVERY == 0:
                positions = bingx.get_open_positions()
                balance   = bingx.get_balance()
                regimes   = hmm_regime.active_regimes()

                # Añadir resumen de regímenes HMM al status
                regime_lines = "\n".join(
                    f"  {s}: {r}" for s, r in list(regimes.items())[:10]
                ) or "  (ninguno aún)"

                status = notifier.status_msg(positions, balance, scan_count, total_signals)
                status += f"\n\n🧬 <b>Regímenes HMM:</b>\n{regime_lines}"
                notifier.send(status)

        except KeyboardInterrupt:
            logger.info("Bot detenido por usuario.")
            notifier.send("🛑 Bot detenido manualmente.")
            break
        except Exception as e:
            logger.error(f"Loop error: {e}", exc_info=True)
            notifier.send(notifier.error_msg("main loop", str(e)))

        time.sleep(SCAN_EVERY)


if __name__ == "__main__":
    main()
