"""系列ごとの特徴（プロファイル）を、実物のデータから自動で作る。

カタログ（手で書いた宣言: 定義・前提・出典）と対になる「観察」。毎回の取得のあとに作り直し、
``silver/profile.parquet`` に置く。利用側はしきい値（鮮度・値域・変化幅・改定幅）を自分で実測せず、ここを読めばよい。

鮮度の欄は意味を分けて持つ（``fetched_at`` の取り違えを繰り返さないため）:

- ``last_observation_date``  データの最後の日付（期間の開始日）。取得が動いているかはこれで見る
- ``last_checked_at``        最後に取り直して確かめた時刻。値が変わらなくても進む
- ``last_changed_at``        最後に値が書き換わった時刻（= bronze の ``fetched_at`` の最大）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from psdata import store
from psdata.catalog import MAX_STALE_DAYS, Series
from psdata.paths import bronze_path

#: 欠けた期間を数える頻度（日次・週次は休場や公表の都合で抜けるのが普通なので、最大の空きで見る）
_PERIOD_FREQ = {"M": "MS", "Q": "QS", "A": "YS"}

PROFILE_COLUMNS = [
    "series_id", "freq", "is_panel",
    "first_date", "last_observation_date", "n_obs", "n_countries", "n_countries_at_last",
    "missing_periods", "missing_dates", "max_gap_days", "median_gap_days",
    "value_min", "value_p01", "value_median", "value_p99", "value_max",
    "abs_change_p50", "abs_change_p99", "abs_change_max", "pct_change_p99",
    "n_revisions", "max_rel_revision", "last_revision_at",
    "release_lag_days_median", "release_lag_days_now",
    "last_checked_at", "last_changed_at", "stale_limit_days", "is_stale",
]


def _provenance(series: Series) -> list[dict]:
    pp = store.provenance_path(bronze_path(series.id))
    if not pp.is_file():
        return []
    return json.loads(pp.read_text(encoding="utf-8")).get("records") or []


def _gaps(dates: pd.Series) -> pd.Series:
    return dates.sort_values().diff().dt.days.dropna()


def profile_series(series: Series, now: pd.Timestamp | None = None) -> dict | None:
    """1 系列のプロファイル。ストアが無ければ None。"""
    df = store.read(bronze_path(series.id))
    if df is None:
        return None
    now = now or pd.Timestamp(datetime.now(timezone.utc))
    groups = [g for _, g in df.groupby("country")] if series.is_panel else [df]
    last = pd.Timestamp(df["date"].max())

    # 空きと変化幅は国ごとに測ってからまとめる（国をまたいで差を取らない）
    gaps = pd.concat([_gaps(g["date"]) for g in groups], ignore_index=True)
    changes, pct = [], []
    for g in groups:
        v = g.sort_values("date")["value"]
        changes.append(v.diff().abs().dropna())
        if (v > 0).all():
            pct.append(v.pct_change(fill_method=None).abs().dropna())
    changes = pd.concat(changes, ignore_index=True) if changes else pd.Series(dtype=float)
    pct = pd.concat(pct, ignore_index=True) if pct else pd.Series(dtype=float)

    missing = []
    if not series.is_panel and series.freq in _PERIOD_FREQ:
        expected = pd.date_range(df["date"].min(), last, freq=_PERIOD_FREQ[series.freq])
        missing = [d.date().isoformat() for d in expected.difference(pd.DatetimeIndex(df["date"]))]

    rp = store.revisions_path(bronze_path(series.id))
    rev = pd.read_parquet(rp) if rp.is_file() else None
    if rev is not None and len(rev):
        rel = ((rev["new_value"] - rev["old_value"]).abs() / rev["old_value"].abs()).replace([np.inf], np.nan)
        n_rev, max_rel, last_rev = len(rev), float(rel.max()), pd.Timestamp(rev["detected_at"].max())
    else:
        n_rev, max_rel, last_rev = 0, np.nan, pd.NaT

    # 公表の遅れ: 取得したときに、最新のデータが何日前の期間だったか（期間の開始日からの日数）
    records = _provenance(series)
    lags = [
        (pd.Timestamp(r["fetched_at"]).tz_convert(None) - pd.Timestamp(r["date_max"])).days
        for r in records
        if r.get("date_max")
    ]
    checked = pd.Timestamp(records[-1]["fetched_at"]) if records else pd.NaT
    limit = MAX_STALE_DAYS[series.freq]
    age = (now.tz_convert(None).normalize() - last).days

    def q(s: pd.Series, p: float) -> float:
        return float(s.quantile(p)) if len(s) else np.nan

    v = df["value"]
    return {
        "series_id": series.id,
        "freq": series.freq,
        "is_panel": series.is_panel,
        "first_date": pd.Timestamp(df["date"].min()),
        "last_observation_date": last,
        "n_obs": len(df),
        "n_countries": int(df["country"].nunique()) if series.is_panel else 1,
        "n_countries_at_last": int(df.loc[df["date"] == last, "country"].nunique()) if series.is_panel else 1,
        "missing_periods": len(missing),
        "missing_dates": ",".join(missing),
        "max_gap_days": q(gaps, 1.0),
        "median_gap_days": q(gaps, 0.5),
        "value_min": float(v.min()),
        "value_p01": q(v, 0.01),
        "value_median": q(v, 0.5),
        "value_p99": q(v, 0.99),
        "value_max": float(v.max()),
        "abs_change_p50": q(changes, 0.5),
        "abs_change_p99": q(changes, 0.99),
        "abs_change_max": q(changes, 1.0),
        "pct_change_p99": q(pct, 0.99),
        "n_revisions": n_rev,
        "max_rel_revision": max_rel,
        "last_revision_at": last_rev,
        "release_lag_days_median": float(np.median(lags)) if lags else np.nan,
        "release_lag_days_now": float(age),
        "last_checked_at": checked,
        "last_changed_at": pd.Timestamp(df["fetched_at"].max()),
        "stale_limit_days": limit,
        "is_stale": age > limit,
    }


def build(series: list[Series], now: pd.Timestamp | None = None) -> pd.DataFrame:
    rows = [r for s in series if (r := profile_series(s, now)) is not None]
    out = pd.DataFrame(rows, columns=PROFILE_COLUMNS)
    for c in ["last_checked_at", "last_changed_at", "last_revision_at"]:
        out[c] = pd.to_datetime(out[c], utc=True)
    return out
