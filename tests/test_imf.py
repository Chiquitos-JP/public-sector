"""IMF DataMapper の解析と、見通し（未来の年）を含む系列の扱い。"""
import pandas as pd

from psdata import catalog, store
from psdata.sources import imf


def test_parse_keeps_iso3_and_drops_groups_and_nulls():
    body = {"values": {"NGDP_RPCH": {
        "GTM": {"2024": 3.7, "2031": 3.9}, "WEOWORLD": {"2024": 3.2}, "ADVEC": {"2024": 1.8}, "HND": {"2024": None},
    }}}
    df = imf.parse(body, "NGDP_RPCH")
    assert df[["country", "value"]].values.tolist() == [["GTM", 3.7], ["GTM", 3.9]]
    assert df["date"].tolist() == [pd.Timestamp("2024-01-01"), pd.Timestamp("2031-01-01")]


def test_projection_years_allow_future_dates(tmp_path):
    s = next(x for x in catalog.load_series() if x.id == "imf.NGDP_RPCH")
    assert s.projection_years > 0
    future = pd.Timestamp.now().normalize() + pd.DateOffset(years=5)
    df = pd.DataFrame({"country": ["GTM"], "date": [future.replace(month=1, day=1)], "value": [3.9]})
    path = tmp_path / "imf.parquet"
    ok = store.update(path, df, start="1980-01-01", source="t", keys=s.keys, allow_future_days=s.allow_future_days)
    assert ok.written
    ng = store.update(tmp_path / "x.parquet", df, start="1980-01-01", source="t", keys=s.keys)
    assert not ng.written  # 見通しを持たない系列なら未来の日付は止める
