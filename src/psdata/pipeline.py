"""取得 → bronze 更新 → silver 統合。"""
from __future__ import annotations

import pandas as pd

from psdata import catalog, store
from psdata.catalog import Series
from psdata.paths import bronze_path, silver_dir
from psdata.sources import FETCHERS

OBSERVATION_COLUMNS = ["series_id", "country", "date", "value", "fetched_at"]


def refresh(series: Series, *, full: bool = False) -> store.UpdateResult:
    """1 系列を取り直して bronze を更新する。``full`` なら定義上の開始日から取り直す。"""
    path = bronze_path(series.id)
    start = series.start if full else store.fetch_start(
        path, default_start=series.start, window=catalog.parse_window(series.refetch_window)
    )
    fresh, params = FETCHERS[series.distributor](series, start)
    return store.update(path, fresh, start=start, source=series.distributor, params=params, label=series.id)


def build_silver(series: list[Series], sources: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """bronze を共通形式の観測値表に統合し、カタログ表と一緒に silver へ書く。"""
    frames = []
    for s in series:
        df = store.read(bronze_path(s.id))
        if df is None:
            continue
        frames.append(df.assign(series_id=s.id, country=s.country)[OBSERVATION_COLUMNS])
    obs = (
        pd.concat(frames, ignore_index=True).sort_values(["series_id", "date"]).reset_index(drop=True)
        if frames
        else pd.DataFrame(columns=OBSERVATION_COLUMNS)
    )
    table = catalog.series_table(series, sources)
    out = silver_dir()
    out.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(out / "observations.parquet", index=False)
    table.to_parquet(out / "series.parquet", index=False)
    return obs, table
