"""
crowding_bot.py — bot de SEÑALES del posicionamiento amontonado. v3.0

NO OPERA. NO PIDE CLAVES DE API. Solo endpoints públicos de BingX.

═══════════════════════════════════════════════════════════════════════
POR QUÉ ESTA VERSIÓN: "lleva días sin dar una señal" y no se sabía por qué
═══════════════════════════════════════════════════════════════════════
El bot tenía la respuesta y no la enseñaba. Cuatro defectos que se
tapaban entre sí:

1. SE PERDÍAN MUESTRAS SIN QUE NADA AVISARA. `_apuntar()` vivía DENTRO
   de `evaluar()`, después de tres salidas tempranas ("pocas velas",
   "sin premiumIndex", "sin open interest"). Y en el bucle, un símbolo
   con virtual abierta hacía `continue` ANTES de llamar a `evaluar()`.
   Resultado: cada fallo de descarga y cada virtual abierta (hasta 4 h)
   dejaba un HUECO en la historia de ese símbolo. El calentamiento exige
   200 muestras; los símbolos con más huecos tardaban el doble o no
   llegaban. Ahora el muestreo va PRIMERO y es incondicional: si hay
   dato, se apunta, pase lo que pase después.

2. EL PANEL TAMBIÉN ESTABA VACÍO. `acc.append()` iba después del return
   de "calentando", así que mientras algún símbolo calentaba no entraba
   en el panel. Sin señales Y sin panel no hay nada que analizar.

3. EL EMBUDO NO DECÍA LO QUE HACÍA FALTA. Contaba cuántos morían en cada
   puerta, pero no A QUÉ DISTANCIA. "sin amontonamiento: 300" no
   distingue "el mercado está plano" de "el umbral es inalcanzable".
   Ahora hay AUTOPSIA: con cero señales, el log imprime el percentil 90,
   99 y el máximo de cada variable frente a su umbral, y dice si el
   umbral está fuera del alcance del mercado de hoy.

4. LA VENTANA DEL Z CRECÍA HASTA 168 h. Medido por simulación: con
   regímenes de volatilidad la tasa de disparo cae del 4,1% al 3,0% al
   pasar de 30 h a 300 h de ventana, porque los tramos agitados engordan
   la desviación típica. Es real pero secundario — no explica un cero.
   Aun así ahora Z_WIN_HORAS es independiente de HIST_HORAS.

Y una salida estructural: SELECCION=transversal. En vez de "z absoluto
>= 2", coge el X% más extremo del universo EN CADA INSTANTÁNEA. Siempre
existe un peor 2%, así que siempre hay candidatos y la tasa de señales
deja de depender de la volatilidad general del mercado. AVISO HONESTO:
tener candidatos siempre NO es tener ventaja siempre. Es una forma de
medir con muestra estable, no una mejora del resultado.

═══════════════════════════════════════════════════════════════════════
CUÁNTA MUESTRA HACE FALTA
═══════════════════════════════════════════════════════════════════════
Con desviación típica de 1R por operación:

    ventaja 0.50 R/op  ->    31 operaciones
    ventaja 0.30 R/op  ->    87 operaciones
    ventaja 0.20 R/op  ->   196 operaciones
    ventaja 0.10 R/op  ->   784 operaciones

Convención: si en la misma vela se tocan stop y objetivo, manda el STOP.
"""
from __future__ import annotations

import bisect
import csv
import json
import logging
import math
import os
import statistics
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import confirm as cf
import panel as pn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("crowding")

_ultimo_ciclo = 0.0
_ultimo_heartbeat = 0.0

BASE = "https://open-api.bingx.com"
UA = {"User-Agent": "crowding-signal-bot/3.0"}

SESSION = requests.Session()
SESSION.headers.update(UA)
_adapter = HTTPAdapter(
    pool_connections=40,
    pool_maxsize=40,
    max_retries=Retry(total=2, backoff_factor=0.3,
                      status_forcelist=[429, 500, 502, 503, 504]),
)
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)


def env(k, d):
    v = os.getenv(k)
    if v is None:
        return d
    try:
        if isinstance(d, bool):
            return str(v).strip().lower() in ("1", "true", "yes", "si", "sí", "on")
        if isinstance(d, int):
            return int(float(v))
        if isinstance(d, float):
            return float(v)
    except ValueError:
        log.warning("%s='%s' ilegible, se usa %r", k, v, d)
        return d
    return v


