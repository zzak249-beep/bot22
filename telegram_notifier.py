import os
import requests
import threading

# ══════════════════════════════════════════════════════
# telegram_notifier.py v12.4
# ══════════════════════════════════════════════════════

_offset = 0

def _token(): return os.getenv("TELEGRAM_TOKEN", "")
def _chat():  return str(os.getenv("TELEGRAM_CHAT_ID", ""))

def _send(text: str) -> bool:
    token = _token(); chat = _chat()
    if not token or not chat:
        print(f"[TG-NO-CONFIG] {text[:100]}")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=15,
        )
        if not r.json().get("ok"):
            print(f"[TG-ERR] {r.json()}")
            return False
        return True
    except Exception as e:
        print(f"[TG-EXCEPTION] {e}")
        return False


# ── Mensajes ───────────────────────────────────────────

def notify_start(version, symbols, mode, balance):
    e = "🟢" if mode == "live" else "🟡"
    funds = f"💰 Balance: ${balance:.2f}" if balance > 0 else "⚠️ Sin fondos"
    _send(
        f"{e} BOT INICIADO {version}\n"
        f"Modo: {mode.upper()}  {funds}\n"
        f"Pares: {len(symbols)}\n"
        f"✅ Trailing SL  ✅ Multi-TF 4h\n"
        f"✅ Circuit breaker  ✅ Sizing ATR\n"
        f"✅ Re-entry  ✅ Filtro volumen\n"
        f"Comandos: /status /positions /balance /pause /resume /close"
    )

def notify_signal(sym, side, score, rsi, price, sl, tp, trend, executed, balance, bias_4h="?"):
    arrow  = "📈 LONG" if side == "long" else "📉 SHORT"
    status = "✅ ORDEN ABIERTA" if executed else "🔔 SEÑAL (sin fondos)"
    _send(
        f"{status} {arrow}\n"
        f"Par: {sym}\n"
        f"Precio: {price:.6g}  SL: {sl:.6g}  TP: {tp:.6g}\n"
        f"RSI: {rsi:.1f}  Score: {score}\n"
        f"1h: {trend}  |  4h: {bias_4h}\n"
        f"Balance: ${balance:.2f}"
    )

def notify_close(sym, side, entry, exit_p, pnl, reason, balance):
    e = "✅ WIN" if pnl >= 0 else "❌ LOSS"
    _send(
        f"{e}  {side.upper()} {sym}\n"
        f"Entrada: {entry:.6g} → Salida: {exit_p:.6g}\n"
        f"PnL: ${pnl:+.4f}  ({reason})\n"
        f"Balance: ${balance:.2f}"
    )

def notify_partial_tp(sym, side, price, balance):
    _send(f"🎯 PARTIAL TP  {sym} {side.upper()}\nPrecio: {price:.6g}  SL → breakeven\nBalance: ${balance:.2f}")

def notify_no_funds(sym, side, score, rsi, price, sl, tp):
    arrow = "📈 LONG" if side == "long" else "📉 SHORT"
    _send(f"💡 SEÑAL SIN FONDOS {arrow}\n{sym}  Precio: {price:.6g}\nSL: {sl:.6g}  TP: {tp:.6g}\nRSI: {rsi:.1f}  Score: {score}")

def notify_circuit_breaker(reason):
    _send(f"🚨 CIRCUIT BREAKER\n{reason}\nUsa /resume para reactivar")

def notify_reentry(sym, side, score):
    _send(f"🔁 RE-ENTRY {'📈' if side=='long' else '📉'} {sym}  Score: {score}")

def notify_error(msg):
    _send(f"🚨 ERROR\n{str(msg)[:350]}")

def notify_heartbeat(version, cycle, balance, open_pos, mode, stats):
    e = "🟢" if mode == "live" else "🟡"
    _send(
        f"{e} HEARTBEAT {version} #{cycle}\n"
        f"Balance: ${balance:.2f}  Posiciones: {open_pos}\n"
        f"Drawdown: {stats.get('drawdown_pct',0):.1f}%  "
        f"Pérd.consec: {stats.get('consecutive_losses',0)}\n"
        f"PnL hoy: ${stats.get('daily_pnl',0):+.4f}"
    )


