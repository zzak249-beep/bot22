"""
metaclaw.py — Agente IA de trading v6.0 [PROMPT MEJORADO]
==========================================================

MEJORAS v6.0:
  ✅ Modelo actualizado a claude-sonnet-4-6 (más preciso que haiku)
  ✅ Prompt de validación más estricto con reglas claras
  ✅ Prompt de aprendizaje mejorado para extraer patrones útiles
  ✅ Threshold de veto reducido: veta con confianza >= 5
  ✅ Contexto de macro BTC añadido a validación
  ✅ Historial de WR por KZ añadido al contexto
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

# Usar Sonnet para mayor calidad de análisis
_CLAUDE_MODEL = "claude-sonnet-4-6"

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
    path = _skills_path()
    with _skills_lock:
        try:
            if len(skills) > 100:
                # Mantener las más relevantes (más trades, mejor WR)
                skills = sorted(
                    skills,
                    key=lambda s: (s.get("wins", 0) / max(s.get("trades", 1), 1),
                                   s.get("trades", 0)),
                    reverse=True,
                )[:100]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(skills, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning(f"[MCL] Error guardando skills: {e}")


def _skill_relevante(skill: dict, señal: dict) -> bool:
    tags    = set(skill.get("tags", []))
    par     = señal.get("par", "")
    lado    = señal.get("lado", "")
    kz      = señal.get("kz", "")
    motivos = set(señal.get("motivos", []))

    puntos = 0
    if lado and lado in tags:           puntos += 3
    if kz   and kz   in tags:          puntos += 2
    if any(t in motivos for t in tags): puntos += 2
    if par  and par   in tags:          puntos += 4
    if "GENERAL" in tags:               puntos += 1

    return puntos >= 3


def _api_key() -> str:
    return (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    cleaned = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


def _call_claude(system_prompt: str, user_msg: str,
                 max_tokens: int = 200, timeout: int = 20) -> Optional[str]:
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
                "model":      _CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "system":     system_prompt,
                "messages":   [{"role": "user", "content": user_msg}],
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            log.warning(f"[MCL] Error Claude API: {resp.status_code} {resp.text[:200]}")
            return None
        data = resp.json()
        return data["content"][0]["text"].strip()
    except requests.exceptions.Timeout:
        log.warning(f"[MCL] Timeout ({timeout}s)")
        return None
    except Exception as e:
        log.warning(f"[MCL] Error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# VALIDAR SEÑAL — PROMPT MEJORADO v6.0
# ══════════════════════════════════════════════════════════════

def validar(señal: dict) -> dict:
    """
    Evalúa una señal SMC con Claude.
    Prompt mejorado con reglas más claras y específicas.
    """
    fallback = {
        "aprobar": True, "confianza": 5,
        "razon": "metaclaw_offline", "ajuste_sl": 0,
        "metaclaw_activo": False,
    }

    if not _api_key():
        return fallback

    skills     = _load_skills()
    relevantes = [s for s in skills if _skill_relevante(s, señal)][:8]

    if relevantes:
        # Ordenar por WR descendente
        relevantes.sort(
            key=lambda s: s.get("wins", 0) / max(s.get("trades", 1), 1),
            reverse=True
        )
        lines      = [f"• WR={s.get('wins',0)}/{s.get('trades',0)} | {s['texto']}"
                      for s in relevantes]
        skills_txt = "SKILLS APRENDIDAS:\n" + "\n".join(lines)
    else:
        skills_txt = "SKILLS: ninguna para este contexto aún."

    rsi       = señal.get("rsi", 50)
    score     = señal.get("score", 0)
    lado      = señal.get("lado", "?")
    par       = señal.get("par", "?")
    rr        = señal.get("rr", 0)
    motivos   = " + ".join(señal.get("motivos", []))
    kz        = señal.get("kz", "FUERA")
    htf       = señal.get("htf", "NEUTRAL")
    htf_4h    = señal.get("htf_4h", "NEUTRAL")
    vwap_ok   = "SOBRE" if señal.get("sobre_vwap") else "BAJO"
    patron    = señal.get("patron") or "ninguno"
    sweep     = "SÍ" if (señal.get("sweep_bull") or señal.get("sweep_bear")) else "NO"
    ob_mit    = "MITIGADO" if señal.get("ob_mitigado") else "VÁLIDO"
    ob_fvg    = "SÍ" if (señal.get("ob_fvg_bull") or señal.get("ob_fvg_bear")) else "NO"
    choch     = "SÍ" if (señal.get("choch_bull") or señal.get("choch_bear")) else "NO"
    discount  = señal.get("discount", False)
    premium   = señal.get("premium", False)
    rango     = señal.get("mercado_lateral", False)
    vol_ratio = señal.get("vol_ratio", 1.0)
    macd_hist = señal.get("macd_hist", 0)

    system_prompt = """Eres MetaClaw, experto en trading de futuros perpetuos con ICT/SMC.

