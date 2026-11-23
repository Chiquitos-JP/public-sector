"""国をまたぐ系列（パネル）: ストア・世界銀行の取得・silver の分離・client の国指定。"""
import pandas as pd
import pytest

from psdata import catalog, client, pipeline, store
from psdata.sources import wb

KEYS = ("country", "date")


def panel(rows):
    return pd.DataFrame(rows, columns=["country", "date", "value"]).assign(date=lambda d: pd.to_datetime(d["date"]))


@pytest.fixture
def path(tmp_path):
    return tmp_path / "bronze" / "wb" / "wb.TEST.parquet"


def test_panel_revision_is_per_country(path):
    store.update(path, panel([("GTM", "2023-01-01", 3.5), ("HND", "2023-01-01", 3.6)]), start="2000-01-01", source="t", keys=KEYS, max_abs_pct_change=None)
    res = store.update(
        path,
        panel([("GTM", "2023-01-01", 3.5), ("HND", "2023-01-01", 3.7), ("HND", "2024-01-01", 3.6)]),
        start="2000-01-01", source="t", keys=KEYS, max_abs_pct_change=None,
    )
    assert res.written and res.rows_added == 1 and res.rows_revised == 1
    df = store.read(path)
    assert list(df.columns) == ["country", "date", "value", "fetched_at", "first_seen_at"]
    log = pd.read_parquet(store.revisions_path(path))
    assert log[["country", "old_value", "new_value"]].values.tolist() == [["HND", 3.6, 3.7]]


def test_panel_same_date_in_two_countries_is_not_a_duplicate(path):
    rep = store.validate_frame(panel([("GTM", "2023-01-01", 1.0), ("HND", "2023-01-01", 2.0)]), label="t", keys=KEYS)
    assert rep.ok
    rep = store.validate_frame(panel([("GTM", "2023-01-01", 1.0), ("GTM", "2023-01-01", 2.0)]), label="t", keys=KEYS)
    assert not rep.ok


def test_wb_parse_drops_nulls_and_codeless_rows():
    rows = [
        {"countryiso3code": "GTM", "date": "2024", "value": 3.7},
        {"countryiso3code": "GTM", "date": "2025", "value": None},
        {"countryiso3code": "", "date": "2024", "value": 1.0},
        {"countryiso3code": "LCN", "date": "2024", "value": 2.2},
    ]
    df = wb.parse(rows)
    assert df[["country", "value"]].values.tolist() == [["GTM", 3.7], ["LCN", 2.2]]
    assert df["date"].iloc[0] == pd.Timestamp("2024-01-01")


def test_wb_countries_marks_aggregates():
    rows = [
        {"id": "GTM", "name": "Guatemala", "region": {"id": "LCN", "value": "Latin America & Caribbean "},
         "incomeLevel": {"id": "UMC", "value": "Upper middle income"}, "lendingType": {"id": "IBD", "value": "IBRD"}},
        {"id": "LCN", "name": "Latin America & Caribbean", "region": {"id": "NA", "value": "Aggregates"},
         "incomeLevel": {"id": "NA", "value": "Aggregates"}, "lendingType": {"id": "", "value": "Aggregates"}},
    ]
    df = wb.parse_countries(rows).set_index("country")
    assert not df.loc["GTM", "is_aggregate"] and df.loc["GTM", "region"] == "LCN"
    assert df.loc["LCN", "is_aggregate"] and df.loc["LCN", "region"] == ""


def test_is_due_follows_fetch_every(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    s = next(x for x in catalog.load_series() if x.id == "wb.SP.POP.TOTL")
    assert pipeline.is_due(s)
    store.update(tmp_path / "bronze" / "wb" / f"{s.id}.parquet", panel([("GTM", "2024-01-01", 1.0)]),
                 start="1960-01-01", source="t", keys=KEYS)
    now = pd.Timestamp.now(tz="UTC")
    assert not pipeline.is_due(s, now + pd.Timedelta(days=3))
    assert pipeline.is_due(s, now + pd.Timedelta(days=7))


def test_groups_are_valid():
    assert catalog.validate_groups(catalog.load_groups()) == []
    assert catalog.validate_groups({"x": {"countries": ["GTM", "GTM"]}}) != []
    assert catalog.validate_groups({"x": {"countries": ["ZZZ"]}}, known={"GTM"}) != []


def test_panel_goes_to_its_own_file_and_client_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    ids = {"fred.UNRATE", "wb.NY.GDP.MKTP.KD.ZG"}
    series = [s for s in catalog.load_series() if s.id in ids]

    def fake_single(s, start):
        return pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "value": [4.0]}), {}

    def fake_panel(s, start):
        return panel([("GTM", "2024-01-01", 3.7), ("HND", "2024-01-01", 3.6), ("MEX", "2024-01-01", 1.2)]), {}

    def fake_countries():
        return pd.DataFrame({
            "country": ["GTM", "HND", "MEX", "LCN"], "name": ["", "", "", ""],
            "region": ["LCN", "LCN", "LCN", ""], "region_name": ["", "", "", "Aggregates"],
            "income": ["UMC", "LMC", "UMC", ""], "income_name": ["", "", "", ""], "lending": ["", "", "", ""],
            "is_aggregate": [False, False, False, True],
        })

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": fake_single, "wb": fake_panel})
    monkeypatch.setattr(pipeline, "REFERENCES", {"wb": fake_countries})
    pipeline.refresh_references({"wb"})
    for s in series:
        assert pipeline.refresh(s).written
    obs, pan, _ = pipeline.build_silver(series, catalog.load_sources())

    # 利用側との契約: observations は単一の国の系列だけで、series_id / date が重ならない
    assert set(obs["series_id"]) == {"fred.UNRATE"}
    assert not obs.duplicated(["series_id", "date"]).any()
    assert set(pan["country"]) == {"GTM", "HND", "MEX"}

    got = client.read("wb.NY.GDP.MKTP.KD.ZG", countries="central_america")
    assert set(got["country"]) == {"GTM", "HND"}
    got = client.read("wb.NY.GDP.MKTP.KD.ZG", countries=["income:UMC"])
    assert set(got["country"]) == {"GTM", "MEX"}
    assert len(client.read("wb.NY.GDP.MKTP.KD.ZG")) == 3
    mixed = client.read(["fred.UNRATE", "wb.NY.GDP.MKTP.KD.ZG"], countries=["MEX"])
    assert sorted(mixed["country"]) == ["MEX", "USA"]
    with pytest.raises(KeyError):
        client.read("wb.NY.GDP.MKTP.KD.ZG", countries="region:XXX")
