#!/usr/bin/env python3
"""カタログの収録基準と、手元の bronze ストアの構造・鮮度を検証する（再取得しない）。

終了コード: 0 = 問題なし（警告は出しても 0）/ 1 = エラーあり
"""
from __future__ import annotations

import sys

import pandas as pd

from psdata import catalog, contracts, pipeline, store
from psdata.paths import bronze_path, silver_dir

MAX_STALE_DAYS = catalog.MAX_STALE_DAYS


def main() -> int:
    sources = catalog.load_sources()
    series = catalog.load_series()
    errors = catalog.validate(series, sources)
    warnings: list[str] = []

    countries_path = silver_dir() / "countries.parquet"
    known = set(pd.read_parquet(countries_path)["country"]) if countries_path.is_file() else None
    errors += catalog.validate_groups(catalog.load_groups(), known)

    for s in series:
        if s.tier != "stocked":
            continue
        df = store.read(bronze_path(s.id))
        if df is None:
            errors.append(f"{s.id}: ストアが無い（scripts/fetch.py で作る）")
            continue
        rep = store.validate_frame(df, label=s.id, keys=s.keys)
        errors += rep.errors
        warnings += rep.warnings
        age = (pd.Timestamp.now().normalize() - pd.Timestamp(df["date"].max())).days
        if age > MAX_STALE_DAYS[s.freq]:
            warnings.append(f"{s.id}: 最終日が {age} 日前（{df['date'].max().date()}）")

    # silver の表の契約（catalog/contracts/silver.yml）。違反すれば CI は保存先に書かない
    contract = contracts.load()
    if contract["schema_version"] != pipeline.SCHEMA_VERSION:
        errors.append(f"契約の schema_version {contract['schema_version']} と pipeline.SCHEMA_VERSION {pipeline.SCHEMA_VERSION} が違う")
    if (silver_dir() / "series.parquet").is_file():
        errors += [f"契約: {e}" for e in contracts.check_silver(contract=contract)]

    for w in warnings:
        print(f"⚠️ {w}")
    for e in errors:
        print(f"❌ {e}")
    print(f"検証: エラー {len(errors)} / 警告 {len(warnings)}（{len(series)} 系列）")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
