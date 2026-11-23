from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "catalog"


def data_dir() -> Path:
    """データの置き場所。``PSDATA_DIR`` で差し替えられる（職場の環境などで別の場所に作るため）。"""
    env = os.environ.get("PSDATA_DIR", "").strip()
    return Path(env).expanduser() if env else REPO_ROOT / "data"


def raw_dir(source: str) -> Path:
    return data_dir() / "raw" / source


def bronze_path(series_id: str) -> Path:
    source = series_id.split(".", 1)[0]
    return data_dir() / "bronze" / source / f"{series_id}.parquet"


def reference_path(source: str, name: str) -> Path:
    """配信元の参照表（国の一覧など）。系列ではないので改定ログは持たず、取るたびに置き換える。"""
    return data_dir() / "bronze" / source / f"_{name}.parquet"


def ondemand_dir() -> Path:
    """カタログ外の系列をその場で取ったものの置き場（手元だけ。保存先には書かない）。"""
    return data_dir() / "ondemand"


def cache_dir() -> Path:
    """取得元の指標一覧など、取り直せる参照情報のキャッシュ（手元だけ）。"""
    return data_dir() / "cache"


def silver_dir() -> Path:
    return data_dir() / "silver"
