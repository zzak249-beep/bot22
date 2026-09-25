"""Configuración del bot desde variables de entorno (se limpian comillas)."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields

from strategy import env_val


@dataclass
class Cfg:
    bingx_api_key: str = ""
    bingx_api_secret: str = ""
    bingx_base: str = "https://open-api.bingx.com"
    dry_run: bool = True
    telegram_token: str = ""
    telegram_chat_id: str = ""
    bot_name: str = "EP5"
    symbols: str = ""                 # lista fija "BTC-USDT,ETH-USDT"; vacío = top por volumen
    top_n: int = 40
    min_quote_vol: float = 20_000_000.0
    blacklist: str = ""
    risk_pct: float = 0.1
    leverage: int = 5
    max_positions: int = 4
    max_per_side: int = 2
    max_trades_day: int = 12
    daily_max_loss_r: float = 4.0
    paper_equity: float = 1000.0
    slippage_bps: float = 3.0
    data_dir: str = "/data"
    kline_limit: int = 1000
    loop_delay_s: int = 6
    workers: int = 6
    heartbeat_h: int = 6
    max_dd_r: float = 10.0            # pausa si cae X R desde el máximo acumulado
    max_exposure_x: float = 3.0       # nocional abierto total ≤ X × equity
    funding_avoid_min: int = 15       # no abrir si faltan ≤ N min para funding en contra
    watchdog_min: int = 15            # reinicio si no completa un ciclo en N min

    @classmethod
    def from_env(cls) -> "Cfg":
        kw = {}
        for f in fields(cls):
            raw = os.getenv(f.name.upper())
            if raw is not None and raw.strip().strip('"').strip("'") != "":
                kw[f.name] = env_val(raw, type(f.default))
        c = cls(**kw)
        if not os.path.isdir(c.data_dir):
            try:
                os.makedirs(c.data_dir, exist_ok=True)
            except OSError:
                c.data_dir = "./data"
                os.makedirs(c.data_dir, exist_ok=True)
        return c

    def blacklist_set(self) -> set[str]:
        return {norm_symbol(s) for s in self.blacklist.split(",") if s.strip()}

    def symbols_list(self) -> list[str]:
        return [norm_symbol(s) for s in self.symbols.split(",") if s.strip()]


def norm_symbol(s: str) -> str:
    s = s.strip().upper().replace("/", "-").replace("_", "-")
    if "-" not in s and s.endswith("USDT"):
        s = s[:-4] + "-USDT"
    return s
