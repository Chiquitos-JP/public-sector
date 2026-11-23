"""API キーなどの秘密の値を読む。ファイルやコードには置かない。

探す順: 環境変数 → Mac のキーチェーン（サービス名 ``psdata-<名前>``）。無ければ空文字。
CI では GitHub Secrets を環境変数として渡す（.github/workflows/fetch.yml）。
値はログや例外の文に出さない。
"""
from __future__ import annotations

import os
import platform
import subprocess

#: 環境変数名 → キーチェーンのサービス名
KEYS = {
    "FRED_API_KEY": "psdata-fred-api-key",
    "ESTAT_APP_ID": "psdata-estat-app-id",
}


def get(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value or platform.system() != "Darwin" or name not in KEYS:
        return value
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYS[name], "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def has(name: str) -> bool:
    return bool(get(name))