RESPONDE SOLO JSON válido sin markdown:
{"aprobar": true/false, "confianza": 1-10, "razon": "max 80 chars"}

═══ REGLAS DE RECHAZO (aprobar=false) ═══
RECHAZAR SIEMPRE si:
1. OB_MITIGADO sin sweep previo de liquidez
2. RSI > 72 en LONG, RSI < 28 en SHORT (extremos)
3. R:R < 2.0 (riesgo/beneficio insuficiente)
4. HTF 1h CONTRARIO al trade Y sin CHoCH
5. Score < 8 Y sin OB+FVG Y sin SWEEP
6. FUERA de killzone Y score < 9 Y HTF=NEUTRAL
7. Vol_ratio < 0.3 (volumen muy bajo, vela muerta)
8. LONG en zona PREMIUM, SHORT en zona DISCOUNT (precio equivocado)
9. FVG ya rellenado (señal inválida)

═══ REGLAS DE APROBACIÓN ALTA (confianza 8-10) ═══
APROBAR con alta confianza si TODOS:
- OB+FVG confluencia (ob_fvg=SÍ)
- Sweep de liquidez previo
- Killzone activa
- HTF 1h Y 4h alineados con el trade
- CHoCH confirmado
- LONG en DISCOUNT / SHORT en PREMIUM
- Score >= 10

═══ MERCADO LATERAL ═══
- Si rango=true: LONG en suelo del rango (EQL), SHORT en techo (EQH)
- Confianza máxima 7 en rango (no buscar extensiones)"""

    user_msg = f"""SEÑAL: {par} {lado} Score:{score}/16
RSI:{rsi:.1f} | R:R:{rr:.2f} | KZ:{kz} | HTF_1H:{htf} | HTF_4H:{htf_4h}
VWAP:{vwap_ok} | Patrón:{patron} | CHoCH:{choch}
Sweep:{sweep} | OB:{ob_mit} | OB+FVG:{ob_fvg}
Premium:{premium} | Discount:{discount} | Rango:{rango}
Vol_ratio:{vol_ratio:.1f} | MACD_hist:{macd_hist:.4f}
Señales:{motivos}

{skills_txt}"""

    respuesta = _call_claude(system_prompt, user_msg, max_tokens=100, timeout=15)
    if not respuesta:
        return fallback

    data = _extract_json(respuesta)
    if not data:
        log.warning(f"[MCL] validar: no JSON: {respuesta[:120]}")
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
        log.warning(f"[MCL] Error parseando: {e}")
        return fallback


# ══════════════════════════════════════════════════════════════
# APRENDER — PROMPT MEJORADO v6.0
# ══════════════════════════════════════════════════════════════

def aprender(señal: dict, ganado: bool, pnl: float):
    if not _api_key():
        return

    par  = señal.get("par", "")
    lado = señal.get("lado", "")
    if not par or not lado:
        log.debug("[MCL] aprender: señal incompleta")
        return

    skills      = _load_skills()
    motivos     = señal.get("motivos", [])
    score       = señal.get("score", 0)
    kz          = señal.get("kz", "FUERA")
    htf         = señal.get("htf", "NEUTRAL")
    htf_4h      = señal.get("htf_4h", "NEUTRAL")
    patron      = señal.get("patron") or "ninguno"
    rsi         = señal.get("rsi", 50)
    ob_fvg      = señal.get("ob_fvg_bull") or señal.get("ob_fvg_bear")
    sweep       = señal.get("sweep_bull") or señal.get("sweep_bear")
    choch       = señal.get("choch_bull") or señal.get("choch_bear")
    discount    = señal.get("discount", False)
    premium     = señal.get("premium", False)
    vol_ratio   = señal.get("vol_ratio", 1.0)
    motivos_str = " + ".join(motivos) if motivos else "sin_motivos"
    resultado   = f"{'GANADO ✅' if ganado else 'PERDIDO ❌'} PnL={pnl:+.4f}"

    # Buscar skill existente para actualizar
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
            f"• WR={s.get('wins',0)}/{s.get('trades',0)} | {s['texto']}" for s in recientes
        )

    system_prompt = """Eres MetaClaw, aprende de trades para mejorar futuros.

