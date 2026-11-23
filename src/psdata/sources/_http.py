from __future__ import annotations

from datetime import datetime, timezone

import requests

from psdata.paths import raw_dir

# User-Agent は requests の既定値のまま（独自の値は FRED に拒否される）
HTTP_TIMEOUT = 60


def get(url: str, params: dict) -> requests.Response:
    resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp


def save_raw(source: str, code: str, body: str, ext: str) -> None:
    """取得した応答をそのまま残す（raw 層。加工しない）。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = raw_dir(source) / code
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stamp}.{ext}").write_text(body, encoding="utf-8")
