"""系列ごとの特徴（プロファイル）と silver の記録票。"""
import json

import pandas as pd

from psdata import catalog, client, pipeline, profile, store


def _series(sid):
    return next(s for s in catalog.load_series() if s.id == sid)


def test_profile_finds_missing_month_revisions_and_freshness(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    s = _series("fred.UNRATE")
    path = tmp_path / "bronze" / "fred" / "fred.UNRATE.parquet"
    dates = pd.to_datetime(["2026-01-01", "2026-02-01", "2026-04-01"])  # 3 月が欠けている
    store.update(path, pd.DataFrame({"date": dates, "value": [4.0, 4.1, 4.3]}), start="2026-01-01", source="t")
    store.update(path, pd.DataFrame({"date": dates, "value": [4.0, 4.2, 4.3]}), start="2026-01-01", source="t")

    now = pd.Timestamp("2026-05-20", tz="UTC")
    r = profile.profile_series(s, now)
    assert r["missing_periods"] == 1 and r["missing_dates"] == "2026-03-01"
    assert r["n_revisions"] == 1 and abs(r["max_rel_revision"] - 0.1 / 4.1) < 1e-9
    assert r["last_observation_date"] == pd.Timestamp("2026-04-01")
    assert r["last_checked_at"] >= r["last_changed_at"] - pd.Timedelta(seconds=1)
    assert r["release_lag_days_now"] == 49 and not r["is_stale"]
    assert profile.profile_series(s, pd.Timestamp("2026-08-01", tz="UTC"))["is_stale"]


def test_profile_measures_gaps_per_country(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    s = _series("wb.SP.POP.TOTL")
    df = pd.DataFrame({
        "country": ["GTM", "GTM", "HND", "HND"],
        "date": pd.to_datetime(["2023-01-01", "2024-01-01", "2020-01-01", "2024-01-01"]),
        "value": [1.0, 2.0, 10.0, 30.0],
    })
    store.update(tmp_path / "bronze" / "wb" / f"{s.id}.parquet", df, start="1960-01-01", source="t", keys=("country", "date"))
    r = profile.profile_series(s)
    assert r["n_countries"] == 2 and r["n_countries_at_last"] == 2
    # 国をまたいで差を取らない: GTM は 1、HND は 20
    assert r["abs_change_max"] == 20.0 and r["max_gap_days"] == 1461


def test_build_silver_writes_profile_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    series = [_series("fred.UNRATE")]

    def fake(s, start):
        return pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "value": [4.0]}), {}

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": fake})
    pipeline.refresh(series[0])
    pipeline.build_silver(series, catalog.load_sources())
    m = client.manifest()
    assert m["schema_version"] == pipeline.SCHEMA_VERSION and m["built_by"] == {"actor": "ci", "ci_run_id": "123"}
    assert m["files"]["profile.parquet"]["rows"] == 1
    assert list(client.profile("fred.UNRATE")["series_id"]) == ["fred.UNRATE"]
    assert "特徴（実データから自動）" in client.describe("fred.UNRATE")


def test_provenance_records_who_which_code_and_raw(tmp_path, monkeypatch):
    import hashlib

    from psdata.sources._http import save_raw

    monkeypatch.setenv("PSDATA_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "777")
    s = _series("fred.UNRATE")

    def fake(series, start):
        save_raw("fred", "UNRATE", "observation_date,UNRATE\n2026-01-01,4.0\n", "csv")
        return pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "value": [4.0]}), {"route": "csv"}

    monkeypatch.setattr(pipeline, "FETCHERS", {"fred": fake})
    pipeline.refresh(s)
    rec = json.loads(store.provenance_path(tmp_path / "bronze" / "fred" / "fred.UNRATE.parquet").read_text())["records"][-1]
    assert rec["actor"]["kind"] == "ci" and rec["actor"]["ci_run_id"] == "777"
    assert rec["mode"] == "incremental" and rec["code"]["python"]
    (raw,) = rec["raw"]
    data = (tmp_path / "raw" / raw["path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == raw["sha256"] and raw["bytes"] == len(data)
