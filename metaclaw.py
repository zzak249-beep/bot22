"""
metaclaw.py — Agente IA de trading con habilidades evolutivas
Inspirado en MetaClaw framework — skills que aprenden y EVOLUCIONAN

Arquitectura:
  1. analizar_par() genera señal SMC v5.0
  2. metaclaw.validar(señal) → Claude evalúa con skills activas → APROBAR/RECHAZAR
  3. Al cerrar trade → metaclaw.aprender(señal, resultado) → genera/actualiza skill
  4. Las skills se persisten en JSON y mejoran con cada trade

Skills = reglas cortas aprendidas de la experiencia:
  "SHORT BTC London + RSI>68 + OB_bear → 3/4 trades ganados (75%)"
  "Evitar LONG en alts fuera de KZ si score < 7 — 0/3 últimos"
  "PIN_BAR + EQL + LONDON = setup de alta precisión (5/6 trades)"
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests

import config

log = logging.getLogger("metaclaw")

# ══════════════════════════════════════════════════════════════
# PERSISTENCIA
# ══════════════════════════════════════════════════════════════

def _skills_path() -> str:
    base = config.MEMORY_DIR or ""
    return os.path.join(base, "metaclaw_skills.json") if base else "metaclaw_skills.json"

def _load_skills() -> list:
    path = _skills_path()
    try:
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception as e:
        log.warning(f"[MCL] Error cargando skills: {e}")
    return []

def _save_skills(skills: list):
    path = _skills_path()
    try:
        # Mantener máx 80 skills — eliminar las menos útiles
        if len(skills) > 80:
            skills = sorted(skills, key=lambda s: (s.get("trades", 0), s.get("updated", "")), reverse=True)[:80]
        with open(path, "w") as f:
            json.dump(skills, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning(f"[MCL] Error guardando skills: {e}")

def _skill_relevante(skill: dict, señal: dict) -> bool:
    """Determina si una skill es relevante para esta señal."""
    tags = skill.get("tags", [])
    par  = señal.get("par", "")
    lado = señal.get("lado", "")
    kz   = señal.get("kz", "")
    motivos = set(señal.get("motivos", []))

    puntos = 0
    if lado in tags:                         puntos += 3
    if kz  in tags:                          puntos += 2
    if any(t in motivos for t in tags):      puntos += 2
    if par in tags:                          puntos += 4
    # Skills generales también aplican
    if "GENERAL" in tags:                    puntos += 1
    return puntos >= 1


# ══════════════════════════════════════════════════════════════
# LLAMADA A CLAUDE API
# ══════════════════════════════════════════════════════════════

# Flag de sesión: True si Anthropic devuelve error de créditos insuficientes
_sin_creditos: bool = False


def _call_claude(system_prompt: str, user_msg: str, max_tokens: int = 500) -> Optional[str]:
    global _sin_creditos  # declarar global AL INICIO de la función
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.debug("[MCL] ANTHROPIC_API_KEY no configurada — saltando MetaClaw")
        return None
    if _sin_creditos:
        return None  # Sin créditos — no reintentar hasta reinicio del bot

    # Modelos en orden de preferencia (más barato primero)
    for model in ("claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-sonnet-4-6"):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key":         api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":      model,
                    "max_tokens": max_tokens,
                    "system":     system_prompt,
                    "messages":   [{"role": "user", "content": user_msg}],
                },
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["content"][0]["text"].strip()
            elif resp.status_code == 400:
                err_txt = resp.text
                log.warning(f"[MCL] 400 con {model}: {err_txt[:200]} — intentando siguiente")
                if "credit balance" in err_txt.lower() or "billing" in err_txt.lower():
                    _sin_creditos = True
                    log.warning("[MCL] ⚠️  Sin créditos Anthropic. Recarga en console.anthropic.com/settings/billing")
                    return None  # No reintentar otros modelos
                continue
            else:
                resp.raise_for_status()
        except requests.exceptions.Timeout:
            log.warning(f"[MCL] Timeout con {model}")
            continue
        except Exception as e:
            log.warning(f"[MCL] Error con {model}: {e}")
            continue
    log.warning("[MCL] Todos los modelos fallaron")
    return None


# ══════════════════════════════════════════════════════════════
# VALIDAR SEÑAL — El agente decide si ejecutar o no
# ══════════════════════════════════════════════════════════════

def validar(señal: dict) -> dict:
    """
    Evalúa una señal SMC usando Claude + skills aprendidas.
    
    Returns:
        {
          "aprobar": bool,       # True = ejecutar, False = rechazar
          "confianza": int,      # 1-10
          "razon": str,          # Explicación corta
          "ajuste_sl": float,    # 0 = sin cambio, >0 = nuevo SL sugerido
          "metaclaw_activo": bool  # False si API no disponible
        }
    """
    # Default: aprobar si MetaClaw no puede decidir (fail-open)
    fallback = {"aprobar": True, "confianza": 5, "razon": "metaclaw_offline", "ajuste_sl": 0, "metaclaw_activo": False}

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return fallback

    skills     = _load_skills()
    relevantes = [s for s in skills if _skill_relevante(s, señal)][:6]

    skills_txt = ""
    if relevantes:
        lines = []
        for s in relevantes:
            wr = f"{s['wins']}/{s['trades']}" if s.get("trades", 0) > 0 else "nuevo"
            lines.append(f"• [{wr}] {s['texto']}")
        skills_txt = "HABILIDADES APRENDIDAS RELEVANTES:\n" + "\n".join(lines)
    else:
        skills_txt = "HABILIDADES: Ninguna aprendida aún para este tipo de señal."

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

    system_prompt = """Eres MetaClaw, un agente experto en trading de futuros perpetuos de criptomonedas.
