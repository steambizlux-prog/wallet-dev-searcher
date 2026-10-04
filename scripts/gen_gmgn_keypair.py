#!/usr/bin/env python3
"""Генерирует Ed25519-пару для создания API-ключа GMGN (https://gmgn.ai/ai). Работает на Windows/Linux/macOS.

Запуск:  venv\\Scripts\\python scripts\\gen_gmgn_keypair.py      (Windows)
         venv/bin/python scripts/gen_gmgn_keypair.py            (Linux)

Публичный ключ вставляется в форму создания API Key на сайте. Приватный ключ этому софту не нужен
(он требуется только для торговых запросов), но сохраните его.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

GMGN_URL = "https://gmgn.ai/ai"

_args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT_DIR = Path(_args[0]) if _args else Path.home() / ".config" / "gmgn"
PRIV = OUT_DIR / "gmgn_private.pem"
PUB = OUT_DIR / "gmgn_public.pem"


def with_cryptography() -> bool:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        return False
    if PRIV.exists():
        key = serialization.load_pem_private_key(PRIV.read_bytes(), password=None)
    else:
        key = Ed25519PrivateKey.generate()
        PRIV.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ))
    PUB.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ))
    return True


def find_openssl() -> str | None:
    exe = shutil.which("openssl")
    if exe:
        return exe
    for candidate in (
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files (x86)\Git\usr\bin\openssl.exe",
        r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def with_openssl() -> bool:
    exe = find_openssl()
    if not exe:
        return False
    if not PRIV.exists():
        subprocess.run([exe, "genpkey", "-algorithm", "ed25519", "-out", str(PRIV)], check=True)
    subprocess.run([exe, "pkey", "-in", str(PRIV), "-pubout", "-out", str(PUB)], check=True)
    return True


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = with_cryptography() or with_openssl()
    if not ok:
        print("Нет ни библиотеки cryptography, ни openssl. Выполните:")
        print(f"  {sys.executable} -m pip install cryptography")
        print("и запустите скрипт ещё раз.")
        return 1
    pub = PUB.read_text(encoding="utf-8")
    print(f"Приватный ключ: {PRIV}  (никому не показывать)")
    print()
    print(f"Публичный ключ — вставьте его в форму создания API Key на {GMGN_URL} :")
    print()
    print(pub)
    if sys.platform == "win32":
        try:
            subprocess.run("clip", input=pub.encode("ascii"), check=True, shell=True)
            print("Публичный ключ скопирован в буфер обмена — просто вставьте его (Ctrl+V) в форму на сайте.")
        except (OSError, subprocess.CalledProcessError):
            pass
    if "--no-browser" not in sys.argv:
        webbrowser.open(GMGN_URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
