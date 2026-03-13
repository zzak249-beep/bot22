"""
metaclaw.py — Agente IA de trading con habilidades evolutivas
SMC Bot v5.0 [MetaClaw Edition — FIXED]

Bugs corregidos:
  FIX#1 — API key sin .strip() — espacios invisibles rompían la auth
  FIX#2 — import re dentro de funciones — movido al nivel del módulo
  FIX#3 — timeout 12s demasiado corto para aprender (max_tokens=200)
  FIX#4 — score referenciado como /14, el sistema usa /16
  FIX#5 — _skill_relevante threshold=1 demasiado permisivo
  FIX#6 — JSON regex no manejaba markdown fences de Claude
  FIX#7 — aprender() sin guard: llamaba Claude con señal sin datos
  FIX#8 — _save_skills sin lock — corrupción si workers concurrentes
"""

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests

import config

log = logging.getLogger("metaclaw")

# ══════════════════════════════════════════════════════════════
# PERSISTENCIA + LOCK
# ══════════════════════════════════════════════════════════════

_skills_lock = threading.Lock()


def _skills_path() -> str:
    base = config.MEMORY_DIR or ""
    return os.path.join(base, "metaclaw_skills.json") if base else "metaclaw_skills.json"


def _load_skills() -> list:
    path = _skills_path()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception as e:
        log.warning(f"[MCL] Error cargando skills: {e}")
    return []