CFG = {
    "TIMEFRAME": env("TIMEFRAME", "15m"),
    "SCAN_SEC": env("SCAN_SEC", 120),
    "MIN_VOL_24H": env("MIN_VOL_24H", 2_000_000.0),
    "MAX_SYMBOLS": env("MAX_SYMBOLS", 300),
    # EN HORAS, NO EN MUESTRAS: la duración del ciclo depende de cuántos
    # símbolos escanee, así que un contador de muestras cambiaría de
    # significado al tocar MAX_SYMBOLS.
    "HIST_HORAS": env("HIST_HORAS", 168.0),    # retención
    # VENTANA DEL Z, separada de la retención. Antes el z se calculaba
    # contra TODA la historia guardada, así que se iba estrechando solo:
    # al crecer la ventana entran tramos de volatilidad alta, la sd sube
    # y el mismo movimiento deja de ser 2 sigma. Medido: 4.1% -> 3.0% de
    # tasa de disparo al pasar de 30 h a 300 h. 0 = usar toda la historia
    # (comportamiento anterior, para comparar).
    "Z_WIN_HORAS": env("Z_WIN_HORAS", 48.0),
    "MIN_HORAS": env("MIN_HORAS", 30.0),
    "MIN_MUESTRAS": env("MIN_MUESTRAS", 200),
    "OI_LOOK_H": env("OI_LOOK_H", 6.0),

    # ── Selección ──────────────────────────────────────────────────
    # "absoluto"   : z >= Z_BASIS contra la propia historia del símbolo.
    #                La tasa de señales depende de la volatilidad general
    #                del mercado: en calma, cero.
    # "transversal": el XS_TOP_PCT% más extremo del universo en ESTA
    #                instantánea. Siempre hay candidatos por construcción.
    #                Eso arregla la MUESTRA, no la ventaja.
    "SELECCION": env("SELECCION", "absoluto"),
    "XS_TOP_PCT": env("XS_TOP_PCT", 2.0),
    # Suelo de decencia en modo transversal: ser el más extremo de un
    # universo plano no es estar amontonado.
    "XS_Z_MIN": env("XS_Z_MIN", 1.0),

    "Z_BASIS": env("Z_BASIS", 2.0),
    "Z_OI": env("Z_OI", 1.0),
    "EXT_PCT": env("EXT_PCT", 80.0),
    "ATR_LEN": env("ATR_LEN", 14),
    "SL_ATR": env("SL_ATR", 1.5),
    "TP_R": env("TP_R", 2.0),
    "MAX_BARS": env("MAX_BARS", 16),
    "MIN_ATR_PCT": env("MIN_ATR_PCT", 0.0),
    "MAX_ATR_PCT": env("MAX_ATR_PCT", 8.0),
    "COST_PCT": env("COST_PCT", 0.25),
    "MAX_COST_R": env("MAX_COST_R", 0.20),

    # La vela de disparo. velas[-1] es la vela EN CURSO: evaluarla hace
    # que una señal aparezca y desaparezca dentro del mismo bar, y que
    # la 'entrada' apuntada no sea reproducible. Con true se exige que
    # la vela en contra esté CERRADA. Menos señales y todas repetibles.
    "VELA_CERRADA": env("VELA_CERRADA", True),

    "STATE": env("STATE", "/data/crowding_state.json"),
    "CSV": env("CSV", "/data/crowding_ops.csv"),
    "TG_TOKEN": env("TG_TOKEN", ""),
    "TG_CHAT": env("TG_CHAT", ""),
    "REPORT_HOUR": env("REPORT_HOUR", 7),
    "TG_SIGNALS": env("TG_SIGNALS", False),
    "TG_CLOSES": env("TG_CLOSES", False),
    "CSV_CON_INFORME": env("CSV_CON_INFORME", True),
    "CSV_AL_ARRANCAR": env("CSV_AL_ARRANCAR", False),
    # Aviso por Telegram cuando el bot lleva N horas sin emitir NADA.
    # El silencio económico no da error en los logs: hay que vigilarlo.
    "MUDO_ALERTA_H": env("MUDO_ALERTA_H", 12.0),
    "PACING": env("PACING", 0.0),
    "MAX_WORKERS": env("MAX_WORKERS", 20),
    "MUESTRA_MIN_SEG": env("MUESTRA_MIN_SEG", 300.0),
    "MAX_PUNTOS": env("MAX_PUNTOS", 2200),
    "KLINES_LIMIT": env("KLINES_LIMIT", 260),
    "PANEL_ENABLED": env("PANEL_ENABLED", True),
    "PANEL_CSV": env("PANEL_CSV", "/data/crowding_panel.csv"),
    "PANEL_CADA_SEG": env("PANEL_CADA_SEG", 900.0),
    "PANEL_MIN_SIMBOLOS": env("PANEL_MIN_SIMBOLOS", 30),
    "CONFIRM_ENABLED": env("CONFIRM_ENABLED", True),
    "CONFIRM_BLOQUEAR": False,
    "CONFIRM_Q": env("CONFIRM_Q", 8),
    "CONFIRM_WIN": env("CONFIRM_WIN", 240),
    "CONFIRM_LAMBDA": env("CONFIRM_LAMBDA", 0.985),
    "CONFIRM_Z": env("CONFIRM_Z", 1.5),
    "CONFIRM_MIN_VELAS": env("CONFIRM_MIN_VELAS", 120),
}

TF_MS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}
BAR_SEC = TF_MS.get(str(CFG["TIMEFRAME"]), 900)


# ─────────────────────────────────────────────────────── API pública
def _get(path: str, params: dict | None = None, intentos: int = 3):
    for i in range(intentos):
        try:
            r = SESSION.get(BASE + path, params=params or {}, timeout=12)
            r.raise_for_status()
            j = r.json()
            if str(j.get("code", 0)) not in ("0", "None"):
                log.debug("%s devolvió code=%s", path, j.get("code"))
                return None
            return j.get("data")
        except Exception as e:
            if i == intentos - 1:
                log.debug("%s falló: %s", path, e)
            time.sleep(0.4 * (i + 1) + 0.1 * (i + 1) ** 2)
    return None


def contratos() -> list[str]:
    d = _get("/openApi/swap/v2/quote/contracts")
    if not d:
        return []
    out = []
    for c in d:
        s = c.get("symbol", "")
        if s.endswith("-USDT") and str(c.get("status", 1)) in ("1", "True", "true"):
            out.append(s)
    return out


