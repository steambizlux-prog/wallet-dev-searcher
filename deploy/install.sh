#!/usr/bin/env bash
# Установка на Ubuntu 22.04/24.04. Запускать от root: sudo bash deploy/install.sh
set -euo pipefail
# без интерактивных вопросов apt/needrestart ("Which services should be restarted?")
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

APP_DIR=/opt/wallet-dev-searcher
SERVICE=wallet-dev-searcher
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[1/6] Пакеты"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git rsync >/dev/null

echo "[2/6] Пользователь и папка"
id -u devsearcher >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin devsearcher
mkdir -p "$APP_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
  rsync -a --delete --exclude venv --exclude data --exclude .env --exclude .git "$SRC_DIR/" "$APP_DIR/"
fi
mkdir -p "$APP_DIR/data"

echo "[3/6] Виртуальное окружение"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "[4/6] Конфиг"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "   Создан $APP_DIR/.env — заполните TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GMGN_API_KEY"
fi
chmod 600 "$APP_DIR/.env"
chown -R devsearcher:devsearcher "$APP_DIR"

echo "[5/6] systemd"
cp "$APP_DIR/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

echo "[6/6] Время (GMGN принимает запросы только при расхождении часов < 5 с)"
timedatectl set-ntp true 2>/dev/null || true

cat <<MSG

Готово. Дальше:
  1) nano $APP_DIR/.env            — вписать токены
  2) systemctl restart $SERVICE    — запустить
  3) journalctl -u $SERVICE -f     — смотреть логи
MSG
