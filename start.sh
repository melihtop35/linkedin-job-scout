#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=================================================="
echo "  LinkedIn Job Scout - Başlatılıyor..."
echo "=================================================="

if ! docker info >/dev/null 2>&1; then
  echo "[HATA] Docker çalışmıyor veya erişilemiyor!"
  echo "Lütfen Docker Desktop / Docker servisini başlatıp tekrar deneyin."
  exit 1
fi

echo "[1/3] İzole konteyner başlatılıyor..."
docker compose up -d

echo "[2/3] Arayüzlerin hazır olması için 5 saniye bekleniyor..."
sleep 5

echo "[3/3] Tarayıcı sekmeleri açılıyor..."
if command -v open >/dev/null 2>&1; then
  open "http://localhost:7788" || true
  open "http://localhost:6080" || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:7788" >/dev/null 2>&1 || true
  xdg-open "http://localhost:6080" >/dev/null 2>&1 || true
fi

echo ""
echo "[TAMAM] Sistem hazır!"
echo "- Web UI (Kontrol Paneli): http://localhost:7788"
echo "- noVNC (Canlı Tarayıcı):  http://localhost:6080"
