"""利用側の入口。silver の観測値とカタログを読む。

    from psdata import client
    client.read("fred.UNRATE")              # date / value
    client.read(["fred.UNRATE", "fred.CPIAUCSL"], start="2020-01-01")  # 縦長
    client.catalog()                         # 系列の定義・クレジット・ライセンス

データの場所は ``PSDATA_DIR``（既定はリポジトリの ``data/``）。
"""
from __future__ import annotations

import pandas as pd

from psdata.paths import silver_dir


def catalog() -> pd.DataFrame:
    return pd.read_parquet(silver_dir() / "series.parquet")


def read(series_id: str | list[str], *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """1 系列なら date / value、複数なら series_id / country / date / value の縦長で返す。"""
    ids = [series_id] if isinstance(series_id, str) else list(series_id)
    unknown = set(ids) - set(catalog()["series_id"])
    if unknown:
        raise KeyError(f"カタログに無い系列: {sorted(unknown)}")
    filters = [("series_id", "in", ids)]
    if start:
        filters.append(("date", ">=", pd.Timestamp(start)))
    if end:
        filters.append(("date", "<=", pd.Timestamp(end)))
    df = pd.read_parquet(silver_dir() / "observations.parquet", filters=filters)
    df =df.sort_values(["series_id", "date"]).reset_index(drop=True)
    if isinstance(series_id, str):
        return df[["date", "value"]]
    return df[["series_id", "country", "date", "value"]]
