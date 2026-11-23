"""値の有効期間の表（vintages）。「その時点で知り得た値」を読むためのもの。

bronze（今の値・初めて取った時刻）と改定ログ（前の値・新しい値・気づいた時刻）から、
1 つの値が「いつからいつまで正しいとされていたか」を組み立てる。

    列: series_id / country / date / value / valid_from / valid_to（今も有効なら空）

ある時刻 T に知り得た値は ``valid_from <= T < valid_to``（``valid_to`` が空なら ``valid_from <= T``）。
⚠️ 時刻は public-sector が取得した時刻であって、作成元の公表時刻ではない。取得を始める前（2026-11-23 より前）は再現できない。
"""
from __future__ import annotations

import pandas as pd

from psdata import store
from psdata.catalog import Series
from psdata.paths import bronze_path

COLUMNS = ["series_id", "country", "date", "value", "valid_from", "valid_to"]


def series_vintages(series: Series) -> pd.DataFrame | None:
    path = bronze_path(series.id)
    cur = store.read(path)
    if cur is None:
        return None
    keys = list(series.keys)
    if "first_seen_at" not in cur.columns:
        cur = cur.assign(first_seen_at=cur["fetched_at"])
    cur = cur.astype({k: "string" for k in keys if k != "date"})
    rp = store.revisions_path(path)
    rev = pd.read_parquet(rp) if rp.is_file() else pd.DataFrame(columns=[*keys, "old_value", "new_value", "detected_at"])
    rev = rev.astype({k: "string" for k in keys if k != "date"})

    if rev.empty:
        out = cur.assign(valid_from=cur["first_seen_at"], valid_to=pd.NaT)[[*keys, "value", "valid_from", "valid_to"]]
    else:
        rev = rev.sort_values([*keys, "detected_at"])
        # 改定のたびに区切る: [初めて取った時刻, 1 回目の改定) は 1 回目の old_value、以降は各改定の new_value
        first = rev.groupby(keys, as_index=False).first()[[*keys, "old_value", "detected_at"]]
        first = first.merge(cur[[*keys, "first_seen_at"]], on=keys, how="left")
        head = pd.DataFrame({
            **{k: first[k] for k in keys},
            "value": first["old_value"],
            "valid_from": first["first_seen_at"],
            "valid_to": first["detected_at"],
        })
        nxt = rev.groupby(keys)["detected_at"].shift(-1)
        steps = pd.DataFrame({
            **{k: rev[k] for k in keys},
            "value": rev["new_value"],
            "valid_from": rev["detected_at"],
            "valid_to": nxt,
        })
        untouched = cur.merge(first[keys], on=keys, how="left", indicator=True)
        untouched = untouched[untouched["_merge"] == "left_only"]
        plain = untouched.assign(valid_from=untouched["first_seen_at"], valid_to=pd.NaT)[[*keys, "value", "valid_from", "valid_to"]]
        out = pd.concat([plain, head, steps], ignore_index=True)

    out = out.assign(series_id=series.id)
    if not series.is_panel:
        out = out.assign(country=series.country)
    for c in ["valid_from", "valid_to"]:
        out[c] = pd.to_datetime(out[c], utc=True)
    return out[COLUMNS]


def build(series: list[Series]) -> pd.DataFrame:
    frames = [v for s in series if (v := series_vintages(s)) is not None]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(["series_id", "country", "date", "valid_from"]).reset_index(drop=True)


def as_of(df: pd.DataFrame, when) -> pd.DataFrame:
    """``when`` の時点で有効だった値だけにする。"""
    t = pd.Timestamp(when)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    live = (df["valid_from"] <= t) & (df["valid_to"].isna() | (df["valid_to"] > t))
    return df[live]
