"""silver の表の契約。実物が守っていること、壊したら落ちること。"""
import pandas as pd
import pytest

from psdata import catalog, contracts, pipeline


@pytest.fixture
def silver(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    series = [s for s in catalog.load_series() if s.id in {"fred.UNRATE", "wb.SP.POP.TOTL"}]

    def single(s, start):
        return pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-02-01"]), "value": [4.0, 4.1]}), {}

    def panel(s, start):
        return pd.DataFrame({"country": ["JPN", "USA"], "date": pd.to_datetime(["2024-01-01"] * 2), "value": [1.0, 2.0]}), {}

    def countries():
        return pd.DataFrame({"country": ["JPN", "USA"], "name": ["Japan", "United States"], "region": ["EAS", "NAC"],
                             "region_name": ["", ""], "income": ["HIC", "HIC"], "income_name": ["", ""],
                             "lending": ["", ""], "is_aggregate": [False, False]})

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": single, "wb": panel})
    monkeypatch.setattr(pipeline, "REFERENCES", {"wb": countries})
    pipeline.refresh_references({"wb"})
    for s in series:
        pipeline.refresh(s)
    pipeline.build_silver(series, catalog.load_sources())
    return tmp_path / "silver"


def test_built_silver_meets_contract(silver):
    assert contracts.check_silver(silver) == []
    assert contracts.load()["schema_version"] == pipeline.SCHEMA_VERSION


def _break(path, fn):
    df = pd.read_parquet(path)
    fn(df).to_parquet(path, index=False)


@pytest.mark.parametrize("table,breaker,expected", [
    ("observations", lambda d: d.drop(columns="fetched_at"), "契約の列が無い"),
    ("observations", lambda d: d.assign(extra=1), "契約に無い列"),
    ("observations", lambda d: pd.concat([d, d.iloc[:1]]), "重なる"),
    ("observations", lambda d: d.assign(value=d["value"].astype(str)), "型が"),
    ("observations", lambda d: d.assign(date=d["date"] + pd.Timedelta(hours=3)), "時刻"),
    ("observations", lambda d: d.assign(series_id="fred.NOPE"), "に無い値"),
    ("series", lambda d: d.assign(unit_kind="money"), "決まっていない値"),
    ("panel", lambda d: d.assign(country="ZZZ"), "countries.country に無い値"),
])
def test_broken_silver_is_caught(silver, table, breaker, expected):
    _break(silver / f"{table}.parquet", breaker)
    assert any(expected in e for e in contracts.check_silver(silver))
