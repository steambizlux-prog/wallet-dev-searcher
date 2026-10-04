#!/usr/bin/env bash
# Генерирует Ed25519-пару для создания API-ключа на https://gmgn.ai/ai
# Публичный ключ вставляется в форму на сайте; приватный для этого софта НЕ нужен
# (он требуется только для торговых запросов), но сохраните его на всякий случай.
set -euo pipefail
OUT="${1:-$HOME/.config/gmgn}"
mkdir -p "$OUT"
chmod 700 "$OUT"
if [ ! -f "$OUT/gmgn_private.pem" ]; then
  openssl genpkey -algorithm ed25519 -out "$OUT/gmgn_private.pem"
  chmod 600 "$OUT/gmgn_private.pem"
fi
openssl pkey -in "$OUT/gmgn_private.pem" -pubout -out "$OUT/gmgn_public.pem"
echo "Приватный ключ: $OUT/gmgn_private.pem (никому не показывать)"
echo
echo "Публичный ключ — вставьте его в форму создания API Key на https://gmgn.ai/ai :"
echo
cat "$OUT/gmgn_public.pem"
