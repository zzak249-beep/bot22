"""
PARCHE para analizar.py v5.5 — Integración modo mercado lateral
================================================================

Añadir estas líneas en analizar.py:

1) AL INICIO del archivo, tras los imports existentes:
─────────────────────────────────────────────────────
try:
    from analizar_lateral import detectar_rango, señal_en_rango
    _LATERAL_OK = True
except ImportError:
    _LATERAL_OK = False
    log.warning("[LATERAL] analizar_lateral.py no encontrado — modo rango desactivado")

2) DENTRO de analizar_par(), ANTES del bloque "if lado is None:" final,
   añadir este bloque (tras el cálculo de rr):
─────────────────────────────────────────────────────

        # ── MODO MERCADO LATERAL — si no hay señal tendencial ──────────────
        if lado is None and _LATERAL_OK and config.RANGE_ACTIVO:
            rango = detectar_rango(candles, lookback=30)
            if rango.get("es_rango"):
                log.info(
                    f"[RANGO] {par} — ADX={rango['adx']:.1f} amp={rango['amplitud_pct']:.1f}% "
                    f"{'cerca_low' if rango['cerca_low'] else 'cerca_high' if rango['cerca_high'] else 'mitad'}"
                )
                señal_r = señal_en_rango(
                    par, candles, rango, rsi,
                    pat_long, pat_short,
                    sl_long, sl_short,
                )
                if señal_r and señal_r["score"] >= config.RANGE_SCORE_MIN:
                    # Completar la señal con todos los campos necesarios
                    señal_r.update({
                        "kz":          kz["nombre"],
                        "htf":         htf,
                        "htf_4h":      htf_4h,
                        "vwap":        round(vwap, 8),
                        "sobre_vwap":  sobre_vwap,
                        "fvg_top":     fvg.get("fvg_top", 0),
                        "fvg_bottom":  fvg.get("fvg_bottom", 0),
                        "fvg_rellenado": fvg.get("fvg_rellenado", True),
                        "ob_bull":     ob["bull_ob"],
                        "ob_bear":     ob["bear_ob"],
                        "ob_fvg_bull": False,
                        "ob_fvg_bear": False,
                        "ob_mitigado": False,
                        "bos_bull":    bos["bos_bull"],
                        "bos_bear":    bos["bos_bear"],
                        "choch_bull":  bos["choch_bull"],
                        "choch_bear":  bos["choch_bear"],
                        "sweep_bull":  sweep["sweep_bull"],
                        "sweep_bear":  sweep["sweep_bear"],
                        "patron":      pat_long["patron"] if señal_r["lado"] == "LONG" else pat_short["patron"],
                        "vela_conf":   False,
                        "premium":     pd_zone["premium"],
                        "discount":    pd_zone["discount"],
                        "zona_pct":    pd_zone["zona_pct"],
                        "displacement": False,
                        "inducement":  False,
                        "pivotes":     pivotes,
                        "macd_hist":   macd_hist,
                        "vol_ratio":   round(vol_ratio, 2),
                        "asia_valido": asia["valido"],
                        "atr":         round(atr_sl if 'atr_sl' in dir() else atr, 8),
                        "rsi":         rsi,
                    })
                    registrar_senal_ts(par)
                    return señal_r

3) En volumen_ok(), añadir soporte para bajo volumen:
─────────────────────────────────────────────────────
Reemplazar la función volumen_ok() existente con esta versión:

def volumen_ok(candles: list) -> bool:
    if not candles:
        return False
    # Volumen promedio de las últimas 20 velas (evitar picos)
    vols = [c["volume"] for c in candles[-20:]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    # Último precio para calcular volumen en USDT aproximado
    precio = candles[-1]["close"]
    vol_usdt = avg_vol * precio
    
    # Umbral normal
    if vol_usdt >= config.VOLUMEN_MIN_24H:
        return True
    
    # Umbral bajo volumen (si está activo)
    if config.LOW_VOL_ACTIVO and vol_usdt >= config.VOLUMEN_MIN_LOW_VOL:
        return True  # Se filtrará más tarde por SCORE_MIN_LOW_VOL
    
    return False

NOTA: Esta función evalúa el volumen por vela, no en 24h. 
La lógica de 24h ya está en scanner_pares.py.
Lo que aquí importa es que las velas tengan suficiente actividad.

═══════════════════════════════════════════════════════════════
Variables de Railway a cambiar (configuración recomendada v5.5)
═══════════════════════════════════════════════════════════════

SCORE_MIN=8              (antes era 6)
MAX_POSICIONES=3         (antes era 5)
METACLAW_VETO_MINIMO=5   (antes era 7)
TIME_EXIT_HORAS=16       (antes era 8)
RANGE_ACTIVO=true        (NUEVO — mercado lateral)
RANGE_ADX_MAX=22         (NUEVO)
RANGE_SCORE_MIN=7        (NUEVO)
LOW_VOL_ACTIVO=true      (NUEVO — monedas bajo volumen)
VOLUMEN_MIN_LOW_VOL=50000 (NUEVO — 50k USDT mínimo)
SCORE_MIN_LOW_VOL=10     (NUEVO — mayor filtro para bajo volumen)
"""