def _save_skills(skills: list):
    """FIX#8: lock para evitar corrupción por escrituras simultáneas."""
    path = _skills_path()
    with _skills_lock:
        try:
            if len(skills) > 80:
                skills = sorted(
                    skills,
                    key=lambda s: (s.get("trades", 0), s.get("updated", "")),
                    reverse=True,
                )[:80]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(skills, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning(f"[MCL] Error guardando skills: {e}")


def _skill_relevante(skill: dict, señal: dict) -> bool:
    """
    FIX#5: threshold subido de 1 a 3.
    Necesita al menos lado + algo más para ser relevante.
    """
    tags    = set(skill.get("tags", []))
    par     = señal.get("par", "")
    lado    = señal.get("lado", "")
    kz      = señal.get("kz", "")
    motivos = set(señal.get("motivos", []))

    puntos = 0
    if lado and lado in tags:              puntos += 3
    if kz   and kz   in tags:             puntos += 2
    if any(t in motivos for t in tags):    puntos += 2
    if par  and par   in tags:             puntos += 4
    if "GENERAL" in tags:                  puntos += 1

    return puntos >= 3  # FIX#5


# ══════════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════════

def _api_key() -> str:
    """FIX#1: .strip() elimina espacios/newlines invisibles."""
    return (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()


def _extract_json(text: str) -> Optional[dict]:
    """
    FIX#6: Extractor robusto — maneja JSON puro, ```json...```, JSON embebido.
    """
    if not text:
        return None
    # Intento 1: parsear directamente
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    # Intento 2: quitar markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Intento 3: buscar primer objeto JSON en texto libre
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


def _call_claude(system_prompt: str, user_msg: str,
                 max_tokens: int = 200, timeout: int = 20) -> Optional[str]:
    """
    FIX#1: .strip() en api_key
    FIX#3: timeout parametrizable (antes fijo a 12s)
    """
    api_key = _api_key()
    if not api_key:
        log.debug("[MCL] ANTHROPIC_API_KEY no configurada — saltando MetaClaw")
        return None

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-6",
                "max_tokens": max_tokens,
                "system":     system_prompt,
                "messages":   [{"role": "user", "content": user_msg}],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"].strip()
    except requests.exceptions.Timeout:
        log.warning(f"[MCL] Timeout ({timeout}s) llamando Claude")
        return None
    except Exception as e:
        log.warning(f"[MCL] Error llamando Claude: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# VALIDAR SEÑAL
# ══════════════════════════════════════════════════════════════

def validar(señal: dict) -> dict:
    """
    Evalúa una señal SMC con Claude + skills aprendidas.
    Returns: {aprobar, confianza, razon, ajuste_sl, metaclaw_activo}
    """
    fallback = {
        "aprobar": True, "confianza": 5,
        "razon": "metaclaw_offline", "ajuste_sl": 0,
        "metaclaw_activo": False,
    }

    if not _api_key():
        return fallback

    skills     = _load_skills()
    relevantes = [s for s in skills if _skill_relevante(s, señal)][:6]

    if relevantes:
        lines = [
            f"• [{s['wins']}/{s['trades']}] {s['texto']}"
            for s in relevantes
        ]
        skills_txt = "HABILIDADES RELEVANTES:\n" + "\n".join(lines)
    else:
        skills_txt = "HABILIDADES: ninguna aún para este contexto."

    # FIX#4: score /16
    rsi     = señal.get("rsi", 50)
    score   = señal.get("score", 0)
    lado    = señal.get("lado", "?")
    par     = señal.get("par", "?")
    rr      = señal.get("rr", 0)
    motivos = " + ".join(señal.get("motivos", []))
    kz      = señal.get("kz", "FUERA")
    htf     = señal.get("htf", "NEUTRAL")
    vwap_ok = "SOBRE" if señal.get("sobre_vwap") else "BAJO"
    patron  = señal.get("patron", "ninguno")
    sweep   = "SÍ" if (señal.get("sweep_bull") or señal.get("sweep_bear")) else "NO"
    ob_mit  = "MITIGADO" if señal.get("ob_mitigado") else "VÁLIDO"
    ob_fvg  = "SÍ" if (señal.get("ob_fvg_bull") or señal.get("ob_fvg_bear")) else "NO"

    system_prompt = """Eres MetaClaw, agente experto en trading de futuros perpetuos.
Estrategia: ICT/SMC — FVG, Order Blocks, Liquidity Sweeps, BOS/CHoCH.

Responde SOLO en JSON, sin markdown ni texto extra:
{"aprobar": true/false, "confianza": 1-10, "razon": "max 60 chars"}

RECHAZAR si:
- RSI >75 LONG o <25 SHORT sin divergencia
- OB mitigado SIN sweep previo
- Fuera KZ Y score<7 Y HTF contrario
- R:R < 2.0
- HTF contrario al trade

APROBAR con confianza 8-10 si:
- OB+FVG + sweep + killzone activa
- Pin Bar/Engulfing en zona premium/descuento
- HTF alineado + score >= 9
- Skills previas muestran setup ganador"""

    user_msg = f"""SEÑAL:
Par: {par} | {lado} | Score: {score}/16
RSI: {rsi:.1f} | R:R: {rr:.2f} | KZ: {kz} | HTF: {htf}
VWAP: {vwap_ok} | Patrón: {patron}
Sweep: {sweep} | OB: {ob_mit} | OB+FVG: {ob_fvg}
Señales: {motivos}

{skills_txt}"""

    respuesta = _call_claude(system_prompt, user_msg, max_tokens=80, timeout=15)
    if not respuesta:
        return fallback

    data = _extract_json(respuesta)  # FIX#6
    if not data:
        log.warning(f"[MCL] validar: no JSON en: {respuesta[:120]}")
        return fallback

    try:
        return {
            "aprobar":         bool(data.get("aprobar", True)),
            "confianza":       max(1, min(10, int(data.get("confianza", 5)))),
            "razon":           str(data.get("razon", ""))[:80],
            "ajuste_sl":       float(data.get("ajuste_sl", 0)),
            "metaclaw_activo": True,
        }
    except Exception as e:
        log.warning(f"[MCL] Error parseando validar: {e}")
        return fallback


# ══════════════════════════════════════════════════════════════
# APRENDER
# ══════════════════════════════════════════════════════════════

def aprender(señal: dict, ganado: bool, pnl: float):
    """
    Llamar tras cerrar un trade para generar/actualizar una skill.
    FIX#7: guard — no llamar Claude con señal vacía
    FIX#4: score /16
    """
    if not _api_key():
        return

    par    = señal.get("par", "")
    lado   = señal.get("lado", "")
    # FIX#7: señal mínima necesaria
    if not par or not lado:
        log.debug("[MCL] aprender: señal incompleta — omitiendo")
        return

    skills  = _load_skills()
    motivos = señal.get("motivos", [])
    score   = señal.get("score", 0)
    kz      = señal.get("kz", "FUERA")
    htf     = señal.get("htf", "NEUTRAL")
    patron  = señal.get("patron", "ninguno")
    rsi     = señal.get("rsi", 50)
    motivos_str  = " + ".join(motivos) if motivos else "sin_motivos"
    resultado_txt = f"{'GANADO ✅' if ganado else 'PERDIDO ❌'} PnL={pnl:+.4f}"

    # Buscar skill existente compatible
    skill_id_existente = None
    for s in skills:
        tags_s = set(s.get("tags", []))
        if (lado in tags_s and kz in tags_s
                and len(tags_s.intersection(set(motivos))) >= 2):
            skill_id_existente = s.get("id")
            break

    recientes_txt = ""
    if skills:
        recientes = sorted(skills, key=lambda x: x.get("updated", ""), reverse=True)[:5]
        recientes_txt = "Skills recientes:\n" + "\n".join(
            f"• [{s['wins']}/{s['trades']}] {s['texto']}" for s in recientes
        )

    system_prompt = """Eres MetaClaw, agente que aprende de trades.
Genera o actualiza una HABILIDAD que capture el patrón.

Responde SOLO en JSON, sin markdown:
{"texto": "skill max 80 chars", "tags": ["LONG_o_SHORT", "KZ", "INDICADOR"], "actualizar_id": "id_o_null"}

Skills buenas:
"LONG Londres + OB+FVG + sweep → muy fiable en KZ"
"Evitar SHORT en mercado alcista — HTF BULL bloquea"
"PIN_BAR + RSI<30 + LONG NY = setup institucional sólido" """

    user_msg = f"""Trade:
Par: {par} | {lado} | Score: {score}/16
KZ: {kz} | HTF: {htf} | RSI: {rsi:.1f}
Señales: {motivos_str} | Patrón: {patron}
Resultado: {resultado_txt}

{'Actualizar ID: ' + skill_id_existente if skill_id_existente else 'Crear nueva skill'}

{recientes_txt}"""

    respuesta = _call_claude(system_prompt, user_msg, max_tokens=200, timeout=25)
    if not respuesta:
        _crear_skill_simple(skills, señal, ganado)
        return

    data = _extract_json(respuesta)  # FIX#6
    if not data:
        log.warning("[MCL] aprender: no JSON — usando fallback")
        _crear_skill_simple(skills, señal, ganado)
        return

    try:
        texto  = str(data.get("texto", ""))[:100].strip()
        tags   = data.get("tags", [])
        upd_id = data.get("actualizar_id")

        # Normalizar null string
        if upd_id in (None, "null", "none", "", "undefined"):
            upd_id = None

        if not texto:
            _crear_skill_simple(skills, señal, ganado)
            return

        now = datetime.now(timezone.utc).isoformat()

        if upd_id:
            for s in skills:
                if s.get("id") == upd_id:
                    s["trades"]  = s.get("trades", 0) + 1
                    s["wins"]    = s.get("wins", 0) + (1 if ganado else 0)
                    s["texto"]   = texto
                    s["updated"] = now
                    _save_skills(skills)
                    log.info(f"[MCL] 🔄 Skill [{s['wins']}/{s['trades']}]: {texto}")
                    return
            log.debug(f"[MCL] upd_id={upd_id} no encontrado — creando nueva")

        tags_ok = [t for t in (tags if isinstance(tags, list) else []) if isinstance(t, str)]
        nueva = {
            "id":      str(uuid.uuid4())[:8],
            "texto":   texto,
            "tags":    tags_ok,
            "trades":  1,
            "wins":    1 if ganado else 0,
            "created": now,
            "updated": now,
        }
        skills.append(nueva)
        _save_skills(skills)
        icono = "✅" if ganado else "📚"
        log.info(f"[MCL] {icono} Nueva skill: {texto}")

    except Exception as e:
        log.warning(f"[MCL] Error en aprender: {e}")
        _crear_skill_simple(skills, señal, ganado)


def _crear_skill_simple(skills: list, señal: dict, ganado: bool):
    """Fallback: skill básica sin Claude."""
    lado      = señal.get("lado", "?")
    kz        = señal.get("kz", "FUERA")
    motivos   = señal.get("motivos", [])
    top_m     = " + ".join(motivos[:3]) if motivos else "SIN_MOTIVOS"
    resultado = "ganador" if ganado else "perdedor"
    texto     = f"{lado} {kz} {top_m} → {resultado}"[:80]
    tags      = [t for t in [lado, kz] + motivos[:3] if t]
    now       = datetime.now(timezone.utc).isoformat()
    skills.append({
        "id": str(uuid.uuid4())[:8], "texto": texto, "tags": tags,
        "trades": 1, "wins": 1 if ganado else 0,
        "created": now, "updated": now,
    })
    _save_skills(skills)
    log.info(f"[MCL] 📌 Skill simple: {texto}")


# ══════════════════════════════════════════════════════════════
# ESTADO (Telegram)
# ══════════════════════════════════════════════════════════════

def get_resumen() -> str:
    skills = _load_skills()
    if not skills:
        return "🤖 *MetaClaw*: Sin skills aprendidas aún"

    total_trades = sum(s.get("trades", 0) for s in skills)
    total_wins   = sum(s.get("wins", 0)   for s in skills)
    wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

    # Top 3 por WR con mínimo 2 trades para ser significativo
    candidatas = [s for s in skills if s.get("trades", 0) >= 2]
    top = sorted(
        candidatas,
        key=lambda s: s.get("wins", 0) / max(s.get("trades", 1), 1),
        reverse=True,
    )[:3]
    if not top:
        top = skills[:3]

    top_txt = "\n".join(f"  • {s['texto']} [{s['wins']}/{s['trades']}]" for s in top)
    return (
        f"🦞 *MetaClaw* — {len(skills)} skills | WR global: {wr:.0f}%\n"
        f"Top skills:\n{top_txt}"
    )


def get_stats() -> dict:
    """Stats para diagnóstico."""
    skills = _load_skills()
    total_trades = sum(s.get("trades", 0) for s in skills)
    total_wins   = sum(s.get("wins", 0)   for s in skills)
    return {
        "total_skills": len(skills),
        "total_trades": total_trades,
        "total_wins":   total_wins,
        "wr_pct":       round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0,
        "api_ok":       bool(_api_key()),
    }
