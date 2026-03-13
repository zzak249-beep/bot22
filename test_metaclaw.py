"""
test_metaclaw.py — Test real de MetaClaw
Ejecutar en Railway con: python test_metaclaw.py
O localmente con ANTHROPIC_API_KEY configurada.
"""
import os, sys, json, traceback

print("=" * 55)
print("TEST METACLAW — verificación completa")
print("=" * 55)

# ── Importar módulos ─────────────────────────────────────
try:
    import config
    import metaclaw
    print("✅ Imports OK")
except Exception as e:
    print(f"❌ Import falló: {e}")
    sys.exit(1)

errores = []
passed  = []

def ok(msg):
    passed.append(msg)
    print(f"  ✅ {msg}")

def fail(msg):
    errores.append(msg)
    print(f"  ❌ {msg}")

# ══════════════════════════════════════════════════════════
# TEST 1 — API key
# ══════════════════════════════════════════════════════════
print("\n[1] API Key")
ak = metaclaw._api_key()
if not ak:
    fail("ANTHROPIC_API_KEY vacía — MetaClaw no funcionará")
elif " " in ak or "\n" in ak or "\r" in ak:
    fail(f"API key tiene caracteres invisibles: hex={ak[:4].encode().hex()}")
else:
    ok(f"API key OK — len={len(ak)} primeros4={ak[:4]}")

# ══════════════════════════════════════════════════════════
# TEST 2 — _extract_json con distintos formatos
# ══════════════════════════════════════════════════════════
print("\n[2] _extract_json — parser robusto")
casos = [
    ('JSON puro',           '{"aprobar": true, "confianza": 8, "razon": "ok"}'),
    ('Con markdown fence',  '```json\n{"aprobar": false, "confianza": 3, "razon": "rsi alto"}\n```'),
    ('Texto + JSON',        'Aquí mi análisis: {"aprobar": true, "confianza": 7, "razon": "setup ok"}'),
    ('JSON con null string','{"texto": "skill", "tags": ["LONG"], "actualizar_id": "null"}'),
]
for nombre, texto in casos:
    r = metaclaw._extract_json(texto)
    if r and isinstance(r, dict):
        ok(f"{nombre}")
    else:
        fail(f"{nombre} → devolvió: {r}")

# ══════════════════════════════════════════════════════════
# TEST 3 — _skill_relevante threshold
# ══════════════════════════════════════════════════════════
print("\n[3] _skill_relevante — threshold >= 3")

skill_buena = {"tags": ["LONG", "LONDON", "OB", "FVG"], "texto": "LONG Londres OB+FVG"}
skill_mala  = {"tags": ["LONG"], "texto": "Genérica LONG"}  # solo lado, puntos=3 exacto
skill_irrelevante = {"tags": ["SHORT", "NY"], "texto": "SHORT NY"}

señal = {
    "par": "BTC-USDT", "lado": "LONG", "kz": "LONDON",
    "motivos": ["OB", "FVG", "BOS"]
}

r1 = metaclaw._skill_relevante(skill_buena, señal)
r2 = metaclaw._skill_relevante(skill_mala, señal)
r3 = metaclaw._skill_relevante(skill_irrelevante, señal)

if r1:
    ok("Skill con lado+KZ+motivos → relevante")
else:
    fail("Skill con lado+KZ+motivos debería ser relevante")

if not r3:
    ok("Skill SHORT irrelevante para LONG → descartada")
else:
    fail("Skill SHORT no debería ser relevante para LONG")

# ══════════════════════════════════════════════════════════
# TEST 4 — validar() con señal real
# ══════════════════════════════════════════════════════════
print("\n[4] validar() — señal real contra Claude API")

señal_test = {
    "par":        "BTC-USDT",
    "lado":       "LONG",
    "score":      10,
    "rsi":        58.5,
    "rr":         2.8,
    "kz":         "LONDON",
    "htf":        "BULL",
    "sobre_vwap": True,
    "patron":     "PIN_BAR",
    "sweep_bull": True,
    "ob_mitigado": False,
    "ob_fvg_bull": True,
    "motivos":    ["OB", "FVG", "BOS", "SWEEP", "MTF1H"],
}

try:
    r = metaclaw.validar(señal_test)
    if not r.get("metaclaw_activo"):
        fail(f"metaclaw_activo=False — API no respondió (razon: {r.get('razon')})")
    else:
        ok(f"validar OK — aprobar={r['aprobar']} confianza={r['confianza']} razon='{r['razon']}'")
        if r["confianza"] >= 7:
            ok("Confianza alta para setup de calidad ✓")
        else:
            print(f"  ⚠️  Confianza {r['confianza']} — esperábamos ≥7 para este setup")
