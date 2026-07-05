"""
Scanner — recorre todos los símbolos perpetuos de BingX (filtrados por
liquidez y excluyendo no-cripto), descarga velas multi-timeframe y
evalúa la señal combinada Supertrend + Unicorn Model.
"""
import asyncio
import logging

from combined_engine import evaluate_symbol
from order_flow import confirm_with_order_flow
from funding_oi_filter import confirm_with_funding_oi

log = logging.getLogger("scanner")


def _is_valid_symbol(symbol, non_crypto_prefixes):
    base = symbol.split("-")[0] if "-" in symbol else symbol.replace("USDT", "")
    return not any(base.startswith(p) for p in non_crypto_prefixes)


async def get_symbol_universe(client, config):
    """
    Lista de símbolos filtrados por tipo (cripto) y, opcionalmente, por
    liquidez. Con SCAN_ALL_SYMBOLS=True (default) se analizan TODAS las
    monedas de BingX, sin filtro de volumen — solo se sigue excluyendo lo
    que no es cripto (forex/índices/commodities que BingX a veces lista).
    """
    all_symbols = await client.get_all_symbols_with_volume()
    scan_all = getattr(config, "SCAN_ALL_SYMBOLS", True)
    min_vol = 0 if scan_all else config.MIN_24H_VOLUME_USDT
    filtered = [
        s["symbol"] for s in all_symbols
        if s["volume_24h_usdt"] >= min_vol
        and _is_valid_symbol(s["symbol"], config.NON_CRYPTO_PREFIXES)
    ]
    log.info(
        "Universo de símbolos tras filtro: %d (SCAN_ALL_SYMBOLS=%s, min_vol=%s)",
        len(filtered), scan_all, min_vol,
    )
    return filtered


async def _evaluate_one(client, symbol, config, semaphore):
    async with semaphore:
        try:
            candles_entry = await client.get_klines(symbol, config.ENTRY_TF, limit=150)
            candles_bias = await client.get_klines(symbol, config.BIAS_TF, limit=120)
            candles_1h = await client.get_klines(symbol, config.HTF_C_TF, limit=60)

            ob_tf = getattr(config, "OB_TF", "15m")
            ob_engine_on = getattr(config, "ENABLE_OB_ENGINE", True)

            if ob_engine_on and ob_tf == config.HTF_A_TF:
                # OB_TF coincide con HTF_A_TF (default de ambos: "15m") -> una sola
                # llamada de klines sirve para las dos cosas. El Order Block Engine
                # necesita más histórico (limit=250) que el Unicorn Model para su
                # warm-up (ST_LEN + pivotes), así que se pide con ese límite mayor
                # y ese mismo set alcanza de sobra para los niveles de liquidez del
                # Unicorn Model (que solo mira las últimas ~22 velas).
                candles_15m = await client.get_klines(symbol, config.HTF_A_TF, limit=250)
                candles_ob = candles_15m
            else:
                candles_15m = await client.get_klines(symbol, config.HTF_A_TF, limit=60)
                candles_ob = await client.get_klines(symbol, ob_tf, limit=250) if ob_engine_on else None

            candles_30m = await client.get_klines(symbol, config.HTF_B_TF, limit=60)

            if len(candles_entry) < 80 or len(candles_bias) < 55:
                return None

            sig = evaluate_symbol(
                symbol, candles_entry, candles_bias, candles_1h,
                config, candles_15m, candles_30m, candles_ob,
            )

            # Cascada de confirmaciones finales: solo se consulta la API cuando
            # ya hay señal válida, para no gastar rate limit en 500+ símbolos.
            if sig.get("signal") is not None:
                sig = await confirm_with_order_flow(client, symbol, sig, config)
            if sig.get("signal") is not None:
                sig = await confirm_with_funding_oi(client, symbol, sig, config)

            return sig
        except Exception as e:
            log.error("Error evaluando %s: %s", symbol, e)
            return None


async def scan_universe(client, symbols, config):
    """Evalúa todos los símbolos con concurrencia acotada. Devuelve señales válidas."""
    semaphore = asyncio.Semaphore(config.SCAN_CONCURRENCY)
    tasks = [_evaluate_one(client, s, config, semaphore) for s in symbols]
    results = await asyncio.gather(*tasks)

    signals = [r for r in results if r and r.get("signal")]
    log.info("Ciclo de scan completo: %d símbolos evaluados, %d señales válidas",
              len(symbols), len(signals))
    return signals
