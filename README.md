# wallet-dev-searcher

Софт на Python для поиска дев-кошельков по мигрейтам токенов. Все данные берутся из **GMGN OpenAPI**
(тот же API, что использует `gmgn-cli` и скиллы на https://gmgn.ai/ai). Управление — через Telegram-бота.

## Как работает

1. Раз в `poll_interval_sec` (по умолчанию 30 с) запрашивается список свежих мигрейтов
   (`POST /v1/trenches`, категория `completed`) по выбранным лаунчпадам (по умолчанию `Pump.fun`).
2. Для каждого нового токена смотрится **fee**. Если fee < `min_fee_sol` (по умолчанию 2 SOL) — токен пропускается.
3. У подходящего токена берётся кошелёк создателя (`creator`, при отсутствии — из `GET /v1/token/info`).
4. По кошельку запрашивается история запусков (`GET /v1/user/created_tokens`): `inner_count` (на кривой) +
   `open_count` (мигрировали) = всего запусков.
5. Дев подходит, если:
   - мигрейтов ≥ `min_migrate_percent` % от всех запусков (по умолчанию **5 %**: 1000 запусков → минимум 50 мигрейтов);
   - запусков ≤ `max_dev_tokens` (по умолчанию **1000**);
   - запусков ≥ `min_dev_tokens` (по умолчанию 1, можно поднять, чтобы отсечь девов с одним токеном).
6. В Telegram уходит сообщение: кошелёк (моноширинным — копируется одним нажатием), ссылка на кошелёк в GMGN,
   статистика дева (запусков / мигрейтов / лучший токен / статус холда / источник фандинга / флаги CTO, DEX ads,
   Twitter-ренеймы) и токен-триггер со ссылкой на GMGN.

Один и тот же дев не присылается повторно чаще, чем раз в `dev_cooldown_hours` (24 ч).
Все настройки меняются из Telegram на лету и сохраняются в `data/settings.json`.

## Установка на Ubuntu VPS

```bash
git clone <этот репозиторий> wallet-dev-searcher
cd wallet-dev-searcher
sudo bash deploy/install.sh        # ставит в /opt/wallet-dev-searcher + systemd
sudo nano /opt/wallet-dev-searcher/.env
sudo systemctl restart wallet-dev-searcher
sudo journalctl -u wallet-dev-searcher -f
```

### Что вписать в `.env`

| Переменная | Что это |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота от [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | куда слать уведомления: id вашего чата с ботом, группы или канала (узнать: написать боту `/start` — он покажет ваш id; для группы можно воспользоваться @userinfobot / @getmyid_bot) |
| `TELEGRAM_ADMIN_IDS` | кто может управлять ботом, через запятую. Пусто → только `TELEGRAM_CHAT_ID` |
| `GMGN_API_KEY` | ключ GMGN OpenAPI (см. ниже) |

### Ключ GMGN

1. На сервере выполните `bash scripts/gen_gmgn_keypair.sh` — скрипт сгенерирует Ed25519-пару и напечатает **публичный** ключ.
2. Зайдите на https://gmgn.ai/ai, в форме создания API Key вставьте публичный ключ, создайте ключ.
3. Полученный `gmgn_...` ключ вставьте в `.env` → `GMGN_API_KEY`. Приватный ключ этому софту не нужен
   (он нужен только для торговых запросов), просто сохраните его.

Важно: GMGN принимает запросы только при расхождении часов сервера < 5 секунд — `install.sh` включает NTP.
IPv6 GMGN не поддерживает; если получаете 401/403 при верном ключе — отключите IPv6 на интерфейсе.

### Проверка доступа и единиц fee

```bash
cd /opt/wallet-dev-searcher
sudo -u devsearcher venv/bin/python scripts/check_api.py --limit 5
```

Скрипт печатает сырые поля свежих мигрейтов (где лежит `creator`, `total_fee` и т. п.) и сводку по деву
первого токена. Если окажется, что GMGN отдаёт fee в долларах, а не в SOL — в боте выполните
`/set fee_unit usd`: порог `min_fee_sol` будет пересчитываться по текущему курсу SOL.
То же самое из Telegram: `/raw <адрес токена>`.

## Команды бота

| Команда | Действие |
|---|---|
| `/status` | состояние сканера, счётчики, последняя ошибка |
| `/settings` | все настройки + кнопки ±(fee, % мигрейтов, макс. токенов), пауза |
| `/set <имя> <значение>` | изменить любую настройку (имена — в `/settings`) |
| `/set_fee 2` | минимальный fee токена, SOL |
| `/set_migrate 5` | минимальная доля мигрейтов у дева, % |
| `/set_maxtokens 1000` | максимум запусков у дева |
| `/set_mintokens 5` | минимум запусков у дева |
| `/set_interval 30` | интервал опроса, сек |
| `/platforms pump stonk` | какие лаунчпады смотреть (понимает `pump`, `bonk`, `stonk`; точные имена GMGN: `Pump.fun`, `letsbonk`, `stonkfun`) |
| `/platforms_seen` | под какими именами GMGN отдаёт лаунчпады прямо сейчас (чтобы добавить новый) |
| `/pause`, `/resume` | остановить / продолжить |
| `/check <кошелёк>` | проверить дева вручную по текущим критериям |
| `/token <CA>` | найти дева по адресу токена и проверить |
| `/raw <CA>` | сырые поля GMGN по токену (сверить fee) |
| `/last [N]` | последние совпадения |
| `/test` | тестовое уведомление в чат |

Остальные настройки через `/set`: `max_token_age_min` (не трогать мигрейты старше N минут, по умолчанию 120),
`dev_cooldown_hours`, `fee_unit` (`sol`/`usd`), `fee_unknown_policy` (`skip`/`check` — что делать, если у
токена нет поля fee), `server_filters` (`on` — дополнительно фильтровать на стороне GMGN по
`max_creator_created_count`, `min_creator_created_open_ratio`, `min_total_fee`; меньше запросов),
`request_gap_sec` (пауза между запросами к GMGN; Free-тариф ≈ 5 ед/с, trenches и created_tokens стоят по 2).

## Запуск на Windows (потестить на своём ПК)

1. Установите Python 3.10+ с https://www.python.org/downloads/ (в установщике поставьте галочку
   **Add python.exe to PATH**).
2. Скачайте репозиторий (Code → Download ZIP, распакуйте) или `git clone`.
3. Запустите `run_windows.bat`: он создаст `venv`, поставит зависимости и откроет `.env` в Блокноте.
4. Впишите `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GMGN_API_KEY` (см. «Что вписать в .env» выше),
   сохраните, закройте Блокнот.
5. Ещё раз запустите `run_windows.bat` — бот стартует в этом окне. Остановить: `Ctrl+C` или закрыть окно.

Ключ GMGN на Windows: запустите `get_gmgn_key_windows.bat` — он сгенерирует пару ключей, скопирует
публичный ключ в буфер обмена и откроет https://gmgn.ai/ai. Вставьте ключ в форму создания API Key,
полученный `gmgn_...` впишите в `.env`. (То же руками: `venv\Scripts\python -m pip install cryptography`,
затем `venv\Scripts\python scripts\gen_gmgn_keypair.py`.)

Проверить сырые поля GMGN (единицы fee и т. п.): `check_api_windows.bat`.

Данные (`settings.json`, база) лежат в папке `data` рядом с кодом — чтобы потом перенести настройки
на VPS, достаточно скопировать `data/settings.json`.

## Запуск без systemd (Linux, для теста)

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
venv/bin/python -m devsearcher
```

## Тесты

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest -q
```

## Структура

```
devsearcher/
  config.py      — .env / переменные окружения
  settings.py    — настройки фильтра (JSON, меняются из Telegram)
  gmgn.py        — клиент GMGN OpenAPI (X-APIKEY, timestamp, client_id, лимиты/429)
  analyzer.py    — разбор ответов и критерии дева (чистые функции)
  storage.py     — SQLite: увиденные токены, проверки девов, совпадения, счётчики
  scanner.py     — цикл: мигрейты → fee → дев → проверка → уведомление
  formatting.py  — HTML-сообщения для Telegram
  bot.py         — команды и кнопки (aiogram 3)
  main.py        — точка входа
deploy/          — systemd unit + install.sh
scripts/         — генерация ключа GMGN, проверка API
tests/           — pytest
```

## Ограничения, о которых стоит знать

- GMGN отдаёт список `completed` максимум по 80 токенов за запрос; при опросе каждые 30 с этого хватает
  с большим запасом (Pump.fun мигрирует на порядок меньше токенов в час).
- Массив `tokens` в `created_tokens` обрезан (~100 последних запусков), поэтому общее число запусков
  берётся из счётчиков `inner_count + open_count`, а не из длины массива.
- Поле fee в ответе GMGN (`total_fee`) не задокументировано по единицам измерения — проверьте
  `scripts/check_api.py` или `/raw` на первых реальных токенах и при необходимости переключите `fee_unit`.
