#!/usr/bin/env python3
"""Быстрая проверка доступа к GMGN и просмотр сырых полей мигрейтов.

Запуск из папки проекта:  venv/bin/python scripts/check_api.py [--platform Pump.fun] [--limit 5]
Показывает, какие поля отдаёт GMGN для свежих мигрейтов (в том числе где лежит fee и creator),
и для первого токена — сводку по деву. Полезно, чтобы сверить единицу fee (SOL или USD).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devsearcher.analyzer import extract_fee, parse_candidate, parse_dev_stats  # noqa: E402
from devsearcher.config import ConfigError, load_config  # noqa: E402
from devsearcher.gmgn import GmgnClient, GmgnError  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", action="append", default=None, help="например Pump.fun или letsbonk")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--full", action="store_true", help="печатать объекты целиком")
    args = ap.parse_args()
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Конфиг: {exc}")
        return 2
    client = GmgnClient(cfg.gmgn_api_key, cfg.gmgn_api_host, request_gap=0.6)
    try:
        items = await client.completed_tokens("sol", args.platform or ["Pump.fun"], limit=args.limit)
        print(f"Мигрейтов получено: {len(items)}")
        if not items:
            return 0
        print("\nПоля первого объекта:", ", ".join(sorted(items[0].keys())))
        for it in items:
            c = parse_candidate(it)
            fee, key = extract_fee(it)
            print(f"\n{c.symbol:<12} {c.address}  creator={c.creator}  platform={c.platform}  "
                  f"fee={fee} ({key})  mcap={c.market_cap}")
            if args.full:
                print(json.dumps(it, ensure_ascii=False, indent=1)[:4000])
        first = parse_candidate(items[0])
        if first.creator:
            data = await client.created_tokens("sol", first.creator)
            st = parse_dev_stats(data, first.creator)
            print(f"\nДев {first.creator}: запусков {st.total}, мигрейтов {st.migrated} ({st.ratio_percent:.1f}%), "
                  f"ATH {st.ath_symbol} {st.ath_mc}")
            rows = data.get("tokens") or []
            if rows:
                print("Поля строки created_tokens:", ", ".join(sorted(rows[0].keys())))
                print("fee в строке:", extract_fee(rows[0]))
        return 0
    except GmgnError as exc:
        print(f"Ошибка GMGN: {exc}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
