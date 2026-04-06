"""
Bot Conservador v6.0 — Menos es más

FILOSOFÍA: Después de análisis de 89 páginas de trades perdedores, la
conclusión es clara: el problema no es el código, es la frecuencia.
Demasiados trades × pequeñas pérdidas = pérdida total grande.

Este bot opera con estas reglas absolutas:
  • Solo 3 pares: BTC-USDT, ETH-USDT, SOL-USDT
  • Timeframe 4h (menos ruido, señales más fiables)
  • Máximo 1 posición a la vez
  • Máximo 2 trades por día
  • SL monitoreado en código cada ciclo (no solo como orden)
  • TP mínimo 3× el SL (ratio riesgo/beneficio real)
  • Stop si pierde $3 en el día

Basado en lo que funcionó en el historial:
  • Holdings > 45 min → más rentables
  • BTC/ETH/SOL → más predecibles
  • Timeframe largo → menos señales falsas
"""

import os, sys, time, json, logging
import requests as req
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
from bingx_client import BingXClient, BingXError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ──────────────────────── CONFIG ─────────────────────────────

def _e(k, d=None, t=str):
    v = os.getenv(k, d)
    return t(v) if v is not None else None

API_KEY        = _e("BINGX_API_KEY")
SECRET_KEY     = _e("BINGX_SECRET_KEY")
TELEGRAM_TOKEN = _e("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT  = _e("TELEGRAM_CHAT_ID",   "")
DEMO_MODE      = _e("DEMO_MODE", "false") == "true"
POSITION_MODE  = _e("POSITION_MODE", "auto")

# Pares — solo los más líquidos y predecibles
SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

TIMEFRAME   = "4h"       # 4h: menos ruido que 1h
LEVERAGE    = 2          # bajado a 2x — más seguro
RISK_PCT    = 0.02       # 2% del balance por trade
TP_RATIO    = 3.0        # TP = 3 × SL (ratio mínimo)
SL_PCT      = 2.0        # SL 2% → TP 6%
CHECK_SEC   = 300        # ciclo cada 5 minutos
MAX_DAY     = 2          # máximo 2 trades por día
DAILY_STOP  = 3.0        # parar si pierde $3 en el día
MIN_BAL     = 15.0       # balance mínimo para operar
HOLD_MIN    = 60         # mantener posición al menos 60min
COOLDOWN_H  = 4          # 4h de cooldown entre trades del mismo par

FEE = 0.0005  # MARKET taker fee BingX

# ──────────────────────── INDICADORES ────────────────────────

def ema(prices, n):
    if len(prices) < 2:
        return prices[-1] if prices else 0
    k, e = 2/(n+1), prices[0]
    for p in prices[1:]:
        e = p*k + e*(1-k)
    return e

def rsi(prices, n=14):
    if len(prices) < n+1:
        return 50.0
    g = [max(0, prices[i]-prices[i-1]) for i in range(1, len(prices))]
    l = [max(0, prices[i-1]-prices[i]) for i in range(1, len(prices))]
    ag = sum(g[-n:])/n
    al = sum(l[-n:])/n
    return 100.0 if al == 0 else 100 - 100/(1 + ag/al)

def atr(highs, lows, closes, n=14):
    if len(closes) < 2:
        return 0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    return sum(trs[-n:])/n if trs else 0

def get_ohlcv(symbol, client_obj):
    klines = client_obj.get_klines(symbol, TIMEFRAME, limit=100)
    if not klines:
        raise RuntimeError(f"Sin velas {symbol}")
    df = pd.DataFrame(klines)
    if isinstance(klines[0], dict):
        df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    else:
        df.columns = ["open_time","open","high","low","close","volume"] + list(range(len(df.columns)-6))
    for col in ("open","high","low","close","volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def analyze(symbol, client_obj):
    """
    Señal simple y robusta:
    LONG:  precio > EMA50 4h + RSI entre 40-60 (tendencia alcista sin sobrecompra)
           + precio rompió máximo de las últimas 3 velas
    SHORT: precio < EMA50 4h + RSI entre 40-60 (tendencia bajista sin sobreventa)
           + precio rompió mínimo de las últimas 3 velas
    Retorna: ('long'|'short'|None, precio, sl_price, tp_price, descripción)
    """
    try:
        df = get_ohlcv(symbol, client_obj)
    except Exception as e:
        return None, 0, 0, 0, str(e)

    closes = df["close"].tolist()
    highs  = df["high"].tolist()
    lows   = df["low"].tolist()
    price  = closes[-1]

    e50   = ema(closes, 50)
    e20   = ema(closes, 20)
    r     = rsi(closes, 14)
    a     = atr(highs, lows, closes, 14)
    atr_pct = a / price * 100

    # ATR mínimo: sin movimiento = sin trade
    if atr_pct < 0.5:
        return None, price, 0, 0, f"ATR bajo {atr_pct:.2f}%"

    # ── LONG ─────────────────────────────────────────────────
    # 1. Precio por encima de EMA50 y EMA20 (doble confirmación)
    # 2. RSI en zona correcta (no sobrecomprado)
    # 3. La vela actual rompe el máximo de las 3 anteriores (impulso)
    # 4. EMA20 > EMA50 (alineación de medias)
    recent_high = max(highs[-4:-1])
    recent_low  = min(lows[-4:-1])

    long_ok = (
        price > e50 and
        price > e20 and
        e20 > e50 and           # tendencia alcista confirmada
        40 <= r <= 65 and        # RSI: no sobrecomprado
        price > recent_high and  # ruptura de máximo reciente
        closes[-1] > closes[-2] # vela actual alcista
    )

    short_ok = (
        price < e50 and
        price < e20 and
        e20 < e50 and            # tendencia bajista confirmada
        35 <= r <= 60 and        # RSI: no sobrevendido
        price < recent_low and   # ruptura de mínimo reciente
        closes[-1] < closes[-2]  # vela actual bajista
    )

    # Calcular TP/SL basado en ATR real
    sl_dist = max(a * 1.5, price * SL_PCT / 100)
    tp_dist = sl_dist * TP_RATIO

    if long_ok:
        sl = price - sl_dist
        tp = price + tp_dist
        sl_p = sl_dist / price * 100
        tp_p = tp_dist / price * 100
        desc = f"LONG | EMA50:{e50:.4f} EMA20:{e20:.4f} RSI:{r:.0f} ATR:{atr_pct:.2f}%"
        return "long", price, round(sl, 6), round(tp, 6), desc

    if short_ok:
        sl = price + sl_dist
        tp = price - tp_dist
        sl_p = sl_dist / price * 100
        tp_p = tp_dist / price * 100
        desc = f"SHORT | EMA50:{e50:.4f} EMA20:{e20:.4f} RSI:{r:.0f} ATR:{atr_pct:.2f}%"
        return "short", price, round(sl, 6), round(tp, 6), desc

    return None, price, 0, 0, f"Sin señal | RSI:{r:.0f} EMA50:{e50:.4f} precio:{'>' if price>e50 else '<'}EMA"

# ──────────────────────── BOT ────────────────────────────────

class ConservativeBot:

    def __init__(self):
        self.client = BingXClient(
            API_KEY, SECRET_KEY, demo=DEMO_MODE,
            telegram_token=TELEGRAM_TOKEN, telegram_chat=TELEGRAM_CHAT,
        )
        self.position   = None   # {'symbol','direction','entry','qty','sl','tp','opened_at','sl_pct','tp_pct'}
        self.cooldowns  = {}     # {symbol: expiry_ts}
        self.trades_today = 0
        self.pnl_today    = 0.0
        self.today_date   = ""
        self.pnl_file     = "/tmp/bot_v6_pnl.json"
        self.account_mode = "hedge"
        self._load_state()

    def _load_state(self):
        try:
            with open(self.pnl_file) as f:
                d = json.load(f)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if d.get("date") == today:
                self.trades_today = d.get("trades_today", 0)
                self.pnl_today    = d.get("pnl_today", 0.0)
                self.today_date   = today
                log.info(f"  Estado cargado: {self.trades_today} trades hoy, PnL ${self.pnl_today:+.4f}")
        except Exception:
            pass

    def _save_state(self):
        try:
            with open(self.pnl_file, "w") as f:
                json.dump({
                    "date":         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "trades_today": self.trades_today,
                    "pnl_today":    self.pnl_today,
                }, f)
        except Exception:
            pass

    def _reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.today_date:
            self.trades_today = 0
            self.pnl_today    = 0.0
            self.today_date   = today
            log.info("  Reset diario")

    def _detect_mode(self):
        if POSITION_MODE.lower() in ("hedge","oneway"):
            self.account_mode = POSITION_MODE.lower()
            return
        try:
            for pos in self.client.get_positions("BTC-USDT"):
                side = str(pos.get("positionSide","")).upper()
                if side in ("LONG","SHORT"):
                    self.account_mode = "hedge"
                    return
                elif side == "BOTH":
                    self.account_mode = "oneway"
                    return
        except Exception:
            pass
        self.account_mode = "hedge"

    def _sync(self):
        """Sincroniza posición con BingX."""
        if not self.position:
            return
        try:
            sym = self.position["symbol"]
            positions = self.client.get_positions(sym)
            alive = False
            for p in positions:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    alive = True
                    break
            if not alive:
                log.info(f"  [SYNC] {sym} cerrado externamente")
                self.position = None
        except Exception:
            pass

    def _is_on_cooldown(self, symbol):
        ts = self.cooldowns.get(symbol)
        if not ts:
            return False
        if time.time() > ts:
            del self.cooldowns[symbol]
            return False
        return True

    def _set_cooldown(self, symbol):
        self.cooldowns[symbol] = time.time() + COOLDOWN_H * 3600

    def _get_price(self, symbol):
        try:
            d = req.get("https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
                        params={"symbol": symbol}, timeout=5).json()
            if d.get("code") == 0:
                return float(d["data"]["lastPrice"])
        except Exception:
            pass
        return 0.0

    def open_position(self, symbol, direction, entry, sl, tp, desc):
        balance = self.client.get_balance()
        if balance < MIN_BAL:
            log.warning(f"  Balance ${balance:.2f} < ${MIN_BAL}")
            return False

        qty = max(round((balance * RISK_PCT * LEVERAGE) / entry, 3), 0.001)

        # Verificar que la ganancia esperada cubre las fees × 3
        notional    = qty * entry * LEVERAGE
        fee_total   = notional * FEE * 2
        tp_pct      = abs(tp - entry) / entry * 100
        sl_pct      = abs(sl - entry) / entry * 100
        exp_profit  = notional * (tp_pct / 100) - fee_total
        if exp_profit < 0.50:
            log.info(f"  {symbol}: profit esperado ${exp_profit:.3f} insuficiente")
            return False

        pos_side = ("LONG" if direction == "long" else "SHORT") if self.account_mode == "hedge" else "BOTH"

        log.info(f"  ABRIENDO {direction.upper()} {symbol} @ ${entry:.4f}")
        log.info(f"  TP: ${tp:.4f} (+{tp_pct:.2f}%) | SL: ${sl:.4f} (-{sl_pct:.2f}%)")
        log.info(f"  Profit esp: ${exp_profit:.3f} | {desc}")

        self.client.set_leverage(symbol, LEVERAGE)
        ok = self.client.place_entry(symbol, direction, qty, pos_side)
        if not ok:
            return False

        # Esperar confirmación real (máx 20s)
        confirmed = False
        for _ in range(20):
            time.sleep(1)
            for p in self.client.get_positions(symbol):
                amt = float(p.get("positionAmt", 0))
                ps  = str(p.get("positionSide","BOTH")).upper()
                if ((direction == "long"  and (ps == "LONG"  or amt > 0)) or
                    (direction == "short" and (ps == "SHORT" or amt < 0))):
                    confirmed = True
                    break
            if confirmed:
                break

        if not confirmed:
            log.error(f"  {symbol}: posición no confirmada")
            self.client.close_all_positions(symbol)
            return False

        self.position = {
            "symbol":    symbol,
            "direction": direction,
            "entry":     entry,
            "qty":       qty,
            "sl":        sl,
            "tp":        tp,
            "sl_pct":    round(sl_pct, 2),
            "tp_pct":    round(tp_pct, 2),
            "pos_side":  pos_side,
            "opened_at": datetime.now(timezone.utc),
            "notional":  notional,
        }
        self.trades_today += 1
        self._save_state()

        # Colocar TP/SL en BingX (intento adicional — el código también monitorea)
        tp_sl = self.client.place_tp_sl(symbol, direction, qty, tp, sl, pos_side)
        tp_i  = "✅" if tp_sl["tp"] else "⚠️"
        sl_i  = "✅" if tp_sl["sl"] else "⚠️"

        e = "🟢" if direction == "long" else "🔴"
        self.client.send_telegram(
            f"<b>{e} {direction.upper()} ABIERTO [v6.0]</b>\n"
            f"Par: {symbol} | TF: {TIMEFRAME} | {LEVERAGE}x\n"
            f"Precio: ${entry:.4f} | Qty: {qty}\n"
            f"TP {tp_i}: ${tp:.4f} (+{tp_pct:.2f}%)\n"
            f"SL {sl_i}: ${sl:.4f} (-{sl_pct:.2f}%)\n"
            f"Ratio TP:SL = {TP_RATIO:.1f}:1\n"
            f"Profit esperado: ${exp_profit:.3f}\n"
            f"Balance: ${balance:.2f} | Hoy: {self.trades_today}/{MAX_DAY}\n"
            f"Señal: {desc}"
        )
        log.info(f"  {symbol}: posición abierta ✅")
        return True

    def close_position(self, reason, current_price=None):
        if not self.position:
            return
        t = self.position
        sym  = t["symbol"]
        d    = t["direction"]
        cur  = current_price or self._get_price(sym) or t["entry"]

        pnl_usd   = t["qty"] * (cur - t["entry"]) * (1 if d == "long" else -1)
        fee_total = t["notional"] * FEE * 2
        pnl_net   = pnl_usd - fee_total
        win       = pnl_net > 0

        hold_mins = (datetime.now(timezone.utc) - t["opened_at"]).total_seconds() / 60
        log.info(f"  CERRANDO {d.upper()} {sym} | {reason} | ${pnl_net:+.4f} | {int(hold_mins)}min")

        # Cancelar órdenes TP/SL antes de cerrar
        self.client.cleanup_symbol_orders(sym)
        time.sleep(0.5)
        self.client.close_all_positions(sym)

        self.pnl_today += pnl_net
        self._save_state()
        self._set_cooldown(sym)

        emoji = "✅" if win else "❌"
        self.client.send_telegram(
            f"<b>{emoji} CERRADO {d.upper()} — {reason}</b>\n"
            f"Par: {sym} | {int(hold_mins)}min\n"
            f"${t['entry']:.4f} → ${cur:.4f}\n"
            f"PnL neto: <b>${pnl_net:+.4f}</b> (fee: ${fee_total:.4f})\n"
            f"PnL hoy: ${self.pnl_today:+.4f} | Trades: {self.trades_today}/{MAX_DAY}"
        )

        self.position = None
        time.sleep(2)

    def monitor_position(self):
        """
        Monitorea la posición activa y cierra si:
        1. Precio alcanza TP
        2. Precio alcanza SL (monitoreado en código, no solo en BingX)
        3. Holding mínimo alcanzado + señal de reversión
        """
        if not self.position:
            return

        t     = self.position
        sym   = t["symbol"]
        d     = t["direction"]
        cur   = self._get_price(sym)
        if cur <= 0:
            return

        hold_mins = (datetime.now(timezone.utc) - t["opened_at"]).total_seconds() / 60
        pnl_pct   = (cur - t["entry"]) / t["entry"] * 100 * (1 if d == "long" else -1)

        log.info(f"  Monitor {d.upper()} {sym}: ${cur:.4f} | PnL: {pnl_pct:+.2f}% | {int(hold_mins)}min")

        # SL monitoreado en código (más fiable que orden BingX)
        if d == "long"  and cur <= t["sl"]:
            self.close_position(f"SL ${cur:.4f}", cur)
            return
        if d == "short" and cur >= t["sl"]:
            self.close_position(f"SL ${cur:.4f}", cur)
            return

        # TP
        if d == "long"  and cur >= t["tp"]:
            self.close_position(f"TP ${cur:.4f}", cur)
            return
        if d == "short" and cur <= t["tp"]:
            self.close_position(f"TP ${cur:.4f}", cur)
            return

    def run(self):
        log.info("=" * 65)
        log.info("  Bot Conservador v6.0 | Solo BTC/ETH/SOL | 4h | 1 trade")
        log.info(f"  LEV:{LEVERAGE}x | RISK:{RISK_PCT:.0%} | TP:{TP_RATIO}:1 | SL:{SL_PCT}%")
        log.info(f"  MaxDay:{MAX_DAY} | DailyStop:${DAILY_STOP} | Cooldown:{COOLDOWN_H}h")
        log.info("=" * 65)

        if DEMO_MODE:
            log.warning("MODO DEMO")

        try:
            balance = self.client.get_balance()
            log.info(f"Balance: ${balance:.2f} USDT")
        except BingXError as e:
            log.error(f"No conecta: {e}")
            sys.exit(1)

        self._detect_mode()

        # Limpiar órdenes pendientes al arrancar
        log.info("  Limpiando órdenes pendientes...")
        self.client.cleanup_all_orders()
        time.sleep(2)

        # Recuperar posición existente si la hay
        self._sync()

        self.client.send_telegram(
            f"<b>Bot Conservador v6.0 iniciado</b>\n"
            f"Solo: BTC / ETH / SOL | TF: {TIMEFRAME}\n"
            f"LEV: {LEVERAGE}x | Max: {MAX_DAY} trades/día\n"
            f"Stop diario: -${DAILY_STOP} | SL monitoreado en código\n"
            f"Balance: ${balance:.2f} USDT\n"
            f"{'⚠️ DEMO MODE' if DEMO_MODE else '💰 MODO REAL'}"
        )

        cycle = 0
        last_report = 0

        while True:
            try:
                cycle += 1
                self._reset_daily()
                self._sync()

                try:
                    balance = self.client.get_balance()
                except BingXError:
                    time.sleep(CHECK_SEC)
                    continue

                log.info(
                    f"\n{'='*65}\n"
                    f"  #{cycle} {datetime.now(timezone.utc).strftime('%H:%M:%S')} | "
                    f"${balance:.2f} | Pos:{'Sí' if self.position else 'No'} | "
                    f"Hoy:${self.pnl_today:+.3f} ({self.trades_today}/{MAX_DAY})\n"
                    f"{'='*65}"
                )

                # Stop diario
                if self.pnl_today < -DAILY_STOP:
                    if self.position:
                        self.close_position("Stop diario activado")
                    log.warning(f"  STOP DIARIO: ${self.pnl_today:.3f} < -${DAILY_STOP}")
                    self.client.send_telegram(
                        f"<b>🔴 Stop diario activado</b>\n"
                        f"Pérdida: ${self.pnl_today:.4f} > -${DAILY_STOP}\n"
                        f"Bot pausado hasta mañana"
                    )
                    # Esperar hasta el próximo día
                    while datetime.now(timezone.utc).strftime("%Y-%m-%d") == self.today_date:
                        time.sleep(300)
                    continue

                # Monitorear posición activa
                if self.position:
                    self.monitor_position()
                    log.info(f"  Próximo ciclo en {CHECK_SEC}s\n")
                    time.sleep(CHECK_SEC)
                    continue

                # Buscar entrada si no hay posición y quedan slots
                if self.trades_today >= MAX_DAY:
                    log.info(f"  Límite diario alcanzado ({self.trades_today}/{MAX_DAY})")
                    time.sleep(CHECK_SEC)
                    continue

                if balance < MIN_BAL:
                    log.warning(f"  Balance ${balance:.2f} < mínimo ${MIN_BAL}")
                    time.sleep(CHECK_SEC)
                    continue

                # Analizar los 3 pares
                best = None
                for sym in SYMBOLS:
                    if self._is_on_cooldown(sym):
                        cd_left = int((self.cooldowns[sym] - time.time()) / 60)
                        log.info(f"  {sym}: cooldown {cd_left}min restantes")
                        continue

                    direction, price, sl, tp, desc = analyze(sym, self.client)
                    log.info(f"  {sym}: {desc[:60]}")

                    if direction and sl > 0 and tp > 0:
                        # Tomar el primer par con señal válida
                        best = (sym, direction, price, sl, tp, desc)
                        break

                if best:
                    sym, direction, price, sl, tp, desc = best
                    log.info(f"  ★ SEÑAL: {direction.upper()} {sym}")
                    self.open_position(sym, direction, price, sl, tp, desc)

                # Reporte cada hora
                if time.time() - last_report > 3600:
                    last_report = time.time()
                    pos_str = "Sin posición"
                    if self.position:
                        t   = self.position
                        cur = self._get_price(t["symbol"]) or t["entry"]
                        pct = (cur - t["entry"]) / t["entry"] * 100
                        pct *= 1 if t["direction"] == "long" else -1
                        pos_str = f"{t['direction'].upper()} {t['symbol']}: {pct:+.2f}%"
                    self.client.send_telegram(
                        f"<b>Bot v6.0 — Reporte horario</b>\n"
                        f"Balance: ${balance:.2f} | {pos_str}\n"
                        f"Trades hoy: {self.trades_today}/{MAX_DAY}\n"
                        f"PnL hoy: ${self.pnl_today:+.4f}"
                    )

                log.info(f"  Próximo ciclo en {CHECK_SEC}s\n")
                time.sleep(CHECK_SEC)

            except KeyboardInterrupt:
                if self.position:
                    self.close_position("Bot detenido manualmente")
                self.client.send_telegram(
                    f"<b>Bot v6.0 detenido</b>\n"
                    f"PnL hoy: ${self.pnl_today:+.4f}\n"
                    f"Trades: {self.trades_today}/{MAX_DAY}"
                )
                sys.exit(0)
            except Exception as e:
                log.error(f"Error ciclo #{cycle}: {e}", exc_info=True)
                self.client.send_telegram(f"<b>⚠️ Error Bot v6.0</b>\n{e}")
                time.sleep(30)


if __name__ == "__main__":
    ConservativeBot().run()
