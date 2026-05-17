"""
learning_engine.py — V35 FINAL
FIX: _apply_hard_limits corregido (min/max tenía lógica redundante)
FIX: Parámetros persisten correctamente entre reinicios
"""
import json, logging, os
from datetime import datetime, timezone
from typing import Tuple
from config import LEARNING_FILE, MIN_TRADES_TO_LEARN, ADX_MIN, VOL_MULT, SYMBOL_BLACKLIST_WR, DATA_DIR

logger = logging.getLogger(__name__)

ADX_HARD_MIN = float(ADX_MIN)
ADX_HARD_MAX = 25.0
STR_HARD_MIN = 30.0
STR_HARD_MAX = 55.0


class LearningEngine:
    def __init__(self, telegram=None):
        self.telegram    = telegram
        os.makedirs(DATA_DIR, exist_ok=True)
        self.trades: list      = self._load()
        self.adjustments: list = []

        # FIX: inicializar con valores base correctos
        self.params = {
            "adx_min":      ADX_HARD_MIN,
            "min_strength": 35.0,
            "vol_mult":     float(VOL_MULT),
        }
        self._apply_hard_limits()

    def _apply_hard_limits(self):
        """FIX: lógica correcta de límites."""
        self.params["adx_min"]      = max(ADX_HARD_MIN, min(ADX_HARD_MAX, self.params["adx_min"]))
        self.params["min_strength"] = max(STR_HARD_MIN, min(STR_HARD_MAX, self.params["min_strength"]))

    def _load(self) -> list:
        try:
            if os.path.exists(LEARNING_FILE):
                with open(LEARNING_FILE) as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Load trades: {e}")
        return []

    def _save(self):
        try:
            with open(LEARNING_FILE, "w") as f:
                json.dump(self.trades, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Save trades: {e}")

    def record(self, symbol: str, signal: dict, outcome: dict):
        trade = {
            "id":           len(self.trades) + 1,
            "ts":           datetime.now(timezone.utc).isoformat(),
            "symbol":       symbol,
            "direction":    signal.get("signal"),
            "entry":        signal.get("entry"),
            "sl":           signal.get("sl"),
            "tp":           signal.get("tp"),
            "adx":          signal.get("adx"),
            "atr_pct":      signal.get("atr_pct"),
            "strength":     signal.get("strength"),
            "vol_ratio":    signal.get("vol_ratio"),
            "pnl":          outcome.get("pnl", 0.0),
            "reason":       outcome.get("reason", "unknown"),
            "duration_min": outcome.get("duration_min", 0),
            "won":          outcome.get("pnl", 0.0) > 0,
        }
        self.trades.append(trade)
        self._save()
        logger.info(f"Trade #{trade['id']} {symbol} {trade['direction']} "
                    f"pnl={trade['pnl']:.4f}")
        if len(self.trades) % 5 == 0:
            self._learn()

    def _learn(self):
        if len(self.trades) < MIN_TRADES_TO_LEARN:
            return

        recent  = self.trades[-20:]
        wins    = [t for t in recent if t["won"]]
        winrate = len(wins) / len(recent) * 100

        logger.info(f"Learning: WR={winrate:.1f}% ({len(wins)}/{len(recent)}) | {self.params}")

        old = dict(self.params)
        reason = None

        if winrate < 30 and len(recent) >= 15:
            self.params["adx_min"]      = ADX_HARD_MIN
            self.params["min_strength"] = 35.0
            reason = f"WR crítico {winrate:.0f}% → RESET"
        elif winrate < 40 and len(recent) >= 10:
            self.params["adx_min"]      = min(ADX_HARD_MAX, self.params["adx_min"] + 1.0)
            self.params["min_strength"] = min(STR_HARD_MAX, self.params["min_strength"] + 2.0)
            reason = f"WR {winrate:.0f}% → filtros +1"
        elif winrate > 60 and len(recent) >= 10:
            self.params["adx_min"]      = max(ADX_HARD_MIN, self.params["adx_min"] - 1.0)
            self.params["min_strength"] = max(STR_HARD_MIN, self.params["min_strength"] - 2.0)
            reason = f"WR {winrate:.0f}% → relajando"

        self._apply_hard_limits()

        if reason and old != self.params:
            self.adjustments.append({"ts": datetime.now(timezone.utc).isoformat(),
                                     "reason": reason, "old": old, "new": dict(self.params)})
            logger.info(f"Params: {old} → {self.params}")
            if self.telegram:
                try: self.telegram.notify_learning_update(old, self.params, reason)
                except: pass

    def should_take(self, signal: dict) -> Tuple[bool, str]:
        adx = signal.get("adx", 0) or 0
        strength = signal.get("strength", 0) or 0
        if adx < self.params["adx_min"]:
            return False, f"ADX {adx:.1f} < {self.params['adx_min']}"
        if strength < self.params["min_strength"]:
            return False, f"Fuerza {strength:.1f} < {self.params['min_strength']}"
        return True, "OK"

    def is_blacklisted(self, symbol: str) -> bool:
        sym_t = [t for t in self.trades if t["symbol"] == symbol]
        if len(sym_t) < 5:
            return False
        wr = sum(1 for t in sym_t if t["won"]) / len(sym_t) * 100
        if wr < SYMBOL_BLACKLIST_WR:
            logger.info(f"Blacklist: {symbol} WR={wr:.0f}% {len(sym_t)}tr")
            if self.telegram:
                try: self.telegram.notify_blacklist(symbol, wr, len(sym_t))
                except: pass
            return True
        return False

    def get_stats(self, today_only: bool = True) -> dict:
        trades = self.trades
        if today_only:
            today  = datetime.now(timezone.utc).date().isoformat()
            trades = [t for t in trades if t["ts"].startswith(today)]
        if not trades:
            return {"total":0,"wins":0,"losses":0,"winrate":0.0,
                    "total_pnl":0.0,"learning_notes":"Sin trades aún"}
        wins   = [t for t in trades if t["won"]]
        pnl    = sum(t.get("pnl",0) for t in trades)
        wr     = len(wins)/len(trades)*100
        return {
            "total":   len(trades),
            "wins":    len(wins),
            "losses":  len(trades)-len(wins),
            "winrate": round(wr,1),
            "total_pnl": round(pnl,4),
            "learning_notes":
                f"ADX>={self.params['adx_min']:.0f} "
                f"Fuerza>={self.params['min_strength']:.0f}% "
                f"({len(self.adjustments)} ajustes)",
        }

    def total_trades(self) -> int:
        return len(self.trades)
