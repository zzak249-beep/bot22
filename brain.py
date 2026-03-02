"""
╔══════════════════════════════════════════════════════════════╗
║              BRAIN v3 — Sistema de Aprendizaje              ║
║  Aprende de cada trade: qué módulos, score, RSI, ADX,       ║
║  mercado BTC, hora UTC → ajusta pesos automáticamente       ║
║  Guarda historial en brain_data.json (persistente Railway)  ║
╚══════════════════════════════════════════════════════════════╝
"""
import os, json, time, logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime, timezone

log = logging.getLogger("brain")
BRAIN_FILE = os.environ.get("BRAIN_FILE", "brain_data.json")
MIN_SCORE_BASE = int(os.environ.get("MIN_SCORE", "5"))

# ── Cuántos trades mínimos para ajustar pesos ────────────
MIN_TRADES_ADJUST = 10
# ── Factor de aprendizaje (0.05 = 5% por trade) ──────────
LR = 0.05
# ── Penalización máxima de score (multiplicador) ─────────
MAX_PENALTY = 0.6
MIN_BOOST   = 1.5

@dataclass
class ComboStats:
    combo: str
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_score: float = 0.0
    weight: float = 1.0       # multiplicador de min_score
    last_updated: str = ""

    def wr(self):
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0

    def total(self):
        return self.wins + self.losses

@dataclass
class HourStats:
    hour: int
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0

    def wr(self):
        t = self.wins + self.losses
        return (self.wins / t * 100) if t else 0.0

@dataclass
class BrainData:
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_pnl: float = 0.0
    combos: Dict[str, dict] = field(default_factory=dict)
    hours: Dict[str, dict] = field(default_factory=dict)
    rsi_zones: Dict[str, dict] = field(default_factory=dict)   # "os","mid","ob"
    adx_zones: Dict[str, dict] = field(default_factory=dict)   # "weak","trend","strong"
    market_modes: Dict[str, dict] = field(default_factory=dict) # "bull","bear","lateral"
    error_log: List[dict] = field(default_factory=list)         # últimos 50 errores
    recent_trades: List[dict] = field(default_factory=list)     # últimos 100 trades

