"""Estado persistente (JSON atómico) y diario de operaciones (CSV)."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone

from strategy import Pos

JOURNAL_COLS = ["cerrada_utc", "symbol", "lado", "abierta_utc", "entrada", "salida", "motivo",
                "r_bruto", "coste_r", "r_neto", "barras", "score", "dist_htf", "er_pct", "rvol",
                "atr_pct", "mfe", "mae", "pnl_usdt", "modo", "slip_bps", "btc_reg",
                "pnl_real_usdt", "r_real"]


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


class State:
    def __init__(self, path: str):
        self.path = path
        self.positions: dict[str, Pos] = {}
        self.day = ""
        self.day_r = 0.0
        self.day_trades = 0
        self.day_wins = 0
        self.day_closed = 0
        self.paper_equity: float | None = None
        self.last_exit: dict[str, int] = {}
        self.blocked_notified = False
        self.leverage_set: list[str] = []
        self.paused = False
        self.pause_reason = ""
        self.cum_r = 0.0
        self.peak_r = 0.0
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path) as f:
            d = json.load(f)
        self.positions = {k: Pos.from_dict(v) for k, v in d.get("positions", {}).items()}
        self.day = d.get("day", "")
        self.day_r = float(d.get("day_r", 0))
        self.day_trades = int(d.get("day_trades", 0))
        self.day_wins = int(d.get("day_wins", 0))
        self.day_closed = int(d.get("day_closed", 0))
        self.paper_equity = d.get("paper_equity")
        self.last_exit = {k: int(v) for k, v in d.get("last_exit", {}).items()}
        self.blocked_notified = bool(d.get("blocked_notified", False))
        self.leverage_set = list(d.get("leverage_set", []))
        self.paused = bool(d.get("paused", False))
        self.pause_reason = d.get("pause_reason", "")
        self.cum_r = float(d.get("cum_r", 0))
        self.peak_r = float(d.get("peak_r", 0))

    def save(self):
        d = {
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "day": self.day, "day_r": self.day_r, "day_trades": self.day_trades, "day_wins": self.day_wins,
            "day_closed": self.day_closed, "paper_equity": self.paper_equity, "last_exit": self.last_exit,
            "blocked_notified": self.blocked_notified, "leverage_set": self.leverage_set,
            "paused": self.paused, "pause_reason": self.pause_reason,
            "cum_r": self.cum_r, "peak_r": self.peak_r,
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)


class Journal:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(JOURNAL_COLS)

    def write(self, row: dict):
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow([row.get(c, "") for c in JOURNAL_COLS])
