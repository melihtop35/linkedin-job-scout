@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo   LinkedIn Job Scout - Baslatiliyor...
echo ==================================================
echo.

:: Docker Desktop calisiyor mu kontrol et
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [HATA] Docker Desktop calismiyor veya erisilemiyor!
    echo Lutfen Docker Desktop uygulamasini acip hazir olmasini bekleyin ve tekrar deneyin.
    echo.
    pause
    exit /b 1
)

echo [1/3] Izole konteyner baslatiliyor...
docker compose up -d
if %errorlevel% neq 0 (
    echo [HATA] Konteyner baslatilirken bir sorun olustu.
    pause
    exit /b 1
)

echo [2/3] Arayuzlerin hazir olmasi icin 5 saniye bekleniyor...
timeout /t 5 /nobreak >nul

echo [3/3] Kontrol Paneli ve Canli Tarayici Ekrani tarayicida aciliyor...
start "" "http://localhost:7788"
start "" "http://localhost:6080"

echo.
echo [TAMAM] Sistem hazir!
echo - Web UI (Kontrol Paneli): http://localhost:7788
echo - noVNC (Canli Tarayici):  http://localhost:6080
echo.
pause
