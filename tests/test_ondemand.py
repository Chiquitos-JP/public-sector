"""必要なときに引き出す: search / pull / pulled / draft。取得元には出ていかない。"""
import pandas as pd
import pytest

from psdata import client, ondemand, pipeline
from psdata.paths import cache_dir


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    calls = []

    def fake_wb(series, start):
        calls.append(series.id)
        return pd.DataFrame({"country": ["GTM", "HND"], "date": pd.to_datetime(["2024-01-01"] * 2), "value": [90.0, 95.0]}), {}

    def fake_fred(series, start):
        calls.append(series.id)
        return pd.DataFrame({"date": pd.to_datetime(["2026-10-01"]), "value": [159293.0]}), {}

    monkeypatch.setattr(ondemand, "FETCHERS", {"wb": fake_wb, "fred": fake_fred, "boj": fake_fred})
    lst = pd.DataFrame({
        "code": ["SE.PRM.ENRR", "SE.PRM.ENRR.FE", "FP.CPI.TOTL.ZG"],
        "name": ["School enrollment, primary (% gross)", "School enrollment, primary, female (% gross)",
                 "Inflation, consumer prices (annual %)"],
        "unit": ["", "", ""], "source_note": ["Gross enrollment ratio", "", ""],
        "source_org": ["UNESCO", "", "IMF"], "topics": ["Education", "Education", "Economy"],
    })
    (cache_dir()).mkdir(parents=True)
    lst.to_parquet(cache_dir() / "wb_indicators.parquet", index=False)
    return calls


def test_search_ranks_name_matches_and_marks_catalog(env):
    got = client.search("school enrollment primary")
    assert got["series_id"].tolist()[0] == "wb.SE.PRM.ENRR"
    assert not got["in_catalog"].any()
    assert client.search("inflation")["in_catalog"].tolist() == [True]


def test_pull_caches_locally_and_refetches_only_when_old(env, monkeypatch):
    df = client.pull("wb.SE.PRM.ENRR")
    assert list(df.columns) == ["country", "date", "value"] and len(df) == 2
    client.pull("wb.SE.PRM.ENRR")
    assert env == ["wb.SE.PRM.ENRR"]  # 2 回目は手元のものを返す
    monkeypatch.setattr(ondemand, "MAX_AGE", pd.Timedelta(seconds=-1))
    client.pull("wb.SE.PRM.ENRR")
    assert env == ["wb.SE.PRM.ENRR"] * 2
    assert ondemand.path_of("wb.SE.PRM.ENRR").is_file()
    assert "bronze" not in str(ondemand.path_of("wb.SE.PRM.ENRR"))  # 常備の bronze には混ぜない
    rec = client.pulled().set_index("series_id").loc["wb.SE.PRM.ENRR"]
    assert rec["pulls"] == 2 and rec["rows"] == 2


def test_pull_single_series_and_unknown_source(env):
    assert list(client.pull("fred.PAYEMS").columns) == ["date", "value"]
    with pytest.raises(KeyError):
        client.pull("xx.FOO")


def test_draft_is_a_catalog_entry_skeleton(env):
    import yaml

    (entry,) = yaml.safe_load(client.draft("wb.SE.PRM.ENRR", "u04"))
    assert entry["id"] == "wb.SE.PRM.ENRR" and entry["country"] == "ALL" and entry["used_by"] == ["u04"]
    assert entry["original_source"] == "UNESCO" and "TODO" in entry["definition"]