# ── Comandos Telegram ──────────────────────────────────

def _handle_command(text: str):
    try:
        import trader, risk_manager as rm
        cmd = text.strip().lower().split()[0]
        if cmd == "/status":
            bal   = trader.get_balance()
            pos   = trader.get_positions()
            summ  = trader.get_summary()
            stats = rm.get_stats(bal)
            _send(
                f"📊 STATUS\n"
                f"Balance: ${bal:.2f}\n"
                f"Posiciones: {len(pos)}\n"
                f"Drawdown: {stats['drawdown_pct']:.1f}%  "
                f"Pérd.consec: {stats['consecutive_losses']}\n"
                f"Trades: {summ.get('total',0)}  "
                f"WR: {summ.get('wr',0)}%  "
                f"PF: {summ.get('pf',0)}\n"
                f"PnL total: ${summ.get('pnl',0):+.4f}\n"
                f"PnL hoy: ${stats['daily_pnl']:+.4f}\n"
                f"Pausado: {'SI — '+stats['pause_reason'] if stats['paused'] else 'No'}"
            )
        elif cmd == "/balance":
            _send(f"💰 Balance: ${trader.get_balance():.2f}")
        elif cmd == "/positions":
            pos = trader.get_positions()
            if not pos:
                _send("📭 Sin posiciones abiertas")
            else:
                lines = ["📋 POSICIONES ABIERTAS"]
                for sym, p in pos.items():
                    lines.append(f"{'📈' if p['side']=='long' else '📉'} {sym} {p['side'].upper()}\n   E:{p['entry']:.6g}  SL:{p['sl']:.5g}  TP:{p['tp']:.5g}")
                _send("\n".join(lines))
        elif cmd == "/pause":
            rm.pause("manual via Telegram")
            _send("⏸️ Bot pausado. /resume para reactivar.")
        elif cmd == "/resume":
            rm.resume()
            _send("▶️ Bot reactivado.")
        elif cmd == "/close":
            parts = text.strip().split()
            if len(parts) < 2:
                _send("Uso: /close SYMBOL\nEj: /close LINK-USDT")
                return
            sym = parts[1].upper()
            pos = trader.get_positions()
            if sym not in pos:
                _send(f"Sin posición abierta para {sym}")
            else:
                import bingx_api as api
                p = pos[sym]
                price = api.get_price(sym) if os.getenv("TRADE_MODE","paper") == "live" else p["entry"]
                trader._execute_close(sym, p, price, "MANUAL")
                _send(f"✅ {sym} cerrado manualmente")
        elif cmd == "/help":
            _send(
                "📖 COMANDOS\n"
                "/status — resumen\n"
                "/balance — balance\n"
                "/positions — posiciones\n"
                "/pause — pausar\n"
                "/resume — reactivar\n"
                "/close SYMBOL — cerrar posición"
            )
        else:
            _send(f"Comando desconocido: {cmd}\n/help para ayuda")
    except Exception as e:
        _send(f"Error en comando: {e}")


def _poll_loop():
    global _offset
    while True:
        try:
            token = _token(); chat = _chat()
            if not token or not chat:
                time.sleep(30); continue
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": _offset, "timeout": 30},
                timeout=35,
            )
            for upd in r.json().get("result", []):
                _offset = upd["update_id"] + 1
                msg  = upd.get("message", {})
                text = msg.get("text", "")
                cid  = str(msg.get("chat", {}).get("id", ""))
                if text.startswith("/") and cid == chat:
                    _handle_command(text)
        except Exception:
            pass
        import time; time.sleep(1)


def start_command_listener():
    import time
    if not _token():
        print("  [TG] ⚠️  Sin TELEGRAM_TOKEN — sin notificaciones")
        return
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    print(f"  [TG] ✅ Listener activo (chat: {_chat()})")
