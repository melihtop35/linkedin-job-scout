#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=================================================="
echo "  LinkedIn Job Scout - Durduruluyor..."
echo "=================================================="

docker compose down

echo ""
echo "[TAMAM] Konteyner güvenli bir şekilde durduruldu."
echo "Oturum çerezleriniz ve kaydedilen iş ilanlarınız 'data' klasöründe korundu."
