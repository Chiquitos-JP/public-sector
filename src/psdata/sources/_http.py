from __future__ import annotations

import gzip
import time
from pathlib import Path
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


def save_raw(source: str, code: str, body: str, ext: str) -> Path:
    """取得した応答をそのまま残す（raw 層。加工しない）。

    gzip で圧縮して ``<時刻>.<ext>.gz`` に書く（JSON は 30〜40 分の 1 になる）。中身は応答のまま。
    保存先では保存期間を過ぎた raw を CI が消す（.github/workflows/fetch.yml の RAW_KEEP_DAYS）。
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = raw_dir(source) / code
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{stamp}.{ext}.gz"
    # mtime=0 にして、同じ応答なら同じバイト列になるようにする
    path.write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))
    return path
