"""取得元をまたぐ準備: 国コードの統一・日本語名・概念での比較。"""
import pandas as pd
import pytest

from psdata import catalog, client, harmonize, pipeline, store


def test_name_ja_prefers_short_common_names():
    assert harmonize.name_ja("KOR") == "韓国"
    assert harmonize.name_ja("TWN") == "台湾"
    assert harmonize.name_ja("GTM") == "グアテマラ"
    assert harmonize.name_ja("WLD") == "世界"
    assert harmonize.name_ja("ZZZ", "fallback") == "fallback"


def test_store_folds_alias_codes_into_canonical(tmp_path):
    path = tmp_path / "imf.parquet"
    keys = ("country", "date")
    old = pd.DataFrame({"country": ["UVK", "XKX"], "date": pd.to_datetime(["2023-01-01", "2024-01-01"]), "value": [1.0, 2.0]})
    store.update(path, old, start="1980-01-01", source="t", keys=keys)
    new = harmonize.canonicalize(pd.DataFrame({"country": ["UVK"], "date": pd.to_datetime(["2024-01-01"]), "value": [2.0]}), "imf")
    res = store.update(path, new, start="1980-01-01", source="t", keys=keys, key_aliases=harmonize.CODE_ALIASES["imf"])
    assert res.written
    df = store.read(path)
    assert df["country"].tolist() == ["XKX", "XKX"] and df["date"].dt.year.tolist() == [2023, 2024]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    ids = {"fred.UNRATE", "imf.LUR", "wb.SL.UEM.TOTL.ZS", "imf.NGDPD", "wb.NY.GDP.MKTP.CD"}
    series = [s for s in catalog.load_series() if s.id in ids]
    months = pd.date_range("2024-01-01", "2025-06-01", freq="MS")

    def fred(s, start):
        return pd.DataFrame({"date": months, "value": [4.0] * 12 + [5.0] * 6}), {}

    def imf(s, start):
        v = 4.1 if s.id == "imf.LUR" else 4000.0  # 十億ドル
        return pd.DataFrame({"country": ["USA", "UVK"], "date": pd.to_datetime(["2024-01-01"] * 2), "value": [v, 1.0]}), {}

    def wb(s, start):
        v = 4.2 if s.id == "wb.SL.UEM.TOTL.ZS" else 4.0e12
        return pd.DataFrame({"country": ["USA"], "date": pd.to_datetime(["2024-01-01"]), "value": [v]}), {}

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": fred, "imf": imf, "wb": wb})
    for s in series:
        pipeline.refresh(s)
    pipeline.build_silver(series, catalog.load_sources())


def test_compare_aligns_frequency_units_and_codes(env):
    df = client.compare("unemployment_rate", countries=["USA"])
    wide = client.to_wide(df, columns="series_id").set_index("period")
    assert wide.loc["2024", "fred.UNRATE"] == 4.0  # 月次を年平均に
    assert "2025" not in wide.index or pd.isna(wide.loc["2025", "fred.UNRATE"])  # 終わっていない年は平均しない
    assert df["source"].nunique() == 3 and set(df["coverage"]) >= {"ilo_modelled", "national_definition"}
    gdp = client.to_wide(client.compare("gdp_usd", countries=["USA"]), columns="series_id")
    assert gdp["imf.NGDPD"].iloc[0] == gdp["wb.NY.GDP.MKTP.CD"].iloc[0] == 4.0e12  # 倍率をそろえる
    assert "XKX" in set(client.compare("unemployment_rate")["country"])  # IMF の UVK はコソボ（XKX）に


def test_concepts_list_and_unknown_concept(env):
    c = client.concepts().set_index("concept")
    assert "imf.LUR" in c.loc["unemployment_rate", "series"]
    with pytest.raises(KeyError):
        client.compare("nope")


def test_catalog_concepts_are_consistent():
    assert catalog.validate(catalog.load_series(), catalog.load_sources()) == []
