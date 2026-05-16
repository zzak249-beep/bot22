"""
Diagnóstico completo — ejecuta esto y manda el output a Telegram
python diagnostico.py
"""
import os, sys, json, time
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, ".")

from bingx_client import BingXClient

KEY    = os.environ.get("BINGX_API_KEY","")
SECRET = os.environ.get("BINGX_API_SECRET","")
TOKEN  = os.environ.get("TELEGRAM_TOKEN","")
CHAT   = os.environ.get("TELEGRAM_CHAT_ID","")

import requests

def tg(msg):
    if TOKEN and CHAT:
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT, "text": msg, "parse_mode":"HTML"}, timeout=10)
        except: pass
    print(msg)

# ── 1. Conexión BingX ─────────────────────────────────────────────────────
tg("🔍 <b>Diagnóstico Bot</b>\n\nPaso 1: Probando conexión BingX...")

try:
    client = BingXClient(KEY, SECRET, demo=False)
    bal    = client.get_balance()
    tg(f"✅ BingX conectado\nBalance raw: <code>{json.dumps(bal)[:300]}</code>")
except Exception as e:
    tg(f"❌ Error BingX: {e}")
    sys.exit(1)

# ── 2. Klines raw ─────────────────────────────────────────────────────────
tg("\nPaso 2: Obteniendo klines BTC-USDT...")
try:
    raw = client.get_klines("BTC-USDT", "3m", limit=10)
    tg(f"✅ Klines recibidos: {len(raw)} velas\nEjemplo: <code>{json.dumps(raw[0] if raw else 'vacío')[:300]}</code>")
except Exception as e:
    tg(f"❌ Error klines: {e}")
    sys.exit(1)

# ── 3. Parsear datos ──────────────────────────────────────────────────────
tg("\nPaso 3: Parseando datos...")
try:
    # Detectar formato automáticamente
    first = raw[0]
    if isinstance(first, (list, tuple)):
        tg(f"Formato: ARRAY [{len(first)} elementos]\nCampos: {first}")
        # BingX v3: [time, open, close, high, low, volume, amount]
        if len(first) >= 7:
            df = pd.DataFrame(raw, columns=["timestamp","open","close","high","low","volume","amount"])
        elif len(first) >= 6:
            df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    elif isinstance(first, dict):
        tg(f"Formato: DICT\nKeys: {list(first.keys())}")
        df = pd.DataFrame(raw)
        # Renombrar columnas comunes
        rename = {"time":"timestamp","t":"timestamp","o":"open","h":"high",
                  "l":"low","c":"close","v":"volume"}
        df = df.rename(columns={k:v for k,v in rename.items() if k in df.columns})
    else:
        tg(f"❌ Formato desconocido: {type(first)}")
        sys.exit(1)

    for col in ["open","high","low","close","volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.sort_values("timestamp").reset_index(drop=True)
    last = df.iloc[-1]
    tg(
        f"✅ DataFrame OK: {len(df)} filas\n"
        f"Columnas: {list(df.columns)}\n"
        f"Última vela:\n"
        f"  open={last.get('open','?'):.2f}\n"
        f"  high={last.get('high','?'):.2f}\n"
        f"  low={last.get('low','?'):.2f}\n"
        f"  close={last.get('close','?'):.2f}\n"
        f"  volume={last.get('volume','?'):.2f}"
    )
except Exception as e:
    tg(f"❌ Error parseando: {e}")
    sys.exit(1)

# ── 4. Strategy ───────────────────────────────────────────────────────────
tg("\nPaso 4: Probando señales EMA...")
try:
    raw100 = client.get_klines("BTC-USDT", "3m", limit=100)
    first  = raw100[0]

    if isinstance(first, (list, tuple)) and len(first) >= 7:
        df2 = pd.DataFrame(raw100, columns=["timestamp","open","close","high","low","volume","amount"])
    elif isinstance(first, (list, tuple)):
        df2 = pd.DataFrame(raw100, columns=["timestamp","open","high","low","close","volume"])
    else:
        df2 = pd.DataFrame(raw100)
        rename = {"time":"timestamp","o":"open","h":"high","l":"low","c":"close","v":"volume"}
        df2 = df2.rename(columns={k:v for k,v in rename.items() if k in df2.columns})

    for col in ["open","high","low","close","volume"]:
        if col in df2.columns:
            df2[col] = pd.to_numeric(df2[col], errors='coerce')
    df2["timestamp"] = pd.to_numeric(df2["timestamp"], errors='coerce')
    df2 = df2.sort_values("timestamp").reset_index(drop=True)

    # EMA manual
    close  = df2["close"]
    ema1   = close.ewm(span=2,  adjust=False).mean()
    ema2   = close.ewm(span=4,  adjust=False).mean()
    ema3   = close.ewm(span=20, adjust=False).mean()

    # Señales raw
    long_a  = (close.shift(1) >= ema3.shift(1)) & (close < ema3)
    long_b  = (close.diff()<0) & (ema1.diff()<0) & (close.shift(1)>=ema1.shift(1)) & (close<ema1) & (ema2.diff()>0)
    short_a = (close.shift(1) <= ema3.shift(1)) & (close > ema3)
    short_b = (close.diff()>0) & (ema1.diff()>0) & (close.shift(1)<=ema1.shift(1)) & (close>ema1) & (ema2.diff()<0)

    raw_longs  = int((long_a  | long_b).sum())
    raw_shorts = int((short_a | short_b).sum())
    last_close = float(close.iloc[-1])
    last_ema3  = float(ema3.iloc[-1])

    tg(
        f"✅ Estrategia EMA calculada\n"
        f"Velas procesadas: {len(df2)}\n"
        f"Señales LONG raw:  {raw_longs}\n"
        f"Señales SHORT raw: {raw_shorts}\n"
        f"Precio actual: {last_close:.2f}\n"
        f"EMA3 actual:   {last_ema3:.2f}"
    )
except Exception as e:
    tg(f"❌ Error señales: {e}")

# ── 5. Scanner ────────────────────────────────────────────────────────────
tg("\nPaso 5: Probando scanner...")
try:
    from scanner import MultiSymbolScanner
    scanner = MultiSymbolScanner(client, "3m", "15m", score_min=30, min_volume=1_000_000)
    results = scanner.scan(force=True)
    if results:
        tg(f"✅ Scanner: {len(results)} señales\n" + scanner.format_report(results[:3]))
    else:
        # Probar con filtros mínimos
        scanner2 = MultiSymbolScanner(client, "3m", "15m", score_min=0, min_volume=0)
        results2 = scanner2.scan(force=True)
        tg(
            f"⚠️ Scanner normal: 0 señales\n"
            f"Scanner sin filtros: {len(results2)} señales\n"
            f"→ Problema en filtros de score/volumen"
        )
except Exception as e:
    tg(f"❌ Error scanner: {e}")

tg("\n✅ <b>Diagnóstico completo</b> — revisa los mensajes anteriores")
