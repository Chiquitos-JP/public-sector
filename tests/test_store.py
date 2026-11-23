import pandas as pd
import pytest

from psdata import store


def frame(dates, values):
    return pd.DataFrame({"date": pd.to_datetime(dates), "value": values})


@pytest.fixture
def path(tmp_path):
    return tmp_path / "bronze" / "fred" / "fred.TEST.parquet"


def test_first_write_creates_store(path):
    res = store.update(path, frame(["2026-01-01", "2026-02-01"], [1.0, 2.0]), start="2026-01-01", source="t")
    assert res.written and res.rows_added == 2 and res.rows_revised == 0
    df = store.read(path)
    assert list(df.columns) == ["date", "value", "fetched_at", "first_seen_at"]
    assert str(df["fetched_at"].dt.tz) == "UTC"


def test_revision_in_window_overwrites_and_is_logged(path):
    store.update(path, frame(["2026-01-01", "2026-02-01", "2026-03-01"], [1.0, 2.0, 3.0]), start="2026-01-01", source="t")
    res = store.update(path, frame(["2026-02-01", "2026-03-01", "2026-04-01"], [2.0, 3.1, 4.0]), start="2026-02-01", source="t")
    assert res.written and res.rows_added == 1 and res.rows_revised == 1
    df = store.read(path).set_index("date")["value"]
    assert df[pd.Timestamp("2026-03-01")] == 3.1
    assert df[pd.Timestamp("2026-01-01")] == 1.0
    log = pd.read_parquet(store.revisions_path(path))
    assert log.iloc[0][["old_value", "new_value"]].tolist() == [3.0, 3.1]


def test_unchanged_rows_keep_original_fetched_at(path):
    store.update(path, frame(["2026-01-01"], [1.0]), start="2026-01-01", source="t")
    first = store.read(path)["fetched_at"].iloc[0]
    store.update(path, frame(["2026-01-01", "2026-02-01"], [1.0, 2.0]), start="2026-01-01", source="t")
    assert store.read(path)["fetched_at"].iloc[0] == first
    assert not store.revisions_path(path).exists()


def test_rows_missing_from_refetch_are_kept(path):
    store.update(path, frame(["2026-01-01", "2026-02-01"], [1.0, 2.0]), start="2026-01-01", source="t")
    res = store.update(path, frame(["2026-02-01"], [2.0]), start="2026-01-01", source="t")
    assert res.written and len(store.read(path)) == 2
    assert any("返ってこなかった" in w for w in res.report.warnings)


def test_rows_before_start_are_ignored(path):
    store.update(path, frame(["2026-01-01", "2026-02-01"], [1.0, 2.0]), start="2026-01-01", source="t")
    store.update(path, frame(["2026-01-01", "2026-02-01"], [9.0, 2.0]), start="2026-02-01", source="t")
    assert store.read(path)["value"].iloc[0] == 1.0


def test_invalid_fresh_is_not_written(path):
    store.update(path, frame(["2026-01-01"], [1.0]), start="2026-01-01", source="t")
    res = store.update(path, frame(["2026-02-01", "2026-02-01"], [2.0, 2.0]), start="2026-01-01", source="t")
    assert not res.written
    assert len(store.read(path)) == 1


def test_future_dates_are_rejected(path):
    future = (pd.Timestamp.now() + pd.Timedelta(days=30)).normalize()
    res = store.update(path, frame([future], [1.0]), start="2026-01-01", source="t")
    assert not res.written and not path.exists()


def test_big_revision_warns(path):
    store.update(path, frame(["2026-01-01"], [100.0]), start="2026-01-01", source="t")
    res = store.update(path, frame(["2026-01-01"], [120.0]), start="2026-01-01", source="t")
    assert res.written and any("改定" in w for w in res.report.warnings)


def test_fetch_start_uses_window(path):
    assert store.fetch_start(path, default_start="2006-01-01", window=pd.DateOffset(years=5)) == "2006-01-01"
    store.update(path, frame(["2026-10-01"], [1.0]), start="2026-10-01", source="t")
    assert store.fetch_start(path, default_start="2006-01-01", window=pd.DateOffset(years=5)) == "2021-10-01"
    assert store.fetch_start(path, default_start="2025-01-01", window=pd.DateOffset(years=5)) == "2025-01-01"


def test_provenance_is_recorded(path):
    store.update(path, frame(["2026-01-01"], [1.0]), start="2026-01-01", source="fred", params={"series_id": "X"})
    import json

    rec = json.loads(store.provenance_path(path).read_text())["records"][-1]
    assert rec["source"] == "fred" and rec["params"]["fetch_start"] == "2026-01-01"


def test_provenance_keeps_every_record(path):
    for i in range(250):
        store.update(path, frame(["2026-01-01"], [1.0]), start="2026-01-01", source="t")
    import json

    assert len(json.loads(store.provenance_path(path).read_text())["records"]) == 250


def test_first_seen_at_is_kept_and_new_rows_get_now(path):
    store.update(path, frame(["2026-01-01"], [1.0]), start="2026-01-01", source="t")
    first = store.read(path)["first_seen_at"].iloc[0]
    store.update(path, frame(["2026-01-01", "2026-02-01"], [1.5, 2.0]), start="2026-01-01", source="t")
    df = store.read(path).set_index("date")
    assert df.loc["2026-01-01", "first_seen_at"] == first  # 改定されても初めて取った時刻は変わらない
    assert df.loc["2026-01-01", "fetched_at"] > first
    assert df.loc["2026-02-01", "first_seen_at"] > first


def test_old_store_without_first_seen_is_backfilled(path):
    store.update(path, frame(["2026-01-01", "2026-02-01"], [1.0, 2.0]), start="2026-01-01", source="t")
    store.update(path, frame(["2026-01-01", "2026-02-01"], [1.0, 2.5]), start="2026-01-01", source="t")
    old = store.read(path).drop(columns="first_seen_at")
    old.to_parquet(path, index=False)
    import json

    first_fetch = pd.Timestamp(json.loads(store.provenance_path(path).read_text())["records"][0]["fetched_at"])
    store.update(path, frame(["2026-01-01", "2026-02-01"], [1.0, 2.5]), start="2026-01-01", source="t")
    df = store.read(path).set_index("date")
    assert df.loc["2026-01-01", "first_seen_at"] == df.loc["2026-01-01", "fetched_at"]
    assert df.loc["2026-02-01", "first_seen_at"] == first_fetch