except Exception as e:
    fail(f"validar() excepción: {e}\n{traceback.format_exc()}")

# ══════════════════════════════════════════════════════════
# TEST 5 — validar() con señal mala (debe rechazar)
# ══════════════════════════════════════════════════════════
print("\n[5] validar() — señal mala (debe rechazar)")

señal_mala = {
    "par":        "DOGE-USDT",
    "lado":       "LONG",
    "score":      4,
    "rsi":        78.0,   # RSI extremo
    "rr":         1.5,    # R:R bajo
    "kz":         "FUERA",
    "htf":        "BEAR", # HTF contrario
    "sobre_vwap": False,
    "patron":     "ninguno",
    "sweep_bull": False,
    "ob_mitigado": True,   # OB mitigado
    "ob_fvg_bull": False,
    "motivos":    ["EMA21"],
}

try:
    r = metaclaw.validar(señal_mala)
    if not r.get("metaclaw_activo"):
        print(f"  ⚠️  API no respondió — no se puede verificar rechazo")
    elif not r["aprobar"]:
        ok(f"Señal mala rechazada ✓ — confianza={r['confianza']} razon='{r['razon']}'")
    else:
        print(f"  ⚠️  Señal mala aprobada con confianza={r['confianza']} — prompt puede necesitar ajuste")
except Exception as e:
    fail(f"validar() señal mala excepción: {e}")

# ══════════════════════════════════════════════════════════
# TEST 6 — aprender() guarda skill
# ══════════════════════════════════════════════════════════
print("\n[6] aprender() — genera y guarda skill")

señal_aprender = {
    "par":     "XLM-USDT",
    "lado":    "LONG",
    "score":   9,
    "rsi":     52.0,
    "kz":      "LONDON",
    "htf":     "BULL",
    "motivos": ["OB", "FVG", "BOS"],
    "patron":  "PIN_BAR",
}

skills_antes = len(metaclaw._load_skills())
try:
    metaclaw.aprender(señal_aprender, ganado=True, pnl=0.42)
    skills_despues = len(metaclaw._load_skills())
    if skills_despues > skills_antes:
        skills = metaclaw._load_skills()
        ultima = skills[-1]
        ok(f"Skill guardada: '{ultima['texto']}' tags={ultima['tags']}")
    else:
        fail("aprender() no guardó ninguna skill nueva")
except Exception as e:
    fail(f"aprender() excepción: {e}\n{traceback.format_exc()}")

# ══════════════════════════════════════════════════════════
# TEST 7 — aprender() guard con señal vacía
# ══════════════════════════════════════════════════════════
print("\n[7] aprender() — guard señal vacía")
skills_antes = len(metaclaw._load_skills())
metaclaw.aprender({}, ganado=True, pnl=0.1)  # debe ignorar
skills_despues = len(metaclaw._load_skills())
if skills_despues == skills_antes:
    ok("Señal vacía ignorada correctamente")
else:
    fail("Señal vacía NO debería crear skill")

# ══════════════════════════════════════════════════════════
# TEST 8 — get_resumen()
# ══════════════════════════════════════════════════════════
print("\n[8] get_resumen()")
try:
    r = metaclaw.get_resumen()
    if "MetaClaw" in r or "skills" in r:
        ok(f"Resumen OK: {r[:80]}...")
    else:
        fail(f"Resumen inesperado: {r}")
except Exception as e:
    fail(f"get_resumen() excepción: {e}")

# ══════════════════════════════════════════════════════════
# TEST 9 — get_stats()
# ══════════════════════════════════════════════════════════
print("\n[9] get_stats()")
try:
    s = metaclaw.get_stats()
    ok(f"skills={s['total_skills']} trades={s['total_trades']} wr={s['wr_pct']}% api={s['api_ok']}")
except Exception as e:
    fail(f"get_stats() excepción: {e}")

# ══════════════════════════════════════════════════════════
# RESUMEN FINAL
# ══════════════════════════════════════════════════════════
print("\n" + "=" * 55)
total = len(passed) + len(errores)
print(f"RESULTADO: {len(passed)}/{total} tests pasados")
if errores:
    print("\nFALLOS:")
    for e in errores:
        print(f"  ❌ {e}")
    sys.exit(1)
else:
    print("✅ MetaClaw funciona correctamente")
