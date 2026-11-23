"""切り出し（tidy な表・計算した値）とデータカード付きの書き出し。"""
import json

import pandas as pd
import pytest

from psdata import catalog, client, extract, pipeline


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    ids = {"fred.CPIAUCSL", "fred.DGS10", "wb.NY.GDP.MKTP.KD.ZG"}
    series = [s for s in catalog.load_series() if s.id in ids]
    months = pd.date_range("2024-01-01", "2025-12-01", freq="MS").delete(21)  # 2025-10 が欠けている

    def fred(s, start):
        if s.id == "fred.CPIAUCSL":
            return pd.DataFrame({"date": months, "value": [100.0 + i for i in range(len(months))]}), {}
        days = pd.bdate_range("2024-01-02", "2025-01-10")
        return pd.DataFrame({"date": days, "value": 4.0}), {}

    def wb(s, start):
        return pd.DataFrame({
            "country": ["GTM", "GTM", "MEX"], "date": pd.to_datetime(["2023-01-01", "2024-01-01", "2024-01-01"]),
            "value": [3.5, 3.7, 1.2],
        }), {}

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": fred, "wb": wb})
    for s in series:
        pipeline.refresh(s)
    pipeline.build_silver(series, catalog.load_sources())
    return tmp_path


def test_tidy_columns_and_yoy_skips_missing_base(env):
    df = client.extract("fred.CPIAUCSL", measures=["value", "yoy_pct"])
    assert list(df.columns) == ["series_id", "title_ja", "country", "country_name", "date", "period", "measure", "value", "unit"]
    yoy = df[df["measure"] == "yoy_pct"].set_index("period")["value"]
    assert "2024-12" not in yoy.index  # 1 年前が無い
    assert round(yoy["2025-01"], 6) == round((112 / 100 - 1) * 100, 6)
    assert "2025-10" not in yoy.index  # 元の値が欠けている月は行が無い
    assert (df.loc[df["measure"] == "yoy_pct", "unit"] == "%").all()


def test_start_filters_after_computing_so_first_year_has_yoy(env):
    df = client.extract("fred.CPIAUCSL", start="2025-01-01", measures=["yoy_pct"])
    assert df["date"].min() == pd.Timestamp("2025-01-01") and len(df) > 0


def test_daily_yoy_uses_last_value_on_or_before(env):
    df = client.extract("fred.DGS10", measures=["yoy_diff"])
    first = df.iloc[0]
    assert first["value"] == 0.0 and first["date"] >= pd.Timestamp("2025-01-02")


def test_panel_measures_are_per_country_and_unknown_measure_fails(env):
    df = client.extract("wb.NY.GDP.MKTP.KD.ZG", measures=["yoy_diff"])
    assert df[["country", "value"]].round(6).values.tolist() == [["GTM", 0.2]]  # MEX は前年が無い
    with pytest.raises(KeyError):
        client.extract("wb.NY.GDP.MKTP.KD.ZG", measures=["growth"])


def test_export_writes_card_with_credits(env, tmp_path):
    df = client.extract(["fred.CPIAUCSL", "wb.NY.GDP.MKTP.KD.ZG"], countries=["GTM"], measures=["value"])
    files = client.export(df, str(tmp_path / "out" / "data.xlsx"))
    assert [p.split("/")[-1] for p in files] == ["data.xlsx", "data.card.md", "data.card.json"]
    card = json.loads(open(files[2], encoding="utf-8").read())
    assert card["query"]["countries"] == ["GTM"] and card["rows"] == len(df)
    assert any("World Bank" in c for c in card["credits"]) and any("Bureau of Labor" in c for c in card["credits"])
    sheets = pd.read_excel(files[0], sheet_name=None)
    assert set(sheets) == {"data", "card"} and len(sheets["data"]) == len(df)
    csv = client.export(df, str(tmp_path / "out" / "data.csv"))[0]
    assert open(csv, "rb").read(3) == b"\xef\xbb\xbf"  # Excel で開ける BOM 付き


def test_to_wide(env):
    df = client.extract("wb.NY.GDP.MKTP.KD.ZG", measures=["value"])
    wide = client.to_wide(df, columns="country")
    assert {"GTM", "MEX", "date", "period"} <= set(wide.columns)
