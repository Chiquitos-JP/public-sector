"""取得 → bronze 更新 → silver 統合。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from psdata import catalog, store
from psdata.catalog import Series
from psdata.paths import bronze_path, reference_path, silver_dir
from psdata.sources import FETCHERS, REFERENCES

OBSERVATION_COLUMNS = ["series_id", "country", "date", "value", "fetched_at"]


def last_fetched(series: Series) -> pd.Timestamp | None:
    """最後に取得を試みて書き込んだ時刻（provenance の最終記録）。"""
    pp = store.provenance_path(bronze_path(series.id))
    if not pp.is_file():
        return None
    records = json.loads(pp.read_text(encoding="utf-8")).get("records") or []
    return pd.Timestamp(records[-1]["fetched_at"]) if records else None


def is_due(series: Series, now: pd.Timestamp | None = None) -> bool:
    """``fetch_every`` を過ぎていれば取る。指定が無ければ毎回取る。"""
    if not series.fetch_every:
        return True
    last = last_fetched(series)
    if last is None:
        return True
    now = now or pd.Timestamp(datetime.now(timezone.utc))
    # 毎日同じ時刻に動く CI で 1 日ずれないよう、半日の余裕を見る
    return now >= last + catalog.parse_window(series.fetch_every) - pd.Timedelta(hours=12)


def refresh(series: Series, *, full: bool = False) -> store.UpdateResult:
    """1 系列を取り直して bronze を更新する。``full`` なら定義上の開始日から取り直す。"""
    path = bronze_path(series.id)
    start = series.start if full else store.fetch_start(
        path, default_start=series.start, window=catalog.parse_window(series.refetch_window)
    )
    fresh, params = FETCHERS[series.distributor](series, start)
    return store.update(
        path,
        fresh,
        start=start,
        source=series.distributor,
        params=params,
        label=series.id,
        keys=series.keys,
        # パネルは成長率など 0 をまたぐ指標が多く、前期比の警告は意味をなさない
        max_abs_pct_change=None if series.is_panel else 1.0,
    )


def refresh_references(distributors: set[str]) -> dict[str, int]:
    """パネルの配信元の国一覧を取り直す（系列ではないので置き換えるだけ）。"""
    counts = {}
    for d in sorted(distributors & set(REFERENCES)):
        df = REFERENCES[d]()
        if df.empty or df["country"].duplicated().any():
            raise RuntimeError(f"{d}: 国の一覧が空か重複している")
        path = reference_path(d, "countries")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        counts[d] = len(df)
    return counts


def _frames(series: list[Series]) -> tuple[list[pd.DataFrame], list[pd.DataFrame]]:
    single, panel = [], []
    for s in series:
        df = store.read(bronze_path(s.id))
        if df is None:
            continue
        if s.is_panel:
            panel.append(df.assign(series_id=s.id)[OBSERVATION_COLUMNS])
        else:
            single.append(df.assign(series_id=s.id, country=s.country)[OBSERVATION_COLUMNS])
    return single, panel


def _concat(frames: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(keys).reset_index(drop=True)


def build_silver(series: list[Series], sources: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """bronze を silver に統合する。

    - ``observations.parquet`` 単一の国の系列（1 行は series_id / date で決まる。利用側との契約）
    - ``panel.parquet``        国をまたぐ系列（1 行は series_id / country / date で決まる）
    - ``series.parquet``       カタログ（両方の系列）
    - ``countries.parquet``    国・地域の一覧（パネルの国コードを引く表）
    """
    single, panel = _frames(series)
    obs = _concat(single, ["series_id", "date"])
    pan = _concat(panel, ["series_id", "country", "date"])
    table = catalog.series_table(series, sources)
    out = silver_dir()
    out.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(out / "observations.parquet", index=False)
    table.to_parquet(out / "series.parquet", index=False)
    if panel:
        pan.to_parquet(out / "panel.parquet", index=False)
    refs = [pd.read_parquet(p) for d in REFERENCES if (p := reference_path(d, "countries")).is_file()]
    if refs:
        # 配信元が増えたら先に載っている方を優先する（今は世界銀行だけ）
        pd.concat(refs, ignore_index=True).drop_duplicates("country").to_parquet(out / "countries.parquet", index=False)
    return obs, pan, table
