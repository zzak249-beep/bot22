# ═══════════════════════════════════════════════════════
# deploy.ps1 — Copia los archivos del bot y sube a GitHub
# Ejecutar desde PowerShell en la carpeta del proyecto
# ═══════════════════════════════════════════════════════

Write-Host "`n=== BB+RSI ELITE — Deploy a Railway ===" -ForegroundColor Cyan

# 1. Verificar que estamos en la carpeta correcta
if (-not (Test-Path "main.py")) {
    Write-Host "`n❌ ERROR: No encuentro main.py en esta carpeta" -ForegroundColor Red
    Write-Host "   Navega a la carpeta del proyecto con:" -ForegroundColor Yellow
    Write-Host "   cd C:\ruta\a\tu\proyecto" -ForegroundColor Yellow
    exit 1
}

Write-Host "✅ Carpeta del proyecto: $(Get-Location)" -ForegroundColor Green

# 2. Verificar git
try {
    $branch = git branch --show-current 2>&1
    Write-Host "✅ Git OK — rama: $branch" -ForegroundColor Green
} catch {
    Write-Host "❌ Git no encontrado. Instala Git desde https://git-scm.com" -ForegroundColor Red
    exit 1
}

# 3. Ver estado actual
Write-Host "`n--- Archivos modificados ---" -ForegroundColor Cyan
git status --short

# 4. Añadir TODOS los archivos .py y de config
Write-Host "`n--- Añadiendo archivos ---" -ForegroundColor Cyan
git add main.py config.py strategy.py trader.py
git add data_feed.py indicators.py bingx_api.py
git add risk_manager.py telegram_notifier.py liquidity.py
git add dashboard.py backtest_final.py reset_state.py
git add Dockerfile railway.json requirements.txt README.md
git add test_bingx.py test_telegram.py

git status --short

# 5. Commit
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm"
git commit -m "fix: all files + crash-proof imports + liquidity integration [$timestamp]"

# 6. Push
Write-Host "`n--- Subiendo a GitHub ---" -ForegroundColor Cyan
git push

Write-Host "`n✅ LISTO — Railway redesplegará en ~1 minuto" -ForegroundColor Green
Write-Host "   Mira los logs en: https://railway.app" -ForegroundColor Cyan