class Brain:
    def __init__(self):
        self.data = BrainData()
        self._load()

    # ── Persistencia ──────────────────────────────────────
    def _load(self):
        try:
            if os.path.exists(BRAIN_FILE):
                with open(BRAIN_FILE, "r") as f:
                    d = json.load(f)
                self.data = BrainData(**d)
                log.info(f"🧠 Brain cargado: {self.data.total_trades} trades históricos")
        except Exception as e:
            log.warning(f"Brain load error: {e} → empezando limpio")
            self.data = BrainData()

    def _save(self):
        try:
            with open(BRAIN_FILE, "w") as f:
                json.dump(asdict(self.data), f, indent=2)
        except Exception as e:
            log.warning(f"Brain save error: {e}")

    # ── Helpers de zona ───────────────────────────────────
    @staticmethod
    def _rsi_zone(rsi: float) -> str:
        if rsi < 35:   return "os"
        if rsi > 65:   return "ob"
        return "mid"

    @staticmethod
    def _adx_zone(adx: float) -> str:
        if adx < 20:   return "weak"
        if adx < 35:   return "trend"
        return "strong"

    @staticmethod
    def _market_mode(btc_bull: bool, btc_bear: bool) -> str:
        if btc_bull:   return "bull"
        if btc_bear:   return "bear"
        return "lateral"

    @staticmethod
    def _hour_now() -> int:
        return datetime.now(timezone.utc).hour

    def _zone_update(self, d: dict, key: str, win: bool, pnl: float):
        if key not in d:
            d[key] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
        d[key]["wins" if win else "losses"] += 1
        d[key]["total_pnl"] = round(d[key]["total_pnl"] + pnl, 4)

    # ── Registrar trade cerrado ───────────────────────────
    def on_trade_closed(self, trade, pnl: float, reason: str,
                        btc_bull: bool, btc_bear: bool, btc_adx: float,
                        rsi: float, adx: float):
        win = pnl > 0
        combo = trade.modules
        hour = self._hour_now()
        rz = self._rsi_zone(rsi)
        az = self._adx_zone(adx)
        mm = self._market_mode(btc_bull, btc_bear)

        # ── Totales ───────────────────────────────────────
        self.data.total_trades += 1
        if win: self.data.total_wins += 1
        else:   self.data.total_losses += 1
        self.data.total_pnl = round(self.data.total_pnl + pnl, 4)

        # ── Combo stats ───────────────────────────────────
        if combo not in self.data.combos:
            self.data.combos[combo] = asdict(ComboStats(combo=combo))
        cs = self.data.combos[combo]
        cs["wins" if win else "losses"] += 1
        cs["total_pnl"] = round(cs["total_pnl"] + pnl, 4)
        cs["avg_score"] = round(
            cs["avg_score"] * 0.9 + trade.score * 0.1, 2)
        cs["last_updated"] = datetime.now(timezone.utc).isoformat()

        # ── Ajustar peso del combo ────────────────────────
        total_c = cs["wins"] + cs["losses"]
        if total_c >= MIN_TRADES_ADJUST:
            wr = cs["wins"] / total_c
            # peso sube si WR>55%, baja si WR<45%
            if wr > 0.55:
                cs["weight"] = min(cs["weight"] + LR, MIN_BOOST)
            elif wr < 0.45:
                cs["weight"] = max(cs["weight"] - LR, MAX_PENALTY)
            # si pierde 3 seguidas → penalizar más fuerte
            recent = [t for t in self.data.recent_trades[-10:]
                      if t.get("combo") == combo]
            if len(recent) >= 3 and all(not t["win"] for t in recent[-3:]):
                cs["weight"] = max(cs["weight"] - LR * 2, MAX_PENALTY)
                log.warning(f"🧠 Combo {combo} penalizado (3 pérdidas seguidas)")

        # ── Zonas ─────────────────────────────────────────
        self._zone_update(self.data.hours, str(hour), win, pnl)
        self._zone_update(self.data.rsi_zones, rz, win, pnl)
        self._zone_update(self.data.adx_zones, az, win, pnl)
        self._zone_update(self.data.market_modes, mm, win, pnl)

        # ── Log de errores (pérdidas > 1 ATR) ────────────
        if not win:
            err = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": trade.symbol,
                "side": trade.side,
                "combo": combo,
                "score": trade.score,
                "rsi": round(rsi, 1),
                "adx": round(adx, 1),
                "market": mm,
                "hour": hour,
                "reason": reason,
                "pnl": round(pnl, 4),
                "lesson": self._lesson(reason, mm, rz, az)
            }
            self.data.error_log = (self.data.error_log + [err])[-50:]
            log.info(f"🧠 Error registrado: {err['lesson']}")

        # ── Historial reciente ─────────────────────────────
        self.data.recent_trades = (self.data.recent_trades + [{
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": trade.symbol,
            "side": trade.side,
            "combo": combo,
            "score": trade.score,
            "win": win,
            "pnl": round(pnl, 4),
            "reason": reason,
            "market": mm,
            "rsi_zone": rz,
            "adx_zone": az,
            "hour": hour
        }])[-100:]

        self._save()

    @staticmethod
    def _lesson(reason: str, market: str, rsi_z: str, adx_z: str) -> str:
        lessons = []
        if "SL" in reason or "PÉRDIDA" in reason:
            if adx_z == "weak": lessons.append("evitar ADX<20")
            if market == "lateral": lessons.append("mercado lateral: reducir tamaño")
            if rsi_z == "mid": lessons.append("RSI en zona media: señal débil")
        if "FLIP" in reason:
            lessons.append("reversión rápida: usar SL más ajustado")
        if "TRAIL" in reason and "ULTRA" not in reason:
            lessons.append("trailing muy agresivo: ajustar multiplicador")
        return "; ".join(lessons) if lessons else "revisar contexto de mercado"

    # ── Consulta: ¿entrar? ────────────────────────────────
    def check_entry(self, score: int, combo: str, size: float):
        """
        Devuelve (can_enter, adjusted_size, reason)
        """
        eff = self.get_effective_min_score(combo)
        if score < eff:
            return False, size, f"score {score}<{eff} (combo penalizado)"

        # Reducir tamaño si combo tiene mal historial reciente
        cs = self.data.combos.get(combo, {})
        weight = cs.get("weight", 1.0)
        if weight < 0.8:
            adj_size = round(size * weight, 2)
            return True, adj_size, f"tamaño reducido x{weight:.2f}"

        # Hora mala → reducir tamaño (si >10 trades en esa hora y WR<40%)
        hour = str(self._hour_now())
        h = self.data.hours.get(hour, {})
        htot = h.get("wins", 0) + h.get("losses", 0)
        if htot >= 10:
            hwr = h.get("wins", 0) / htot
            if hwr < 0.40:
                return True, round(size * 0.6, 2), f"hora UTC:{hour} WR:{hwr*100:.0f}%<40%"

        return True, size, ""

    def get_effective_min_score(self, combo: str) -> int:
        cs = self.data.combos.get(combo, {})
        weight = cs.get("weight", 1.0)
        # peso<1 → subir umbral (más exigente), peso>1 → bajar umbral
        adjusted = MIN_SCORE_BASE / weight
        return max(3, min(12, round(adjusted)))

    # ── Resumen Telegram ──────────────────────────────────
    def telegram_summary_line(self) -> str:
        d = self.data
        wr = (d.total_wins / d.total_trades * 100) if d.total_trades else 0
        return (f"🧠 Brain: {d.total_trades}T "
                f"{d.total_wins}W/{d.total_losses}L "
                f"WR:{wr:.1f}% PnL:${d.total_pnl:+.2f}")

    def telegram_report(self) -> str:
        d = self.data
        wr = (d.total_wins / d.total_trades * 100) if d.total_trades else 0
        lines = [
            f"🧠 <b>BRAIN REPORT v3</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📊 {d.total_trades} trades | WR:{wr:.1f}% | ${d.total_pnl:+.2f}",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🏆 Top combos:",
        ]
        combos_sorted = sorted(
            d.combos.items(),
            key=lambda x: x[1].get("total_pnl", 0), reverse=True
        )[:5]
        for name, cs in combos_sorted:
            tot = cs["wins"] + cs["losses"]
            cwr = (cs["wins"] / tot * 100) if tot else 0
            w = cs.get("weight", 1.0)
            lines.append(f"  {'🟢' if w>=1 else '🔴'} {name[:30]}: "
                         f"{cs['wins']}W/{cs['losses']}L WR:{cwr:.0f}% "
                         f"w:{w:.2f} ${cs['total_pnl']:+.2f}")

        # Zonas RSI
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📉 RSI zones:")
        for z, stats in d.rsi_zones.items():
            tot = stats["wins"] + stats["losses"]
            zwr = (stats["wins"] / tot * 100) if tot else 0
            lines.append(f"  {z}: {tot}T WR:{zwr:.0f}% ${stats['total_pnl']:+.2f}")

        # Modo mercado
        lines.append(f"🌐 Mercado:")
        for m, stats in d.market_modes.items():
            tot = stats["wins"] + stats["losses"]
            mwr = (stats["wins"] / tot * 100) if tot else 0
            lines.append(f"  {m}: {tot}T WR:{mwr:.0f}% ${stats['total_pnl']:+.2f}")

        # Últimos errores
        if d.error_log:
            lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append(f"❌ Últimos errores:")
            for e in d.error_log[-3:]:
                lines.append(f"  {e['symbol']} {e['side']} [{e['combo'][:20]}]")
                lines.append(f"    → {e['lesson']}")

        return "\n".join(lines)

    def score_distribution_bar(self) -> str:
        if self.data.total_trades < 5:
            return ""
        buckets = {}
        for t in self.data.recent_trades:
            sc = t.get("score", 0)
            b = f"{(sc//2)*2}-{(sc//2)*2+1}"
            if b not in buckets:
                buckets[b] = {"w": 0, "l": 0}
            if t["win"]: buckets[b]["w"] += 1
            else:        buckets[b]["l"] += 1
        lines = ["Score distribution (recent):"]
        for b in sorted(buckets):
            s = buckets[b]
            tot = s["w"] + s["l"]
            bar = "█" * s["w"] + "░" * s["l"]
            wr = s["w"] / tot * 100 if tot else 0
            lines.append(f"  {b}: {bar} WR:{wr:.0f}%")
        return "\n".join(lines)

# ── Instancia global ──────────────────────────────────────
brain = Brain()

def on_trade_closed(trade, pnl, reason, btc_bull, btc_bear, btc_adx, rsi, adx):
    brain.on_trade_closed(trade, pnl, reason, btc_bull, btc_bear, btc_adx, rsi, adx)

def check_entry(score, mods, size):
    return brain.check_entry(score, mods, size)
