"""IMF DataMapper API（認証不要）からの取得。系列 id は ``imf.<指標コード>``（World Economic Outlook）。

指標ごとに全ての国・地域をまとめて取る（パネル）。日付は年の初日。
WEO は年 2 回（4 月・10 月）の公表で、**過去の年も含めて全体が改定され、見通し（未来の年）を含む**。
IMF の利用条件で自動の一括ダウンロードは許可が要るとされるので、取得は月 1 回・指標ごとに 1 回の呼び出しに抑える。
"""
from __future__ import annotations

import re

import pandas as pd

from psdata.catalog import Series
from psdata.sources._http import get, save_raw

API_URL = "https://www.imf.org/external/datamapper/api/v1"
TIMEOUT = 120
RETRIES = 2


def parse(body: dict, code: str) -> pd.DataFrame:
    values = (body.get("values") or {}).get(code)
    if not values:
        raise RuntimeError(f"IMF DataMapper の応答に {code} の値が無い")
    rows = [
        (country, f"{year}-01-01", v)
        for country, by_year in values.items()
        if re.fullmatch(r"[A-Z]{3}", country)  # ADVEC・WEOWORLD など 3 文字でない集計は持たない
        for year, v in (by_year or {}).items()
    ]
    df = pd.DataFrame(rows, columns=["country", "date", "value"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["date", "value"]).sort_values(["country", "date"]).reset_index(drop=True)


def fetch(series: Series, start: str) -> tuple[pd.DataFrame, dict]:
    resp = get(f"{API_URL}/{series.code}", {}, timeout=TIMEOUT, retries=RETRIES)
    save_raw("imf", series.code, resp.text, "json")
    df = parse(resp.json(), series.code)
    return df[df["date"] >= pd.Timestamp(start)].reset_index(drop=True), {"indicator": series.code}


def fetch_countries() -> pd.DataFrame:
    """IMF の国・地域の一覧（世界銀行の一覧に無いコードを補うため）。地域・グループは集計値として印を付ける。"""
    countries = get(f"{API_URL}/countries", {}, timeout=TIMEOUT, retries=RETRIES).json().get("countries") or {}
    regions = get(f"{API_URL}/regions", {}, timeout=TIMEOUT, retries=RETRIES).json().get("regions") or {}
    groups = get(f"{API_URL}/groups", {}, timeout=TIMEOUT, retries=RETRIES).json().get("groups") or {}
    rows = []
    for table, aggregate in ((countries, False), (regions, True), (groups, True)):
        for code, meta in table.items():
            if re.fullmatch(r"[A-Z]{3}", code):
                rows.append({"country": code, "name": ((meta or {}).get("label") or "").strip(), "is_aggregate": aggregate})
    df = pd.DataFrame(rows).drop_duplicates("country")
    for c in ["region", "region_name", "income", "income_name", "lending"]:
        df[c] = ""
    cols = ["country", "name", "region", "region_name", "income", "income_name", "lending", "is_aggregate"]
    return df[cols].sort_values("country").reset_index(drop=True)