Tu estrategia base es ICT/SMC (Smart Money Concepts): FVG, Order Blocks, Liquidity Sweeps, BOS/CHoCH.
Evalúas señales de trading y decides si aprobarlas o rechazarlas basándote en:
1. La calidad técnica de la señal
2. Las habilidades aprendidas de trades anteriores
3. El contexto de mercado actual

Responde SIEMPRE en este formato JSON exacto, sin texto extra:
{"aprobar": true/false, "confianza": 1-10, "razon": "texto corto max 60 chars"}

Criterios para rechazar:
- RSI extremo sin divergencia (>75 LONG, <25 SHORT)  
- OB mitigado SIN sweep de liquidez previo
- Fuera de killzone con score bajo (<6) y HTF en contra
- Patrón débil (BULL_STRONG/BEAR_STRONG) sin confirmación adicional
- R:R menor a 2.0

Criterios para aprobar con confianza alta (8-10):
- Sweep + OB+FVG + killzone activa
- Pin Bar o Engulfing en zona premium/descuento
- HTF alineado + score ≥ 9
- Habilidades anteriores muestran setup ganador"""

    user_msg = f"""SEÑAL A EVALUAR:
Par: {par} | Dirección: {lado} | Score: {score}/14
RSI: {rsi} | R:R: {rr} | KZ: {kz} | HTF: {htf}
Precio vs VWAP: {vwap_ok} | Patrón: {patron}
Sweep liquidez: {sweep} | Order Block: {ob_mit}
Señales activas: {motivos}

{skills_txt}