def volumenes() -> dict:
    d = _get("/openApi/swap/v2/quote/ticker")
    if not d:
        return {}
    out = {}
    for t in d:
        try:
            out[t.get("symbol")] = float(t.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            pass
    return out


def premium_todos() -> dict[str, dict]:
    """Una sola llamada para TODOS los símbolos."""
    d = _get("/openApi/swap/v2/quote/premiumIndex")
    if not d:
        return {}
    if isinstance(d, dict):
        d = [d]
    out = {}
    for item in d:
        try:
            s = item.get("symbol")
            mark = float(item.get("markPrice") or 0)
            index = float(item.get("indexPrice") or 0)
            fr = float(item.get("lastFundingRate") or 0)
            if not s or mark <= 0 or index <= 0:
                continue
            out[s] = {"mark": mark, "index": index,
                      "basis": (mark - index) / index * 100.0,
                      "funding": fr * 100.0}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def open_interest(symbol: str):
    d = _get("/openApi/swap/v2/quote/openInterest", {"symbol": symbol})
    if isinstance(d, list):
        d = d[0] if d else None
    if not isinstance(d, dict):
        return None
    try:
        v = float(d.get("openInterest") or 0)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def klines(symbol: str, limit: int | None = None):
    lim = limit or int(CFG["KLINES_LIMIT"])
    d = _get("/openApi/swap/v3/quote/klines",
             {"symbol": symbol, "interval": CFG["TIMEFRAME"], "limit": lim})
    if not d:
        d = _get("/openApi/swap/v2/quote/klines",
                 {"symbol": symbol, "interval": CFG["TIMEFRAME"], "limit": lim})
    if not d:
        return []
    filas = []
    for k in d:
        try:
            if isinstance(k, dict):
                filas.append({"t": int(k["time"]), "o": float(k["open"]),
                              "h": float(k["high"]), "l": float(k["low"]),
                              "c": float(k["close"])})
            else:
                filas.append({"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                              "l": float(k[3]), "c": float(k[4])})
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    filas.sort(key=lambda x: x["t"])
    return filas


# Contadores de descarga. Un fallo de red que nadie cuenta es un hueco en
# la historia que luego se lee como "calentando" sin motivo aparente.
FETCH = {"ok": 0, "sin_oi": 0, "sin_velas": 0, "excepcion": 0}


def _fetch_symbol_raw(symbol: str) -> dict | None:
    try:
        oi = open_interest(symbol)
        velas = klines(symbol)
        if oi is None:
            FETCH["sin_oi"] += 1
            return None
        if not velas:
            FETCH["sin_velas"] += 1
            return None
        FETCH["ok"] += 1
        return {"oi": oi, "velas": velas}
    except Exception:
        FETCH["excepcion"] += 1
        log.debug("fetch falló %s", symbol, exc_info=True)
        return None


# ─────────────────────────────────────────────────────── indicadores
def atr(velas, n):
    if len(velas) < n + 1:
        return 0.0
    trs = []
    for i in range(len(velas) - n, len(velas)):
        h, l, cp = velas[i]["h"], velas[i]["l"], velas[i - 1]["c"]
        trs.append(max(h - l, abs(h - cp), abs(l - cp)))
    return sum(trs) / len(trs) if trs else 0.0


def percentil(valores, x):
    if not valores:
        return 50.0
    return sum(1 for v in valores if v <= x) / len(valores) * 100.0


def zscore(hist, x):
    if len(hist) < 30:
        return None
    mu = statistics.fmean(hist)
    sd = statistics.pstdev(hist)
    return (x - mu) / sd if sd > 1e-12 else 0.0


def pctl(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p / 100.0 * len(s)))]


# ─────────────────────────────────────────────────────── estado
@dataclass
class Virtual:
    symbol: str
    lado: str
    abierta_ts: int
    entrada: float
    sl: float
    tp: float
    riesgo: float
    coste_r: float
    basis_z: float
    oi_z: float
    funding: float
    atr_pct: float
    conf_z: float = 0.0
    conf_regimen: str = "sin datos"
    barras: int = 0
    mfe: float = 0.0
    mae: float = 0.0


def _podar(h: deque, ahora: float, horas: float):
    limite = ahora - horas * 3600.0
    while h and h[0][0] < limite:
        h.popleft()


def _ventana(h: deque, ahora: float, horas: float) -> list:
    """
    Los puntos de las últimas N horas, para el z. Si horas<=0 devuelve
    todo (comportamiento anterior). Separar esto de la retención es lo
    que impide que el z se estreche solo según pasa el tiempo.
    """
    if horas <= 0:
        return list(h)
    limite = ahora - horas * 3600.0
    return [p for p in h if p[0] >= limite]


def _valor_hace(h: deque, ahora: float, horas: float):
    if not h:
        return None
    objetivo = ahora - horas * 3600.0
    if h[0][0] > objetivo:
        return None
    mejor = min(h, key=lambda p: abs(p[0] - objetivo))
    return mejor[1]


def _cambios_oi(lo: list, look_h: float) -> list[float]:
    """O(n log n). La versión con deque(lo[:i+1]) costaba 565 ms/símbolo."""
    ts = [p[0] for p in lo]
    look = look_h * 3600.0
    out: list[float] = []
    for i in range(len(lo)):
        objetivo = ts[i] - look
        if ts[0] > objetivo:
            continue
        j = bisect.bisect_left(ts, objetivo, 0, i + 1)
        mejor = min((k for k in (j - 1, j) if 0 <= k <= i),
                    key=lambda k: abs(ts[k] - objetivo), default=None)
        if mejor is None:
            continue
        ref = lo[mejor][1]
        if ref > 0:
            out.append((lo[i][1] - ref) / ref * 100.0)
    return out


def _apuntar(h: deque, ahora: float, valor: float, min_seg: float, tope: int):
    if h and (ahora - h[-1][0]) < min_seg:
        return
    h.append((ahora, valor))
    while len(h) > tope:
        h.popleft()


def _span_horas(h: deque) -> float:
    return (h[-1][0] - h[0][0]) / 3600.0 if len(h) > 1 else 0.0


class Estado:
    def __init__(self, path):
        self.path = path
        self.basis: dict[str, deque] = {}
        self.oi: dict[str, deque] = {}
        self.abiertas: dict[str, Virtual] = {}
        self.ultimo_informe = ""
        self.ultima_senal_ts = 0.0
        self._cargar()

    def _cargar(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            ahora = time.time()
            paso = float(CFG["SCAN_SEC"]) + 60.0
            migradas = 0

            def _leer(bloque):
                nonlocal migradas
                out = {}
                for k, v in bloque.items():
                    dq = deque()
                    if v and not isinstance(v[0], (list, tuple)):
                        migradas += 1
                        base = ahora - len(v) * paso
                        for i, x in enumerate(v):
                            dq.append((base + i * paso, float(x)))
                    else:
                        for p in v:
                            dq.append((float(p[0]), float(p[1])))
                    out[k] = dq
                return out

            self.basis = _leer(d.get("basis", {}))
            self.oi = _leer(d.get("oi", {}))
            if migradas:
                log.warning("Migradas %d series del formato antiguo: tiempos estimados",
                            migradas)
            # Las virtuales van APARTE: si el dataclass cambió de campos,
            # antes el except se comía TODO el bloque y perdías también
            # ultimo_informe. Ahora un fallo aquí no arrastra lo demás.
            try:
                self.abiertas = {k: Virtual(**v) for k, v in d.get("abiertas", {}).items()}
            except Exception as exc:  # noqa: BLE001
                log.warning("Virtuales del estado ilegibles (%s): se descartan, "
                            "la historia de basis/OI se conserva", exc)
                self.abiertas = {}
            self.ultimo_informe = d.get("ultimo_informe", "")
            self.ultima_senal_ts = float(d.get("ultima_senal_ts", 0) or 0)
            log.info("Estado cargado: %d símbolos con historia, %d virtuales abiertas",
                     len(self.basis), len(self.abiertas))
        except FileNotFoundError:
            log.warning("SIN ESTADO PREVIO en %s. Si Railway no tiene un volumen "
                        "montado ahí, esto se repite en CADA despliegue y el "
                        "calentamiento no termina nunca.", self.path)
        except Exception:
            log.exception("Estado ilegible, se empieza de cero")

    def guardar(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "basis": {k: [list(p) for p in v] for k, v in self.basis.items()},
                    "oi": {k: [list(p) for p in v] for k, v in self.oi.items()},
                    "abiertas": {k: asdict(v) for k, v in self.abiertas.items()},
                    "ultimo_informe": self.ultimo_informe,
                    "ultima_senal_ts": self.ultima_senal_ts,
                }, f)
            os.replace(tmp, self.path)
        except Exception:
            log.exception("No se pudo guardar el estado")


class _Cfg:
    """Puente para que confirm.py y panel.py lean CFG con getattr."""

    def __getattr__(self, k):
        if k in CFG:
            return CFG[k]
        raise AttributeError(k)


_CFG_OBJ = _Cfg()

COLS = ["cerrada_utc", "symbol", "lado", "abierta_utc", "entrada", "salida",
        "motivo", "r_bruto", "coste_r", "r_neto", "barras", "basis_z",
        "oi_z", "funding", "atr_pct", "conf_z", "conf_regimen", "mfe", "mae"]


def anotar(fila: dict):
    try:
        ruta = CFG["CSV"]
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        nuevo = not os.path.exists(ruta) or os.path.getsize(ruta) == 0
        with open(ruta, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
            if nuevo:
                w.writeheader()
            w.writerow(fila)
    except Exception:
        log.exception("No se pudo anotar en el CSV")


def tg(texto: str, tipo: str = "informe"):
    if tipo == "senal" and not CFG["TG_SIGNALS"]:
        return
    if tipo == "cierre" and not CFG["TG_CLOSES"]:
        return
    if not CFG["TG_TOKEN"] or not CFG["TG_CHAT"]:
        log.info("[telegram] %s", texto.replace("\n", " | ")[:300])
        return
    try:
        requests.post(f"https://api.telegram.org/bot{CFG['TG_TOKEN']}/sendMessage",
                      json={"chat_id": CFG["TG_CHAT"], "text": texto,
                            "parse_mode": "HTML", "disable_web_page_preview": True},
                      timeout=15)
    except Exception:
        log.exception("Telegram falló")


def enviar_csv(motivo: str = "") -> bool:
    for ruta, nombre in ((CFG["CSV"], "crowding_ops.csv"),
                         (CFG.get("PANEL_CSV", "/data/crowding_panel.csv"),
                          "crowding_panel.csv")):
        try:
            if not os.path.exists(ruta) or os.path.getsize(ruta) < 100:
                continue
            if not CFG["TG_TOKEN"] or not CFG["TG_CHAT"]:
                log.warning("Sin Telegram: el CSV está en %s", ruta)
                return False
            tam = os.path.getsize(ruta)
            if tam > 48 * 1024 * 1024:
                log.warning("%s pesa %.0f MB: Telegram no lo admite", nombre, tam / 1e6)
                continue
            with open(ruta, "rb") as f:
                r = requests.post(
                    f"https://api.telegram.org/bot{CFG['TG_TOKEN']}/sendDocument",
                    data={"chat_id": CFG["TG_CHAT"],
                          "caption": f"{nombre} · {tam/1024:.0f} KB"
                                     f"{(' · ' + motivo) if motivo else ''}"[:1000]},
                    files={"document": (nombre, f, "text/csv")}, timeout=120)
            if r.status_code == 200 and r.json().get("ok") is True:
                log.info("%s enviado (%d bytes)", nombre, tam)
            else:
                log.error("Telegram rechazó %s: %s %s", nombre, r.status_code,
                          r.text[:200])
        except Exception:
            log.exception("No se pudo enviar %s", nombre)
    return True


# ═══════════════════════════════════════════════════════ FASE 1 · muestreo
def muestrear(symbol: str, velas: list, p: dict, oi: float, st: Estado) -> dict | None:
    """
    Apunta la observación y calcula las métricas. SIN NINGUNA PUERTA.

    AQUÍ ESTABA EL FALLO DE LA VERSIÓN ANTERIOR. El muestreo vivía dentro
    de evaluar(), detrás de tres salidas tempranas, y en el bucle un
    símbolo con virtual abierta ni siquiera llegaba a evaluar(). Cada
    fallo de OI y cada virtual abierta (hasta 4 h) abría un hueco en la
    historia de ese símbolo; el calentamiento exige 200 muestras y esos
    huecos lo alargaban sin que nada lo dijera.

    Ahora: si hay dato, se apunta. Punto. Lo que decide si se puede
    OPERAR se mira después, y no toca la historia.

    Devuelve None solo si faltan datos de verdad.
    """
    if not velas or len(velas) < 60 or p is None or oi is None or oi <= 0:
        return None

    ahora = time.time()
    hb = st.basis.setdefault(symbol, deque())
    ho = st.oi.setdefault(symbol, deque())
    _apuntar(hb, ahora, p["basis"], float(CFG["MUESTRA_MIN_SEG"]), int(CFG["MAX_PUNTOS"]))
    _apuntar(ho, ahora, oi, float(CFG["MUESTRA_MIN_SEG"]), int(CFG["MAX_PUNTOS"]))
    horas_ret = float(CFG["HIST_HORAS"])
    _podar(hb, ahora, horas_ret)
    _podar(ho, ahora, horas_ret)

    m: dict[str, Any] = {"symbol": symbol, "listo": False, "motivo": ""}

    span = _span_horas(hb)
    min_h = float(CFG["MIN_HORAS"])
    min_n = int(CFG["MIN_MUESTRAS"])
    m["span_h"] = span
    m["n_muestras"] = len(hb)
    if span < min_h or len(hb) < min_n:
        m["motivo"] = f"calentando ({span:.1f}/{min_h:.0f}h, {len(hb)}/{min_n})"
        return m

    look_h = float(CFG["OI_LOOK_H"])
    oi_prev = _valor_hace(ho, ahora, look_h)
    if oi_prev is None or oi_prev <= 0:
        m["motivo"] = "sin ventana de OI"
        return m
    oi_chg = (oi - oi_prev) / oi_prev * 100.0

    zwin = float(CFG["Z_WIN_HORAS"])
    hist_b = [x[1] for x in _ventana(hb, ahora, zwin)]
    hist_o = _cambios_oi(_ventana(ho, ahora, zwin), look_h)
    zb = zscore(hist_b, p["basis"])
    zo = zscore(hist_o, oi_chg)
    if zb is None or zo is None:
        m["motivo"] = (f"z no calculable (basis {len(hist_b)}, "
                       f"OI {len(hist_o)} — hacen falta 30)")
        return m

    px = velas[-1]["c"]
    a = atr(velas, int(CFG["ATR_LEN"]))
    if px <= 0 or a <= 0:
        m["motivo"] = "precio o ATR no válidos"
        return m
    riesgo = float(CFG["SL_ATR"]) * a

    m.update({
        "listo": True, "motivo": "ok",
        "px": px, "basis_z": zb, "oi_z": zo,
        "pct_precio": percentil([v["c"] for v in velas[-100:]], px),
        "atr_pct": a / px * 100.0,
        "funding": p["funding"],
        "riesgo": riesgo,
        "coste_r": (float(CFG["COST_PCT"]) / 100.0 * px) / riesgo,
        "velas": velas,
    })
    return m


# ═══════════════════════════════════════════════════════ FASE 2 · puertas
def umbrales_transversales(metricas: list[dict]) -> tuple[float, float]:
    """
    Umbrales de basis_z sacados del propio universo de esta instantánea.

    Siempre existe un 2% más extremo, así que en modo transversal SIEMPRE
    hay candidatos y la tasa de señales deja de depender de la
    volatilidad general. Eso arregla la MUESTRA, no la ventaja: un
    símbolo puede ser el más extremo de un universo plano y no estar
    amontonado en absoluto. Por eso XS_Z_MIN pone un suelo por debajo del
    cual no se opera aunque se sea el primero de la lista.
    """
    zs = sorted(m["basis_z"] for m in metricas if m.get("listo"))
    if len(zs) < 30:
        return float("inf"), float("-inf")
    top = float(CFG["XS_TOP_PCT"])
    suelo = float(CFG["XS_Z_MIN"])
    alto = zs[max(0, int(len(zs) * (1 - top / 100.0)) - 1)]
    bajo = zs[min(len(zs) - 1, int(len(zs) * (top / 100.0)))]
    return max(alto, suelo), min(bajo, -suelo)


def disparar(m: dict, st: Estado, z_alto: float, z_bajo: float) -> tuple[Any, str]:
    """Devuelve (('LONG'|'SHORT', datos), motivo) o (None, motivo)."""
    zb, zo, pr = m["basis_z"], m["oi_z"], m["pct_precio"]
    ext = float(CFG["EXT_PCT"])

    largos_amont = zb >= z_alto and zo >= float(CFG["Z_OI"]) and pr >= ext
    cortos_amont = zb <= z_bajo and zo >= float(CFG["Z_OI"]) and pr <= (100 - ext)
    if not (largos_amont or cortos_amont):
        return None, f"sin amontonamiento (zb {zb:.2f})"

    ATR_AMONT.append(m["atr_pct"])
    if len(ATR_AMONT) > 5000:
        del ATR_AMONT[:2500]

    if m["atr_pct"] < float(CFG["MIN_ATR_PCT"]):
        return None, f"sin amplitud ({m['atr_pct']:.2f}%)"
    if m["atr_pct"] > float(CFG["MAX_ATR_PCT"]):
        return None, f"amplitud excesiva ({m['atr_pct']:.2f}%)"
    if m["coste_r"] > float(CFG["MAX_COST_R"]):
        return None, f"coste {m['coste_r']:.2f}R"

    velas = m["velas"]
    # Con VELA_CERRADA, la vela de disparo es la última CERRADA y su
    # referencia la anterior. Sin esto se evalúa la vela en curso: la
    # señal aparece y desaparece dentro del mismo bar y la 'entrada'
    # apuntada no es reproducible ni en el propio CSV.
    if bool(CFG["VELA_CERRADA"]):
        if len(velas) < 3:
            return None, "pocas velas para la cerrada"
        ult, ant = velas[-2], velas[-3]
        precio = ult["c"]
        ts_ref = ult["t"]
    else:
        ult, ant = velas[-1], velas[-2]
        precio = ult["c"]
        ts_ref = ult["t"]

    lado = None
    if largos_amont and ult["c"] < ant["l"] and ult["c"] < ult["o"]:
        lado = "SHORT"
    elif cortos_amont and ult["c"] > ant["h"] and ult["c"] > ult["o"]:
        lado = "LONG"
    if lado is None:
        return None, "esperando vela en contra"

    cierres_reg = [v["c"] for v in velas[-(int(CFG["CONFIRM_WIN"]) + 1):]]
    reg = cf.calcular(_CFG_OBJ, cierres_reg)
    return (lado, {"px": precio, "ts": ts_ref, "riesgo": m["riesgo"],
                   "coste_r": m["coste_r"], "zb": zb, "zo": zo,
                   "funding": m["funding"], "atr_pct": m["atr_pct"],
                   "conf_z": reg.z if reg.ok else 0.0,
                   "conf_regimen": reg.etiqueta if reg.ok else reg.motivo}), "señal"


ATR_AMONT: list[float] = []


# ═══════════════════════════════════════════════════════ AUTOPSIA
def autopsia(metricas: list[dict], z_alto: float, z_bajo: float) -> list[str]:
    """
    Con cero señales, ¿qué puerta las mató y POR CUÁNTO?

    El embudo de antes decía "sin amontonamiento: 300" y eso no distingue
    dos situaciones que piden arreglos opuestos:
      · el mercado está plano y el umbral es correcto  -> esperar
      · el umbral está fuera del alcance del mercado   -> bajarlo o
        cambiar a selección transversal

    La diferencia se ve en el MÁXIMO: si el zb más alto de 300 símbolos
    es 1.4 y el umbral es 2.0, hoy no había forma de disparar. Si el
    máximo es 3.1, el corte está en otra puerta.
    """
    listos = [m for m in metricas if m.get("listo")]
    L = []
    if not listos:
        calientan = sum(1 for m in metricas if "calentando" in m.get("motivo", ""))
        L.append(f"AUTOPSIA · ninguno listo ({calientan}/{len(metricas)} calentando)")
        if metricas:
            spans = [m.get("span_h", 0) for m in metricas]
            ns = [m.get("n_muestras", 0) for m in metricas]
            L.append(f"  historia: span p50 {pctl(spans,50):.1f}h p90 {pctl(spans,90):.1f}h "
                     f"(hace falta {CFG['MIN_HORAS']}) · muestras p50 {pctl(ns,50):.0f} "
                     f"p90 {pctl(ns,90):.0f} (hacen falta {CFG['MIN_MUESTRAS']})")
        return L

    zb = [m["basis_z"] for m in listos]
    zo = [m["oi_z"] for m in listos]
    pr = [m["pct_precio"] for m in listos]
    cr = [m["coste_r"] for m in listos]
    at = [m["atr_pct"] for m in listos]

    def linea(nombre, xs, umbral, signo):
        """
        signo='>=' exige superar; '<=' exige quedar por debajo.

        Se enseña la COLA QUE IMPORTA, no siempre la de arriba: para un
        umbral '<=' los percentiles altos no dicen nada, y mirarlos fue
        justo lo que escondía el problema en la versión anterior.
        """
        if signo == ">=":
            n_pasan = sum(1 for x in xs if x >= umbral)
            extremo = max(xs)
            alcanza = extremo >= umbral
            cola = (f"p50 {pctl(xs,50):+7.2f} p90 {pctl(xs,90):+7.2f} "
                    f"p99 {pctl(xs,99):+7.2f} máx {extremo:+7.2f}")
        else:
            n_pasan = sum(1 for x in xs if x <= umbral)
            extremo = min(xs)
            alcanza = extremo <= umbral
            cola = (f"p50 {pctl(xs,50):+7.2f} p10 {pctl(xs,10):+7.2f} "
                    f"p01 {pctl(xs,1):+7.2f} mín {extremo:+7.2f}")
        marca = "" if alcanza else "  ← INALCANZABLE HOY"
        return (f"  {nombre:<14} {cola} · umbral {signo}{umbral:+.2f} · "
                f"pasan {n_pasan}{marca}")

    L.append(f"AUTOPSIA · {len(listos)} símbolos listos de {len(metricas)}")
    L.append(linea("basis_z alto", zb, z_alto, ">="))
    L.append(linea("basis_z bajo", zb, z_bajo, "<="))
    L.append(linea("oi_z", zo, float(CFG["Z_OI"]), ">="))
    L.append(linea("pct_precio", pr, float(CFG["EXT_PCT"]), ">="))
    L.append(linea("coste_r", cr, float(CFG["MAX_COST_R"]), "<="))
    L.append(linea("atr_pct", at, float(CFG["MAX_ATR_PCT"]), "<="))

    # Cuántos superan las TRES condiciones a la vez: es lo que de verdad
    # decide, porque pasar cada una por separado no implica pasarlas juntas.
    tri_alto = sum(1 for m in listos
                   if m["basis_z"] >= z_alto and m["oi_z"] >= float(CFG["Z_OI"])
                   and m["pct_precio"] >= float(CFG["EXT_PCT"]))
    tri_bajo = sum(1 for m in listos
                   if m["basis_z"] <= z_bajo and m["oi_z"] >= float(CFG["Z_OI"])
                   and m["pct_precio"] <= 100 - float(CFG["EXT_PCT"]))
    L.append(f"  AMONTONADOS (las tres a la vez): {tri_alto} largos · {tri_bajo} cortos")
    if tri_alto + tri_bajo == 0:
        L.append("  → nadie se amontona. Si esto se repite día tras día y el máximo "
                 "de basis_z no llega al umbral, el umbral no es del mercado: "
                 "prueba SELECCION=transversal.")
    else:
        L.append("  → hay amontonados; si no salen señales, mueren en amplitud, "
                 "coste o en la vela en contra.")
    return L


# ═══════════════════════════════════════════════════════ virtuales
def seguir_virtuales(st: Estado, symbol: str, velas):
    v = st.abiertas.get(symbol)
    if v is None:
        return
    nuevas = [k for k in velas if k["t"] > v.abierta_ts]
    if not nuevas:
        return
    v.barras = len(nuevas)
    largo = v.lado == "LONG"
    salida = motivo = None
    for k in nuevas:
        favor = (k["h"] - v.entrada) if largo else (v.entrada - k["l"])
        contra = (v.entrada - k["l"]) if largo else (k["h"] - v.entrada)
        v.mfe = max(v.mfe, favor / v.riesgo)
        v.mae = max(v.mae, contra / v.riesgo)
        toca_sl = (k["l"] <= v.sl) if largo else (k["h"] >= v.sl)
        toca_tp = (k["h"] >= v.tp) if largo else (k["l"] <= v.tp)
        if toca_sl:
            salida, motivo = v.sl, "stop"
            break
        if toca_tp:
            salida, motivo = v.tp, "objetivo"
            break
    if salida is None and v.barras >= int(CFG["MAX_BARS"]):
        salida, motivo = nuevas[-1]["c"], "tiempo"
    if salida is None:
        return

    bruto = ((salida - v.entrada) if largo else (v.entrada - salida)) / v.riesgo
    neto = bruto - v.coste_r
    anotar({
        "cerrada_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": v.symbol, "lado": v.lado,
        "abierta_utc": datetime.fromtimestamp(v.abierta_ts / 1000, timezone.utc)
                               .isoformat(timespec="seconds"),
        "entrada": v.entrada, "salida": salida, "motivo": motivo,
        "r_bruto": round(bruto, 4), "coste_r": round(v.coste_r, 4),
        "r_neto": round(neto, 4), "barras": v.barras,
        "basis_z": round(v.basis_z, 3), "oi_z": round(v.oi_z, 3),
        "funding": round(v.funding, 5), "atr_pct": round(v.atr_pct, 3),
        "conf_z": round(v.conf_z, 3), "conf_regimen": v.conf_regimen,
        "mfe": round(v.mfe, 3), "mae": round(v.mae, 3),
    })
    icono = "✅" if neto > 0 else "🔴"
    tg(f"{icono} <b>{v.symbol.split('-')[0]}</b> {v.lado} virtual cerrada por "
       f"{motivo}: <b>{neto:+.2f} R</b> en {v.barras} velas", "cierre")
    st.abiertas.pop(symbol, None)


# ═══════════════════════════════════════════════════════ informe
def informe(st: Estado):
    try:
        with open(CFG["CSV"], encoding="utf-8") as f:
            filas = list(csv.DictReader(f))
    except Exception:
        filas = []
    if not filas:
        return (f"📊 <b>Crowding · informe</b>\nSin operaciones cerradas todavía.\n"
                f"Historia acumulada en {len(st.basis)} símbolos.\n"
                f"<i>Mira la línea AUTOPSIA del log: dice qué puerta corta y "
                f"si el umbral está fuera del alcance del mercado.</i>")
    rs = [float(x["r_neto"]) for x in filas]
    n = len(rs)
    media = statistics.fmean(rs)
    sd = statistics.pstdev(rs) if n > 1 else 1.0
    t = media * math.sqrt(n) / sd if sd > 1e-9 else 0.0
    gan = sum(1 for r in rs if r > 0)
    por_dia: dict[str, float] = {}
    for x in filas:
        d = x["cerrada_utc"][:10]
        por_dia[d] = por_dia.get(d, 0.0) + float(x["r_neto"])
    peor = max(por_dia.items(), key=lambda kv: abs(kv[1])) if por_dia else ("-", 0.0)
    dom = abs(peor[1]) / max(abs(sum(rs)), 1e-9) * 100 if rs else 0

    if n < 31:
        lectura = f"muestra {n}: no alcanza ni para detectar 0.50 R/op"
    elif n < 87:
        lectura = f"muestra {n}: solo detectaría una ventaja ≥0.50 R/op"
    elif n < 196:
        lectura = f"muestra {n}: solo detectaría una ventaja ≥0.30 R/op"
    else:
        lectura = f"muestra {n}: detecta ventajas ≥0.20 R/op"

    L = [f"📊 <b>Crowding · informe</b>",
         f"<b>{n}</b> operaciones · {gan / n * 100:.0f}% ganadoras",
         f"<b>{media:+.3f} R/op</b> · total {sum(rs):+.1f} R · t={t:.2f}",
         f"<i>{lectura}</i>",
         f"Días con operaciones: {len(por_dia)}"]
    if dom > 40:
        L.append(f"⚠️ El día {peor[0]} pesa el {dom:.0f}% del total: son señales "
                 f"correlacionadas, no {n} independientes")
    if abs(t) < 2:
        L.append("Sin significación: <b>esto todavía no dice nada</b>")

    # MFE/MAE: contesta gratis si el objetivo y el stop están donde deben.
    mfes = [float(x["mfe"]) for x in filas if x.get("mfe")]
    maes_g = [float(x["mae"]) for x in filas if x.get("mae") and float(x["r_neto"]) > 0]
    if mfes:
        perd = [float(x["mfe"]) for x in filas
                if x.get("mfe") and float(x["r_neto"]) <= 0]
        if perd:
            cerca = sum(1 for x in perd if x >= 1.0)
            L.append(f"MFE de las perdedoras: mediana {statistics.median(perd):.2f} R · "
                     f"{cerca}/{len(perd)} pasaron de +1 R y aun así perdieron")
    if maes_g:
        L.append(f"MAE de las ganadoras: p90 {pctl(maes_g, 90):.2f} R "
                 f"(stop en 1.00 R)")

    for campo, titulo in (("conf_regimen", "Por régimen"), ("lado", "Por lado")):
        g: dict[str, list] = {}
        for x in filas:
            g.setdefault(x.get(campo) or "?", []).append(float(x["r_neto"]))
        if len(g) > 1:
            L.append(titulo + ":")
            for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
                L.append(f"  {k}: {statistics.fmean(v):+.3f} R/op (n={len(v)})"
                         + (" ⚠" if len(v) < 31 else ""))
        elif campo == "lado" and g:
            L.append(f"<i>Solo hay {next(iter(g))}: el otro lado no ha disparado "
                     f"ni una vez. Revisa Z_BASIS y EXT_PCT.</i>")
    L.append(f"Virtuales abiertas: {len(st.abiertas)}")
    return "\n".join(L)


EMBUDO: dict[str, int] = {}


# ═══════════════════════════════════════════════════════ ciclo
def ciclo(st: Estado, simbolos: list[str]):
    global _ultimo_ciclo, _ultimo_heartbeat
    señales = 0
    razones: dict[str, int] = {}
    for k in FETCH:
        FETCH[k] = 0

    premiums = premium_todos()
    if not premiums:
        log.warning("No se pudo obtener premiumIndex global: ciclo saltado")
        return

    raw: dict[str, dict] = {}
    workers = max(1, int(CFG["MAX_WORKERS"]))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_symbol_raw, sym): sym for sym in simbolos}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                data = fut.result()
                if data:
                    raw[sym] = data
            except Exception:
                log.debug("Error en worker %s", sym, exc_info=True)

    # ── FASE 1: muestrear TODO, sin puertas y sin excepciones ──────
    # Incluidos los símbolos con virtual abierta: antes se saltaban con
    # un `continue` y perdían hasta 4 h de historia cada vez.
    metricas: list[dict] = []
    for sym in simbolos:
        data = raw.get(sym)
        if not data:
            razones["sin datos"] = razones.get("sin datos", 0) + 1
            continue
        try:
            seguir_virtuales(st, sym, data["velas"])
            m = muestrear(sym, data["velas"], premiums.get(sym), data["oi"], st)
            if m is None:
                razones["sin datos"] = razones.get("sin datos", 0) + 1
                continue
            metricas.append(m)
            if not m["listo"]:
                clave = m["motivo"].split("(")[0].strip()
                razones[clave] = razones.get(clave, 0) + 1
        except Exception:
            log.exception("Fallo muestreando %s", sym)

    # ── Panel: se escribe con TODO lo que esté listo, calienten otros o no
    if pn.toca(_CFG_OBJ):
        filas = [{k: m[k] for k in ("symbol", "px", "basis_z", "oi_z",
                                    "pct_precio", "atr_pct", "funding", "coste_r")}
                 for m in metricas if m.get("listo")]
        escritas = pn.registrar(_CFG_OBJ, filas)
        if escritas:
            log.info("Panel: %d filas apuntadas (%s)", escritas, CFG["PANEL_CSV"])

    # ── FASE 2: umbrales y disparo ─────────────────────────────────
    modo = str(CFG["SELECCION"]).lower()
    if modo == "transversal":
        z_alto, z_bajo = umbrales_transversales(metricas)
    else:
        z_alto, z_bajo = float(CFG["Z_BASIS"]), -float(CFG["Z_BASIS"])

    for m in metricas:
        if not m.get("listo") or m["symbol"] in st.abiertas:
            continue
        try:
            sig, motivo = disparar(m, st, z_alto, z_bajo)
            clave = motivo.split("(")[0].strip()
            razones[clave] = razones.get(clave, 0) + 1
            if sig is None:
                continue
            lado, d = sig
            sym = m["symbol"]
            entrada = d["px"]
            sl = entrada - d["riesgo"] if lado == "LONG" else entrada + d["riesgo"]
            tp = (entrada + float(CFG["TP_R"]) * d["riesgo"] if lado == "LONG"
                  else entrada - float(CFG["TP_R"]) * d["riesgo"])
            st.abiertas[sym] = Virtual(
                symbol=sym, lado=lado, abierta_ts=d["ts"], entrada=entrada,
                sl=sl, tp=tp, riesgo=d["riesgo"], coste_r=d["coste_r"],
                basis_z=d["zb"], oi_z=d["zo"], funding=d["funding"],
                atr_pct=d["atr_pct"], conf_z=d["conf_z"],
                conf_regimen=d["conf_regimen"])
            señales += 1
            st.ultima_senal_ts = time.time()
            flecha = "🟢" if lado == "LONG" else "🔴"
            tg(f"{flecha} <b>{sym.split('-')[0]}</b> {lado} (virtual)\n"
               f"Entrada <code>{entrada:.8g}</code> · SL <code>{sl:.8g}</code> · "
               f"TP <code>{tp:.8g}</code>\n"
               f"basis z {d['zb']:+.2f} · OI z {d['zo']:+.2f} · "
               f"funding {d['funding']:+.4f}%\n"
               f"ATR {d['atr_pct']:.2f}% · coste {d['coste_r']:.2f} R\n"
               f"<i>Solo señal. El bot no opera.</i>", "senal")
        except Exception:
            log.exception("Fallo disparando %s", m.get("symbol"))

    # ── Log ────────────────────────────────────────────────────────
    for k, v in razones.items():
        EMBUDO[k] = EMBUDO.get(k, 0) + v
    ahora = time.time()
    cad = (ahora - _ultimo_ciclo) / 60.0 if _ultimo_ciclo else 0.0
    _ultimo_ciclo = ahora

    listos = sum(1 for m in metricas if m.get("listo"))
    total = len(simbolos)
    top = sorted(razones.items(), key=lambda kv: -kv[1])[:4]
    log.info("Ciclo: %d/%d con datos · %d listos · %d señales · cadencia %.1f min "
             "| descargas ok=%d sin_oi=%d sin_velas=%d exc=%d | %s",
             len(raw), total, listos, señales, cad,
             FETCH["ok"], FETCH["sin_oi"], FETCH["sin_velas"], FETCH["excepcion"],
             " · ".join(f"{k}: {v}" for k, v in top))
    log.info("Embudo acumulado | %s",
             " · ".join(f"{k}: {v}" for k, v in sorted(EMBUDO.items(),
                                                       key=lambda kv: -kv[1])))

    # LA LÍNEA QUE CONTESTA "¿por qué no da señales?"
    if señales == 0:
        for linea in autopsia(metricas, z_alto, z_bajo):
            log.info(linea)

    if len(ATR_AMONT) >= 20:
        log.info("Amplitud de los amontonamientos (n=%d): p10 %.2f%% · mediana "
                 "%.2f%% · p90 %.2f%% | filtro actual %.2f–%.2f%%",
                 len(ATR_AMONT), pctl(ATR_AMONT, 10), pctl(ATR_AMONT, 50),
                 pctl(ATR_AMONT, 90), float(CFG["MIN_ATR_PCT"]),
                 float(CFG["MAX_ATR_PCT"]))

    # ── Silencio económico: el bot vivo y mudo no da error en el log ──
    mudo_h = float(CFG["MUDO_ALERTA_H"])
    if mudo_h > 0 and listos > 0 and st.ultima_senal_ts > 0:
        horas = (ahora - st.ultima_senal_ts) / 3600.0
        if horas >= mudo_h and (ahora - _ultimo_heartbeat) >= 3600:
            tg("🔇 <b>Crowding lleva " + f"{horas:.0f} h sin una sola señal</b>\n"
               + f"{listos}/{total} símbolos listos, o sea que NO es calentamiento.\n"
               + "\n".join(autopsia(metricas, z_alto, z_bajo)[:8]))

    if ahora - _ultimo_heartbeat >= 3600:
        _ultimo_heartbeat = ahora
        spans = [m.get("span_h", 0) for m in metricas]
        tg(f"💓 <b>Crowding heartbeat</b>\n"
           f"Universo {total} · con datos {len(raw)} · listos {listos}\n"
           f"Historia: span p50 {pctl(spans, 50):.1f} h (hace falta "
           f"{CFG['MIN_HORAS']:.0f})\n"
           f"Virtuales abiertas: {len(st.abiertas)} · cadencia {cad:.1f} min\n"
           f"Selección: {modo}" + (f" (umbral vivo {z_alto:+.2f} / {z_bajo:+.2f})"
                                   if modo == "transversal" else ""))

    st.guardar()


