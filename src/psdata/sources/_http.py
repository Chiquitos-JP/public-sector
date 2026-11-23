from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from psdata.paths import raw_dir

# User-Agent は requests の既定値のまま（独自の値は FRED に拒否される）
HTTP_TIMEOUT = 60


def get(url: str, params: dict, *, timeout: float = HTTP_TIMEOUT, retries: int = 0) -> requests.Response:
    """GET して 2xx でなければ例外。``retries`` 回まで、時間切れ・接続失敗・5xx を間隔を空けて取り直す。"""
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code < 500 or attempt == retries:
                resp.raise_for_status()
                return resp
        except (requests.Timeout, requests.ConnectionError):
            if attempt == retries:
                raise
        time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def save_raw(source: str, code: str, body: str, ext: str) -> None:
    """取得した応答をそのまま残す（raw 層。加工しない）。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = raw_dir(source) / code
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stamp}.{ext}").write_text(body, encoding="utf-8")
