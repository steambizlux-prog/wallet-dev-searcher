#!/usr/bin/env bash
# Обновить софт на сервере до последней версии ветки и перезапустить.
# Запуск от root:  bash /opt/wallet-dev-searcher/deploy/update.sh
set -euo pipefail

APP_DIR=/opt/wallet-dev-searcher
SRC_DIR=/root/wallet-dev-searcher
SERVICE=wallet-dev-searcher
BRANCH="${BRANCH:-claude/zen-keller-nrxmt4}"
REPO="${REPO:-https://github.com/steambizlux-prog/wallet-dev-searcher.git}"

if [ -d "$SRC_DIR/.git" ]; then
  git -C "$SRC_DIR" fetch -q origin "$BRANCH"
  git -C "$SRC_DIR" checkout -q "$BRANCH"
  git -C "$SRC_DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q -b "$BRANCH" "$REPO" "$SRC_DIR"
fi

bash "$SRC_DIR/deploy/install.sh"
systemctl restart "$SERVICE"
sleep 2
systemctl --no-pager --lines=8 status "$SERVICE" || true
echo
echo "Логи: journalctl -u $SERVICE -f"
