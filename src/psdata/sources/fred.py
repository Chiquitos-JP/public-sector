"""FRED（セントルイス連銀）からの取得。

``FRED_API_KEY`` があれば公式 API、無ければ公開 CSV を使う。どちらも開始日を指定できる。
"""
from __future__ import annotations

import io
import os

import pandas as pd

from psdata.catalog import Series
from psdata.sources._http import get, save_raw

API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        raise ValueError(f"FRED の CSV の列が足りない: {list(df.columns)}")
    df = df.iloc[:, :2]
    df.columns = ["date", "value"]
    return _clean(df)


def parse_json(body: dict) -> pd.DataFrame:
    obs = body.get("observations", [])
    return _clean(pd.DataFrame({"date": [o["date"] for o in obs], "value": [o["value"] for o in obs]}))


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    # FRED は欠測を "." で返す
    df["value"] = pd.to_numeric(df["value"].astype("string").str.strip().replace({".": None, "": None}), errors="coerce")
    return df.dropna(subset=["date", "value"]).reset_index(drop=True)


def fetch(series: Series, start: str) -> tuple[pd.DataFrame, dict]:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        resp = get(API_URL, {"series_id": series.code, "api_key": key, "file_type": "json", "observation_start": start})
        save_raw("fred", series.code, resp.text, "json")
        return parse_json(resp.json()), {"route": "api", "series_id": series.code}
    resp = get(CSV_URL, {"id": series.code, "cosd": start})
    save_raw("fred", series.code, resp.text, "csv")
    return parse_csv(resp.text), {"route": "csv", "series_id": series.code}
