"""
ENTRY POINT — Railway
Inicia SATY Elite v11 con BingX + Telegram
"""
import os, sys, time, logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RAILWAY] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("railway")

def sync_env():
    """Sincroniza nombres de variables entre bots y Railway."""
    pairs = [
        ("BINGX_API_SECRET", "BINGX_SECRET_KEY"),
        ("TELEGRAM_BOT_TOKEN", "TG_TOKEN"),
        ("TELEGRAM_CHAT_ID",   "TG_CHAT_ID"),
    ]
    for src, dst in pairs:
        val = os.environ.get(src, "")
        if val and not os.environ.get(dst):
            os.environ[dst] = val

    log.info("─── Variables ───────────────────────────────────────")
    for var in ["BINGX_API_KEY","BINGX_API_SECRET",
                "TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID",
                "FIXED_USDT","MAX_OPEN_TRADES","MIN_SCORE",
                "MAX_DRAWDOWN","BTC_FILTER","DAILY_LOSS_LIMIT"]:
        val = os.environ.get(var, "")
        if val:
            hide = "KEY" in var or "TOKEN" in var or "SECRET" in var
            display = f"***{val[-4:]}" if hide else val
            log.info(f"  OK  {var} = {display}")
        else:
            log.info(f"  --  {var} = (no configurada)")

    if not os.environ.get("BINGX_API_KEY") or not os.environ.get("BINGX_API_SECRET"):
        log.error("BINGX_API_KEY y BINGX_API_SECRET son OBLIGATORIAS.")
        log.error("Railway: tu proyecto → Variables → Add Variable")
        sys.exit(1)

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("  SATY ELITE v11 — Railway + BingX + Telegram")
    log.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 60)
    sync_env()
    log.info("Arrancando bot...")
    import saty_elite_v11
    saty_elite_v11.main()
