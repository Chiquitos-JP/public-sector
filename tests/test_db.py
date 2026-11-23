"""DuckDB のビュー（client.sql）。手元の silver だけで確かめる（保存先には出ていかない）。"""
import pandas as pd
import pytest

from psdata import catalog, client, pipeline


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    ids = {"fred.UNRATE", "wb.SP.POP.TOTL"}
    series = [s for s in catalog.load_series() if s.id in ids]

    def single(s, start):
        return pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-02-01"]), "value": [4.0, 4.1]}), {}

    def panel(s, start):
        return pd.DataFrame({"country": ["JPN", "USA", "GTM"], "date": pd.to_datetime(["2024-01-01"] * 3), "value": [1.0, 2.0, 3.0]}), {}

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": single, "wb": panel})
    for s in series:
        pipeline.refresh(s)
    pipeline.build_silver(series, catalog.load_sources())


def test_obs_unions_single_and_panel(env):
    got = client.sql("select series_id, count(*) n from obs group by 1 order by 1")
    assert got.values.tolist() == [["fred.UNRATE", 2], ["wb.SP.POP.TOTL", 3]]


def test_groups_view_joins_panel(env):
    got = client.sql(
        "select o.country from obs o join groups g on g.country = o.country and g.\"group\" = 'g7' "
        "where o.series_id = 'wb.SP.POP.TOTL' order by 1"
    )
    assert got["country"].tolist() == ["JPN", "USA"]


def test_as_of_macro_matches_client_read(env):
    t = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=1)).isoformat()
    a = client.sql("select date, value from as_of(?) where series_id = 'fred.UNRATE' order by date", [t])
    b = client.read("fred.UNRATE", as_of=t)
    assert a["value"].tolist() == b["value"].tolist() == [4.0, 4.1]
    assert client.sql("select count(*) n from as_of('2000-01-01')")["n"].iloc[0] == 0


def test_catalog_and_profile_views(env):
    assert set(client.sql("select series_id from series")["series_id"]) >= {"fred.UNRATE", "wb.SP.POP.TOTL"}
    assert client.sql("select is_stale from profile where series_id = 'fred.UNRATE'")["is_stale"].iloc[0] in (True, False)


def test_unknown_source_is_rejected(env):
    with pytest.raises(KeyError):
        client.sql("select 1", source="s3")
