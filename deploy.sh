#!/bin/bash
# ============================================================
# DEPLOY bot v8.2 → GitHub → Railway (auto-redeploy)
# Uso: bash deploy.sh
# ============================================================
set -e

REPO="zzak249-beep/bot22"
BOT_FILE="bot_v82.py"
TARGET="app.py"

echo "🚀 Deploy bot v8.2..."

# 1. Clonar repo
if [ ! -d "bot22" ]; then
  git clone https://github.com/$REPO.git
fi
cd bot22

# 2. Copiar el nuevo bot
cp "../$BOT_FILE" "$TARGET"

# 3. Commit y push
git config user.email "deploy@bot82" 2>/dev/null || true
git config user.name "Bot Deploy" 2>/dev/null || true
git add $TARGET
git commit -m "🤖 v8.2 profitability: trailing dinámico, SL bonus, candle filter, divergencia RSI, BE inmediato, TP0, time-exit"
git push origin main

echo ""
echo "✅ Pusheado. Railway redesplegará en ~30 segundos."
echo "   Comprueba: https://railway.com/project/9cf385d5-4679-4a84-a6c3-0026cec729f6"
echo ""
echo "📱 Cuando arranque, envía /reset al bot de Telegram para limpiar el opt_score heredado."
