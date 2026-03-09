#!/usr/bin/env python3
"""
selector.py v3.0 — Selector dinámico de pares
Elige qué pares operar basándose en performance histórica.
Rotación automática cada N horas.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List

STATE_DIR     = Path("bot_state")
SELECTOR_FILE = STATE_DIR / "selector_state.json"


def _load_selector_state():
    try:
        with open(SELECTOR_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "last_update":  None,
            "active_pairs": [],
            "blacklist":    [],
            "whitelist":    [],
            "last_rotation": None,
        }

def _save_selector_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    with open(SELECTOR_FILE, "w") as f:
        json.dump(state, f, indent=2)


class PairSelector:
    """Selecciona dinámicamente qué pares operar."""

    def __init__(self, learner=None):
        self.state   = _load_selector_state()
        self.learner = learner

    def _import_learner(self):
        if self.learner is None:
            try:
                from learner import Learner
                self.learner = Learner()
            except ImportError:
                return False
        return True

    def update_active_pairs(self, config_symbols: List[str], n_top=15) -> List[str]:
        """
        Actualizar lista de pares activos.
        Combina TOP pares por performance con pares neutrales para completar.
        """
        if not self._import_learner():
            # Sin learner: usar todos los pares de config
            self.state["active_pairs"] = config_symbols[:n_top]
            _save_selector_state(self.state)
            return config_symbols[:n_top]

        top_pairs    = self.learner.get_top_pairs(n_top)
        bottom_pairs = self.learner.get_bottom_pairs(5)

        active = []

        # 1) TOP pares que estén en config
        for pair in top_pairs:
            if pair in config_symbols and pair not in bottom_pairs:
                active.append(pair)

        # 2) Completar con pares neutrales si faltan
        if len(active) < n_top:
            for pair in config_symbols:
                if pair not in top_pairs and pair not in bottom_pairs:
                    if pair not in self.state.get("blacklist", []):
                        active.append(pair)
                        if len(active) >= n_top:
                            break

        active = active[:n_top]

        self.state["active_pairs"]  = active
        self.state["blacklist"]     = bottom_pairs
        self.state["last_update"]   = datetime.now().isoformat()
        self.state["last_rotation"] = datetime.now().isoformat()
        _save_selector_state(self.state)

        return active

    def should_trade_pair(self, symbol: str) -> bool:
        """Decide si operar un par."""
        if symbol in self.state.get("blacklist", []):
            return False

        whitelist = self.state.get("whitelist", [])
        if whitelist:
            return symbol in whitelist

        active = self.state.get("active_pairs", [])
        if active:
            return symbol in active

        return True   # Sin restricciones → operar

    def add_to_blacklist(self, symbol: str):
        if symbol not in self.state["blacklist"]:
            self.state["blacklist"].append(symbol)
            _save_selector_state(self.state)

    def remove_from_blacklist(self, symbol: str):
        if symbol in self.state["blacklist"]:
            self.state["blacklist"].remove(symbol)
            _save_selector_state(self.state)

    def set_whitelist(self, pairs: List[str]):
        self.state["whitelist"] = pairs
        _save_selector_state(self.state)

    def clear_whitelist(self):
        self.state["whitelist"] = []
        _save_selector_state(self.state)

    def get_summary(self) -> dict:
        return {
            "active_pairs":      self.state.get("active_pairs", []),
            "blacklist":         self.state.get("blacklist", []),
            "whitelist":         self.state.get("whitelist", []),
            "last_update":       self.state.get("last_update"),
            "total_active":      len(self.state.get("active_pairs", [])),
            "total_blacklisted": len(self.state.get("blacklist", [])),
        }
