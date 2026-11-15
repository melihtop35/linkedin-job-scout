@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo   LinkedIn Job Scout - Durduruluyor...
echo ==================================================
echo.

docker compose down
if %errorlevel% neq 0 (
    echo [HATA] Konteyner durdurulurken bir sorun olustu.
    pause
    exit /b 1
)

echo.
echo [TAMAM] Konteyner guvenli bir sekilde durduruldu.
echo Kaydedilen oturum cerezleriniz ve is ilanlariniz "data" klasorunde korundu.
echo.
pause
