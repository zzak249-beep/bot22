#!/usr/bin/env python3
"""
learner.py v2.0 - Motor de aprendizaje basado en historial de trades
Mejora parametros por par segun performance real
"""

import json
from pathlib import Path
from datetime import datetime

STATE_DIR    = Path("bot_state")
LEARNER_FILE = STATE_DIR / "learner_state.json"


class Learner:
    def __init__(self):
        STATE_DIR.mkdir(exist_ok=True)
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(LEARNER_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"pairs": {}, "last_update": None}

    def _save(self):
        with open(LEARNER_FILE, "w") as f:
            json.dump(self.state, f, indent=2)

    def record_trade(self, symbol: str, side: str, pnl: float,
                     score: int, rsi: float, reason: str):
        """Registra el resultado de un trade para aprendizaje."""
        if symbol not in self.state["pairs"]:
            self.state["pairs"][symbol] = {
                "wins": 0, "losses": 0, "total_pnl": 0,
                "scores": [], "last_trade": None
            }
        p = self.state["pairs"][symbol]
        if pnl > 0:
            p["wins"] += 1
        else:
            p["losses"] += 1
        p["total_pnl"] = round(p.get("total_pnl", 0) + pnl, 6)
        p["scores"].append(score)
        p["scores"] = p["scores"][-50:]  # keep last 50
        p["last_trade"] = datetime.now().isoformat()
        self.state["last_update"] = datetime.now().isoformat()
        self._save()

    def get_config_for_pair(self, symbol: str) -> dict:
        """Retorna configuracion ajustada para un par segun historial."""
        p = self.state["pairs"].get(symbol)
        if not p:
            return {"score_min": None, "size_multiplier": 1.0, "skip": False}

        total = p["wins"] + p["losses"]
        if total < 5:
            return {"score_min": None, "size_multiplier": 1.0, "skip": False}

        wr = p["wins"] / total
        pnl = p.get("total_pnl", 0)

        # Ajustar multiplicador segun performance
        if wr >= 0.65 and pnl > 0:
            size_mult = 1.3
        elif wr >= 0.55 and pnl > 0:
            size_mult = 1.1
        elif wr < 0.35 or pnl < -5:
            size_mult = 0.7
        else:
            size_mult = 1.0

        # Bajar score_min si el par rinde bien
        score_min = None
        if wr >= 0.60:
            score_min = 15
        elif wr < 0.40 and total >= 10:
            score_min = 30

        skip = wr < 0.30 and total >= 10 and pnl < -10

        return {
            "score_min":       score_min,
            "size_multiplier": size_mult,
            "skip":            skip,
            "wr":              round(wr * 100, 1),
            "total":           total,
        }

    def get_top_pairs(self, n: int = 15) -> list:
        """Retorna los N mejores pares por PnL."""
        pairs = []
        for sym, p in self.state["pairs"].items():
            total = p["wins"] + p["losses"]
            if total >= 3:
                pairs.append((sym, p.get("total_pnl", 0)))
        pairs.sort(key=lambda x: x[1], reverse=True)
        return [p[0] for p in pairs[:n]]

    def get_bottom_pairs(self, n: int = 5) -> list:
        """Retorna los N peores pares (candidatos a blacklist)."""
        pairs = []
        for sym, p in self.state["pairs"].items():
            total = p["wins"] + p["losses"]
            if total >= 5:
                wr = p["wins"] / total
                pnl = p.get("total_pnl", 0)
                if wr < 0.35 and pnl < -5:
                    pairs.append((sym, pnl))
        pairs.sort(key=lambda x: x[1])
        return [p[0] for p in pairs[:n]]

    def update(self):
        """Actualiza el learner con trades recientes del paper file."""
        try:
            paper_file = Path("paper_trades.json")
            if not paper_file.exists():
                return
            with open(paper_file) as f:
                trades = json.load(f)
            # Procesar ultimos 100 trades
            for t in trades[-100:]:
                sym = t.get("symbol", "")
                if sym:
                    self.record_trade(
                        sym,
                        t.get("side", ""),
                        t.get("pnl", 0),
                        t.get("score", 0),
                        t.get("rsi_e", 50),
                        t.get("reason", ""),
                    )
        except Exception:
            pass
