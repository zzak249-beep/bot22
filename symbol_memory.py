"""
symbol_memory.py — Sistema de Memoria Adaptativa v1.0

El bot aprende de cada trade y ajusta su comportamiento:
  • Símbolos con historial malo → cooldown más largo o excluidos
  • Símbolos con historial bueno → se priorizan en el scan
  • TP/SL se ajustan según la volatilidad histórica del símbolo
  • Se persiste en JSON para sobrevivir reinicios de Railway
"""

import json, os, time, logging
from datetime import datetime, timezone
from typing import Dict, Optional

log = logging.getLogger(__name__)

MEMORY_FILE = "/tmp/symbol_memory.json"
MAX_HISTORY = 20   # máx trades recordados por símbolo


class SymbolMemory:
    """
    Almacena y analiza el historial de trades por símbolo.
    Persiste entre reinicios del bot.
    """

    def __init__(self, filepath: str = MEMORY_FILE):
        self.filepath = filepath
        self.data: Dict[str, dict] = {}
        self._load()

    # ─────────────────── persistencia ────────────────────────

    def _load(self):
        try:
            with open(self.filepath) as f:
                self.data = json.load(f)
            log.info(f"  [MEMORY] Cargada: {len(self.data)} símbolos")
        except Exception:
            self.data = {}

    def _save(self):
        try:
            with open(self.filepath, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log.debug(f"memory save: {e}")

    # ─────────────────── registro de trades ──────────────────

    def record_trade(self, symbol: str, direction: str,
                     pnl_net: float, hold_minutes: float,
                     entry_price: float, exit_price: float,
                     tp_pct: float, sl_pct: float):
        """Registra el resultado de un trade cerrado."""
        if symbol not in self.data:
            self.data[symbol] = {
                "trades":       [],
                "total_pnl":    0.0,
                "wins":         0,
                "losses":       0,
                "avg_hold_min": 0.0,
                "last_updated": "",
            }

        s = self.data[symbol]
        win = pnl_net > 0

        # Añadir al historial (máx MAX_HISTORY)
        trade = {
            "ts":        datetime.now(timezone.utc).isoformat(),
            "dir":       direction,
            "pnl":       round(pnl_net, 4),
            "hold_min":  round(hold_minutes, 1),
            "entry":     round(entry_price, 6),
            "exit":      round(exit_price, 6),
            "tp_pct":    round(tp_pct, 2),
            "sl_pct":    round(sl_pct, 2),
            "win":       win,
        }
        s["trades"].append(trade)
        if len(s["trades"]) > MAX_HISTORY:
            s["trades"].pop(0)

        # Actualizar estadísticas
        s["total_pnl"]    = round(s["total_pnl"] + pnl_net, 4)
        if win:
            s["wins"]     += 1
        else:
            s["losses"]   += 1
        holds = [t["hold_min"] for t in s["trades"]]
        s["avg_hold_min"] = round(sum(holds) / len(holds), 1)
        s["last_updated"] = datetime.now(timezone.utc).isoformat()

        self._save()
        log.info(f"  [MEMORY] {symbol}: {'+' if win else '-'}${abs(pnl_net):.4f} | "
                 f"WR:{self.win_rate(symbol):.0f}% | Total:{s['total_pnl']:+.4f}")

    # ─────────────────── consultas ────────────────────────────

    def win_rate(self, symbol: str) -> float:
        """WR% del símbolo. 50.0 si sin historial."""
        s = self.data.get(symbol)
        if not s:
            return 50.0
        total = s["wins"] + s["losses"]
        return s["wins"] / total * 100 if total > 0 else 50.0

    def total_pnl(self, symbol: str) -> float:
        return self.data.get(symbol, {}).get("total_pnl", 0.0)

    def trade_count(self, symbol: str) -> int:
        s = self.data.get(symbol, {})
        return s.get("wins", 0) + s.get("losses", 0)

    def recent_losses(self, symbol: str, n: int = 3) -> int:
        """Número de pérdidas consecutivas recientes."""
        trades = self.data.get(symbol, {}).get("trades", [])
        if not trades:
            return 0
        streak = 0
        for t in reversed(trades[-n:]):
            if not t["win"]:
                streak += 1
            else:
                break
        return streak

    def avg_win_pnl(self, symbol: str) -> float:
        trades = self.data.get(symbol, {}).get("trades", [])
        wins   = [t["pnl"] for t in trades if t["win"]]
        return sum(wins) / len(wins) if wins else 0.0

    def avg_loss_pnl(self, symbol: str) -> float:
        trades = self.data.get(symbol, {}).get("trades", [])
        losses = [t["pnl"] for t in trades if not t["win"]]
        return sum(losses) / len(losses) if losses else 0.0

    def avg_hold_minutes(self, symbol: str) -> float:
        return self.data.get(symbol, {}).get("avg_hold_min", 0.0)

    # ─────────────────── decisiones adaptativas ──────────────

    def should_skip(self, symbol: str, min_wr: float = 30.0,
                    max_consec_losses: int = 3) -> tuple:
        """
        Decide si saltarse un símbolo basado en historial.
        Retorna (skip: bool, reason: str)
        """
        n = self.trade_count(symbol)
        if n < 3:
            return False, ""   # sin datos suficientes

        wr      = self.win_rate(symbol)
        pnl     = self.total_pnl(symbol)
        c_loss  = self.recent_losses(symbol, max_consec_losses)
        avg_win = self.avg_win_pnl(symbol)
        avg_los = self.avg_loss_pnl(symbol)

        # Pérdidas consecutivas
        if c_loss >= max_consec_losses:
            return True, f"SKIP:{symbol} {c_loss} pérd.consec."

        # WR muy bajo con suficientes trades
        if n >= 5 and wr < min_wr:
            return True, f"SKIP:{symbol} WR={wr:.0f}%<{min_wr:.0f}%"

        # PnL total muy negativo
        if n >= 4 and pnl < -2.0:
            return True, f"SKIP:{symbol} PnL=${pnl:.2f}"

        # Risk/reward desfavorable
        if n >= 5 and avg_win > 0 and avg_los < 0:
            rr = abs(avg_win / avg_los)
            if rr < 0.8:  # perdiendo más de lo que gana
                return True, f"SKIP:{symbol} RR={rr:.2f}"

        return False, ""

    def symbol_score(self, symbol: str) -> float:
        """
        Puntuación del símbolo basada en historial (0-100).
        Usado para priorizar los mejores símbolos.
        """
        n = self.trade_count(symbol)
        if n == 0:
            return 50.0   # neutro si sin historial

        wr      = self.win_rate(symbol)
        pnl     = self.total_pnl(symbol)
        c_loss  = self.recent_losses(symbol)

        score = 50.0

        # Win rate
        score += (wr - 50) * 0.5   # +/-25 puntos según WR

        # PnL total
        if pnl > 0:
            score += min(20, pnl * 10)
        else:
            score += max(-20, pnl * 10)

        # Pérdidas recientes penalizan
        score -= c_loss * 10

        # Con más trades, más fiable la estadística
        score += min(10, n * 0.5)

        return max(0.0, min(100.0, score))

    def get_tp_sl_adjustment(self, symbol: str, base_tp: float,
                             base_sl: float) -> tuple:
        """
        Ajusta TP/SL según el comportamiento histórico del símbolo.
        Si el símbolo suele alcanzar TP → mantener.
        Si suele tocar SL antes → ampliar SL ligeramente.
        """
        n = self.trade_count(symbol)
        if n < 4:
            return base_tp, base_sl

        trades    = self.data.get(symbol, {}).get("trades", [])
        tp_hits   = sum(1 for t in trades if t["win"] and t["pnl"] > 0.1)
        sl_hits   = sum(1 for t in trades if not t["win"] and t["pnl"] < -0.1)
        total_sig = tp_hits + sl_hits

        if total_sig == 0:
            return base_tp, base_sl

        sl_rate = sl_hits / total_sig

        # Si el SL se toca muy frecuentemente → ampliarlo un poco
        adj_tp, adj_sl = base_tp, base_sl
        if sl_rate > 0.6:
            adj_sl = round(base_sl * 1.2, 2)   # SL 20% más amplio
            adj_tp = round(base_tp * 1.3, 2)   # TP también más ambicioso
            log.debug(f"  [MEMORY] {symbol}: SL rate={sl_rate:.0%} → SL {base_sl}→{adj_sl}%")

        return adj_tp, adj_sl

    # ─────────────────── reporte ─────────────────────────────

    def summary(self, top_n: int = 5) -> str:
        """Resumen de los mejores y peores símbolos."""
        if not self.data:
            return "Sin historial aún"

        scored = []
        for sym, s in self.data.items():
            n = s["wins"] + s["losses"]
            if n < 2:
                continue
            scored.append({
                "symbol": sym,
                "n":      n,
                "wr":     self.win_rate(sym),
                "pnl":    s["total_pnl"],
                "score":  self.symbol_score(sym),
            })

        if not scored:
            return "Sin suficientes trades para analizar"

        scored.sort(key=lambda x: x["score"], reverse=True)
        total_syms   = len(scored)
        total_trades = sum(x["n"] for x in scored)
        total_pnl    = sum(x["pnl"] for x in scored)

        lines = [f"📊 Memoria: {total_syms} símbolos | {total_trades} trades | PnL:${total_pnl:+.4f}"]

        lines.append("🏆 Top:")
        for x in scored[:top_n]:
            lines.append(f"  {x['symbol']}: WR:{x['wr']:.0f}% PnL:${x['pnl']:+.4f} ({x['n']}t)")

        worst = sorted(scored, key=lambda x: x["score"])[:3]
        if worst:
            lines.append("🚫 Peores:")
            for x in worst:
                lines.append(f"  {x['symbol']}: WR:{x['wr']:.0f}% PnL:${x['pnl']:+.4f} ({x['n']}t)")

        return "\n".join(lines)

    def sort_symbols_by_score(self, symbols: list) -> list:
        """Ordena lista de símbolos: mejores primero."""
        def score(sym):
            return self.symbol_score(sym)
        return sorted(symbols, key=score, reverse=True)