def main():
    log.info("Crowding bot v3.0 — SOLO SEÑALES, sin claves de API, %s | "
             "selección=%s | workers=%s",
             CFG["TIMEFRAME"], CFG["SELECCION"], CFG["MAX_WORKERS"])
    st = Estado(CFG["STATE"])
    syms = contratos()
    vols = volumenes()
    if vols:
        syms = [s for s in syms if vols.get(s, 0) >= float(CFG["MIN_VOL_24H"])]
        syms.sort(key=lambda s: vols.get(s, 0), reverse=True)
    syms = syms[: int(CFG["MAX_SYMBOLS"])]
    log.info("Universo: %d símbolos", len(syms))
    tg(f"🤖 <b>Crowding bot v3.0 arrancado</b>\n"
       f"{len(syms)} símbolos · {CFG['TIMEFRAME']} · selección "
       f"<b>{CFG['SELECCION']}</b>\n"
       f"Ventana del z: {CFG['Z_WIN_HORAS']:.0f} h · retención "
       f"{CFG['HIST_HORAS']:.0f} h\n"
       f"Vela de disparo: {'CERRADA' if CFG['VELA_CERRADA'] else 'en curso'}\n"
       f"<i>Sin claves de API: solo lee endpoints públicos. No puede operar.</i>\n"
       f"<i>Con cero señales, el log imprime AUTOPSIA con la distancia a cada "
       f"umbral.</i>")

    if CFG["CSV_AL_ARRANCAR"]:
        enviar_csv("pedido al arrancar")

    ultimo_universo = time.time()
    while True:
        try:
            if time.time() - ultimo_universo > 6 * 3600:
                v2 = volumenes()
                if v2:
                    s2 = [s for s in contratos()
                          if v2.get(s, 0) >= float(CFG["MIN_VOL_24H"])]
                    s2.sort(key=lambda s: v2.get(s, 0), reverse=True)
                    if s2:
                        syms = s2[: int(CFG["MAX_SYMBOLS"])]
                ultimo_universo = time.time()

            ciclo(st, syms)

            hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if (datetime.now(timezone.utc).hour == int(CFG["REPORT_HOUR"])
                    and st.ultimo_informe != hoy):
                st.ultimo_informe = hoy
                st.guardar()
                tg(informe(st))
                if CFG["CSV_CON_INFORME"]:
                    enviar_csv("informe diario")
        except Exception:
            log.exception("Fallo en el ciclo")
        time.sleep(int(CFG["SCAN_SEC"]))


if __name__ == "__main__":
    main()