¿Aprobar esta operación?"""

    respuesta = _call_claude(system_prompt, user_msg, max_tokens=120)

    if not respuesta:
        return fallback

    try:
        # Extraer JSON de la respuesta
        import re
        match = re.search(r'\{[^}]+\}', respuesta, re.DOTALL)
        if not match:
            raise ValueError("No JSON found")
        data = json.loads(match.group())
        return {
            "aprobar":          bool(data.get("aprobar", True)),
            "confianza":        int(data.get("confianza", 5)),
            "razon":            str(data.get("razon", ""))[:80],
            "ajuste_sl":        float(data.get("ajuste_sl", 0)),
            "metaclaw_activo":  True,
        }
    except Exception as e:
        log.warning(f"[MCL] Error parseando respuesta: {e} | resp: {respuesta[:100]}")
        return fallback


# ══════════════════════════════════════════════════════════════
# APRENDER — Generar/actualizar skill tras resultado
# ══════════════════════════════════════════════════════════════

def aprender(señal: dict, ganado: bool, pnl: float):
    """
    Llamar después de cerrar un trade.
    Claude analiza qué pasó y genera/actualiza una skill.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return

    skills = _load_skills()
    resultado_txt = f"{'GANADO ✅' if ganado else 'PERDIDO ❌'} PnL={pnl:+.4f}"
    
    par     = señal.get("par", "?")
    lado    = señal.get("lado", "?")
    score   = señal.get("score", 0)
    kz      = señal.get("kz", "FUERA")
    htf     = señal.get("htf", "NEUTRAL")
    motivos = " + ".join(señal.get("motivos", []))
    patron  = señal.get("patron", "ninguno")
    rsi     = señal.get("rsi", 50)

    # Buscar skill existente que coincida con este setup
    skill_id_existente = None
    for s in skills:
        tags = set(s.get("tags", []))
        motivos_set = set(señal.get("motivos", []))
        # Match si comparte lado + KZ + al menos 2 motivos
        if (lado in tags and kz in tags and
                len(tags.intersection(motivos_set)) >= 2):
            skill_id_existente = s.get("id")
            break

    skills_txt = ""
    if skills:
        recientes = skills[-5:]
        skills_txt = "Skills existentes recientes:\n" + "\n".join(
            f"• {s['texto']} [{s['wins']}/{s['trades']}]" for s in recientes
        )

    system_prompt = """Eres MetaClaw, agente de trading que aprende de sus trades.
Tras cada trade, generas o actualizas una HABILIDAD (skill) concisa que capture el patrón aprendido.

Una skill es una regla de trading aprendida. Debe ser:
- Concisa (máx 80 chars)
- Accionable (dice qué hacer o evitar)
- Específica (menciona los indicadores clave)

Responde en JSON exacto:
{"texto": "la skill en español max 80 chars", "tags": ["LONG"/"SHORT", "KZ_nombre", "INDICADOR1", "INDICADOR2"], "actualizar_id": "id_si_existe_o_null"}

Ejemplos de skills buenas:
"SHORT Londres + PIN_BAR + OB_bear + RSI>65 = alta precisión"  
"Evitar LONG fuera KZ en altcoins con score<7 — baja tasa éxito"
"SWEEP_bull + FVG + London = entrada institucional muy fiable" """

    user_msg = f"""Trade cerrado:
Par: {par} | {lado} | Score: {score}/14
Sesión: {kz} | HTF: {htf} | RSI: {rsi}
Señales: {motivos}
Patrón: {patron}
Resultado: {resultado_txt}

{'Skill a actualizar ID: ' + skill_id_existente if skill_id_existente else 'Skill existente: ninguna — crear nueva'}

{skills_txt}

Genera la skill aprendida de este trade:"""

    respuesta = _call_claude(system_prompt, user_msg, max_tokens=200)

    if not respuesta:
        # Crear skill simple sin Claude si API falla
        _crear_skill_simple(skills, señal, ganado)
        return

    try:
        import re
        match = re.search(r'\{[^}]+\}', respuesta, re.DOTALL)
        if not match:
            raise ValueError("No JSON")
        data     = json.loads(match.group())
        texto    = str(data.get("texto", ""))[:100]
        tags     = data.get("tags", [])
        upd_id   = data.get("actualizar_id")

        if not texto:
            _crear_skill_simple(skills, señal, ganado)
            return

        now = datetime.now(timezone.utc).isoformat()

        if upd_id:
            for s in skills:
                if s.get("id") == upd_id:
                    s["trades"] = s.get("trades", 0) + 1
                    s["wins"]   = s.get("wins", 0) + (1 if ganado else 0)
                    s["texto"]  = texto  # Claude actualiza el texto
                    s["updated"] = now
                    log.info(f"[MCL] Skill actualizada: {texto}")
                    _save_skills(skills)
                    return

        # Crear nueva skill
        nueva = {
            "id":      str(uuid.uuid4())[:8],
            "texto":   texto,
            "tags":    tags if isinstance(tags, list) else [],
            "trades":  1,
            "wins":    1 if ganado else 0,
            "created": now,
            "updated": now,
        }
        skills.append(nueva)
        _save_skills(skills)
        log.info(f"[MCL] ✨ Nueva skill: {texto}")

    except Exception as e:
        log.warning(f"[MCL] Error creando skill: {e}")
        _crear_skill_simple(skills, señal, ganado)


def _crear_skill_simple(skills: list, señal: dict, ganado: bool):
    """Fallback: crear skill básica sin Claude."""
    lado    = señal.get("lado", "?")
    kz      = señal.get("kz", "FUERA")
    motivos = señal.get("motivos", [])
    top_m   = " + ".join(motivos[:3]) if motivos else "SIN_MOTIVOS"
    resultado = "ganador" if ganado else "perdedor"
    texto   = f"{lado} {kz} {top_m} → {resultado}"[:80]
    tags    = [lado, kz] + motivos[:3]
    now     = datetime.now(timezone.utc).isoformat()
    skills.append({
        "id": str(uuid.uuid4())[:8], "texto": texto, "tags": tags,
        "trades": 1, "wins": 1 if ganado else 0, "created": now, "updated": now,
    })
    _save_skills(skills)


# ══════════════════════════════════════════════════════════════
# ESTADO DEL AGENTE (para Telegram)
# ══════════════════════════════════════════════════════════════

def get_resumen() -> str:
    skills = _load_skills()
    if not skills:
        return "🤖 *MetaClaw*: Sin skills aprendidas aún"
    total_trades = sum(s.get("trades", 0) for s in skills)
    total_wins   = sum(s.get("wins", 0)   for s in skills)
    wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
    top = sorted(skills, key=lambda s: s.get("wins", 0) / max(s.get("trades", 1), 1), reverse=True)[:3]
    top_txt = "\n".join(f"  • {s['texto']} [{s['wins']}/{s['trades']}]" for s in top)
    return (
        f"🦞 *MetaClaw* — {len(skills)} skills | WR global: {wr:.0f}%\n"
        f"Top skills:\n{top_txt}"
    )
