"""値の有効期間（vintages）と client.read(as_of=...)。"""
import pandas as pd
import pytest

from psdata import catalog, client, pipeline, store, vintages

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def _setup(tmp_path, monkeypatch):
    """M2 の 3 か月。2 月は 2 回改定、3 月は 12/10 に初めて届いた。"""
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    s = next(x for x in catalog.load_series() if x.id == "fred.M2SL")
    path = tmp_path / "bronze" / "fred" / "fred.M2SL.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
        "value": [100.0, 102.0, 103.0],
        "fetched_at": [T("2026-12-01"), T("2026-12-20"), T("2026-12-10")],
        "first_seen_at": [T("2026-12-01"), T("2026-12-01"), T("2026-12-10")],
    }).to_parquet(path, index=False)
    pd.DataFrame({
        "date": pd.to_datetime(["2026-02-01", "2026-02-01"]),
        "old_value": [101.0, 101.5],
        "new_value": [101.5, 102.0],
        "detected_at": [T("2026-12-05"), T("2026-12-20")],
    }).to_parquet(store.revisions_path(path), index=False)
    pipeline.build_silver([s], catalog.load_sources())
    return s


def test_vintages_split_at_each_revision(tmp_path, monkeypatch):
    s = _setup(tmp_path, monkeypatch)
    v = vintages.series_vintages(s)
    feb = v[v["date"] == "2026-02-01"].sort_values("valid_from")
    assert feb["value"].tolist() == [101.0, 101.5, 102.0]
    assert feb["valid_from"].tolist() == [T("2026-12-01"), T("2026-12-05"), T("2026-12-20")]
    assert feb["valid_to"].iloc[:2].tolist() == [T("2026-12-05"), T("2026-12-20")] and pd.isna(feb["valid_to"].iloc[2])
    assert len(v) == 5  # 1 月 1 + 2 月 3 + 3 月 1


def test_read_as_of_returns_what_was_known(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    at = lambda when: client.read("fred.M2SL", as_of=when)["value"].tolist()  # noqa: E731
    assert at("2026-12-02") == [100.0, 101.0]           # 3 月はまだ届いていない
    assert at("2026-12-06") == [100.0, 101.5]           # 1 回目の改定のあと
    assert at("2026-12-15") == [100.0, 101.5, 103.0]
    assert at("2026-12-31") == client.read("fred.M2SL")["value"].tolist()  # 今の値と同じ
    with pytest.raises(KeyError):
        client.read("fred.M2SL", as_of="2026-11-01")  # 取得を始める前


def test_as_of_country_filter_applies_only_to_panels(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    series = [x for x in catalog.load_series() if x.id in {"fred.UNRATE", "wb.SP.POP.TOTL"}]

    def single(s, start):
        return pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "value": [4.0]}), {}

    def panel(s, start):
        return pd.DataFrame({"country": ["GTM", "USA"], "date": pd.to_datetime(["2024-01-01"] * 2), "value": [1.0, 2.0]}), {}

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": single, "wb": panel})
    for s in series:
        pipeline.refresh(s)
    pipeline.build_silver(series, catalog.load_sources())
    now = pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=1)
    got = client.read(["fred.UNRATE", "wb.SP.POP.TOTL"], countries="central_america", as_of=now)
    assert sorted(zip(got["series_id"], got["country"])) == [("fred.UNRATE", "USA"), ("wb.SP.POP.TOTL", "GTM")]
    assert got.reset_index(drop=True).equals(client.read(["fred.UNRATE", "wb.SP.POP.TOTL"], countries="central_america"))