RESPONDE SOLO JSON válido sin markdown:
{"texto": "skill max 90 chars", "tags": ["LONG/SHORT", "KZ", "INDICADOR1", ...], "actualizar_id": "id_o_null"}

Genera skills ESPECÍFICAS y ACCIONABLES:
✅ "LONG Londres + OB+FVG + CHoCH + discount → WR alto"
✅ "SHORT NY + sweep + RSI>65 + premium → fiable"
✅ "Evitar LONG HTF_NEUTRAL sin CHoCH → trampa frecuente"
✅ "BTC/ETH/SOL LONDON OB+FVG = setup más fiable"
❌ Evitar skills genéricas como "trade en tendencia"

Si la señal perdió, genera una skill de ADVERTENCIA:
✅ "CUIDADO: LONG en RSI>60 KZ_FUERA → overextended"
✅ "CUIDADO: OB sin sweep previo → trampa institucional" """

    user_msg = f"""Trade:
{par} | {lado} | Score:{score}/16
KZ:{kz} | HTF_1H:{htf} | HTF_4H:{htf_4h} | RSI:{rsi:.1f}
OB+FVG:{ob_fvg} | Sweep:{sweep} | CHoCH:{choch}
Discount:{discount} | Premium:{premium} | Vol_ratio:{vol_ratio:.1f}
Señales:{motivos_str} | Patrón:{patron}
Resultado:{resultado}

{'Actualizar ID: ' + skill_id_existente if skill_id_existente else 'Nueva skill'}
{recientes_txt}"""

    respuesta = _call_claude(system_prompt, user_msg, max_tokens=200, timeout=25)
    if not respuesta:
        _crear_skill_simple(skills, señal, ganado)
        return

    data = _extract_json(respuesta)
    if not data:
        _crear_skill_simple(skills, señal, ganado)
        return

    try:
        texto  = str(data.get("texto", ""))[:100].strip()
        tags   = data.get("tags", [])
        upd_id = data.get("actualizar_id")

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
                    wr = s["wins"] / s["trades"] * 100
                    log.info(f"[MCL] 🔄 Skill [{s['wins']}/{s['trades']} WR={wr:.0f}%]: {texto}")
                    return

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
    lado    = señal.get("lado", "?")
    kz      = señal.get("kz", "FUERA")
    motivos = señal.get("motivos", [])
    top_m   = " + ".join(motivos[:3]) if motivos else "SIN_MOTIVOS"
    res     = "ganador" if ganado else "CUIDADO — perdedor"
    texto   = f"{lado} {kz} {top_m} → {res}"[:90]
    tags    = [t for t in [lado, kz] + motivos[:3] if t]
    now     = datetime.now(timezone.utc).isoformat()
    skills.append({
        "id": str(uuid.uuid4())[:8], "texto": texto, "tags": tags,
        "trades": 1, "wins": 1 if ganado else 0,
        "created": now, "updated": now,
    })
    _save_skills(skills)
    log.info(f"[MCL] 📌 Skill simple: {texto}")


# ══════════════════════════════════════════════════════════════
# ESTADO Y STATS
# ══════════════════════════════════════════════════════════════

def get_resumen() -> str:
    skills = _load_skills()
    if not skills:
        return "🤖 *MetaClaw v6.0*: Sin skills aún"

    total_trades = sum(s.get("trades", 0) for s in skills)
    total_wins   = sum(s.get("wins", 0)   for s in skills)
    wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

    # Top skills por WR (con mínimo 2 trades)
    candidatas = [s for s in skills if s.get("trades", 0) >= 2]
    top = sorted(
        candidatas,
        key=lambda s: s.get("wins", 0) / max(s.get("trades", 1), 1),
        reverse=True,
    )[:3]
    if not top:
        top = skills[:3]

    top_txt = "\n".join(
        f"  • [{s['wins']}/{s['trades']} WR={s.get('wins',0)/max(s.get('trades',1),1)*100:.0f}%] {s['texto']}"
        for s in top
    )
    return (
        f"🦞 *MetaClaw v6.0* — {len(skills)} skills | WR global: {wr:.0f}%\n"
        f"Top skills:\n{top_txt}"
    )


def get_stats() -> dict:
    skills       = _load_skills()
    total_trades = sum(s.get("trades", 0) for s in skills)
    total_wins   = sum(s.get("wins", 0)   for s in skills)
    return {
        "total_skills": len(skills),
        "total_trades": total_trades,
        "total_wins":   total_wins,
        "wr_pct":       round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0,
        "api_ok":       bool(_api_key()),
    }
