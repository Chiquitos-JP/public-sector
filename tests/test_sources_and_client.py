import pandas as pd
import pytest

from psdata import catalog, client, pipeline
from psdata.sources import boj, fred


def test_fred_csv_drops_missing_marker():
    df = fred.parse_csv("observation_date,UNRATE\n2026-01-01,4.0\n2026-02-01,.\n2026-03-01,4.2\n")
    assert df["value"].tolist() == [4.0, 4.2]
    assert df["value"].dtype == "float64"


def test_fred_json():
    df = fred.parse_json({"observations": [{"date": "2026-01-01", "value": "1.5"}, {"date": "2026-01-02", "value": "."}]})
    assert df["value"].tolist() == [1.5]


def test_boj_drops_holidays_and_raises_on_error():
    body = {"STATUS": 200, "RESULTSET": [{"VALUES": {"SURVEY_DATES": [20261117, 20261118, 20261119], "VALUES": [159.3, None, 159.4]}}]}
    assert boj.parse(body)["value"].tolist() == [159.3, 159.4]
    with pytest.raises(RuntimeError):
        boj.parse({"STATUS": 400, "MESSAGE": "bad"})


def test_pipeline_and_client_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    series = [s for s in catalog.load_series() if s.id in {"fred.UNRATE", "boj.FM08.FXERD04"}]
    sources = catalog.load_sources()

    def fake(s, start):
        return pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-02-01"]), "value": [1.0, 2.0]}), {}

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": fake, "boj": fake})
    for s in series:
        assert pipeline.refresh(s).written
    pipeline.build_silver(series, sources)

    one = client.read("fred.UNRATE", start="2026-02-01")
    assert list(one.columns) == ["date", "value"] and one["value"].tolist() == [2.0]
    many = client.read(["fred.UNRATE", "boj.FM08.FXERD04"])
    assert set(many["country"]) == {"USA", "JPN"} and len(many) == 4
    with pytest.raises(KeyError):
        client.read("fred.SP500")


def test_raw_is_saved_gzipped_and_unchanged(tmp_path, monkeypatch):
    import gzip

    from psdata.sources._http import save_raw

    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    body = '{"a": "日本語", "v": [1, 2, 3]}' * 100
    path = save_raw("wb", "NY.GDP", body, "p1.json")
    assert path.name.endswith(".p1.json.gz") and path.parent == tmp_path / "raw" / "wb" / "NY.GDP"
    assert gzip.decompress(path.read_bytes()).decode("utf-8") == body
    assert path.stat().st_size < len(body.encode("utf-8"))
