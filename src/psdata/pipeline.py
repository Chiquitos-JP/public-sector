"""取得 → bronze 更新 → silver 統合。"""
from __future__ import annotations

import json
import os
import platform
from importlib import metadata
import subprocess
from datetime import datetime, timezone

import pandas as pd

from psdata import catalog, profile, store, vintages
from psdata.catalog import Series
from psdata.paths import bronze_path, reference_path, silver_dir
from psdata.sources import FETCHERS, REFERENCES, _http

OBSERVATION_COLUMNS = ["series_id", "country", "date", "value", "fetched_at"]


def last_fetched(series: Series) -> pd.Timestamp | None:
    """最後に取得を試みて書き込んだ時刻（provenance の最終記録）。"""
    return last_fetched_at(store.provenance_path(bronze_path(series.id)))


def last_fetched_at(pp) -> pd.Timestamp | None:
    if not pp.is_file():
        return None
    records = json.loads(pp.read_text(encoding="utf-8")).get("records") or []
    return pd.Timestamp(records[-1]["fetched_at"]) if records else None


def is_due(series: Series, now: pd.Timestamp | None = None) -> bool:
    """``fetch_every`` を過ぎていれば取る。指定が無ければ毎回取る。"""
    if not series.fetch_every:
        return True
    last = last_fetched(series)
    if last is None:
        return True
    now = now or pd.Timestamp(datetime.now(timezone.utc))
    # 毎日同じ時刻に動く CI で 1 日ずれないよう、半日の余裕を見る
    return now >= last + catalog.parse_window(series.fetch_every) - pd.Timedelta(hours=12)


def run_context() -> dict:
    """取得記録に添える「誰が・どの版で」。個人を特定する情報（ユーザー名・ホスト名）は入れない。"""
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    try:
        version = metadata.version("psdata")
    except metadata.PackageNotFoundError:
        version = ""
    return {
        "actor": {
            "kind": "ci" if run_id else "local",
            "ci_run_id": run_id,
            "ci_workflow": os.environ.get("GITHUB_WORKFLOW", ""),
            "ci_event": os.environ.get("GITHUB_EVENT_NAME", ""),
            "platform": f"{platform.system()}-{platform.machine()}",
        },
        "code": {"git_sha": _git_sha(), "psdata": version, "python": platform.python_version()},
    }


def refresh(series: Series, *, full: bool = False) -> store.UpdateResult:
    """1 系列を取り直して bronze を更新する。``full`` なら定義上の開始日から取り直す。"""
    path = bronze_path(series.id)
    start = series.start if full else store.fetch_start(
        path, default_start=series.start, window=catalog.parse_window(series.refetch_window)
    )
    _http.SAVED.clear()
    fresh, params = FETCHERS[series.distributor](series, start)
    context = {**run_context(), "mode": "full" if full else "incremental", "raw": list(_http.SAVED)}
    return store.update(
        path,
        fresh,
        start=start,
        source=series.distributor,
        params=params,
        label=series.id,
        keys=series.keys,
        # パネルは成長率など 0 をまたぐ指標が多く、前期比の警告は意味をなさない
        max_abs_pct_change=None if series.is_panel else 1.0,
        context=context,
        allow_future_days=series.allow_future_days,
    )


def refresh_references(distributors: set[str]) -> dict[str, int]:
    """パネルの配信元の国一覧を取り直す（系列ではないので置き換えるだけ）。"""
    counts = {}
    for d in sorted(distributors & set(REFERENCES)):
        df = REFERENCES[d]()
        if df.empty or df["country"].duplicated().any():
            raise RuntimeError(f"{d}: 国の一覧が空か重複している")
        path = reference_path(d, "countries")
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        counts[d] = len(df)
    return counts


def _frames(series: list[Series]) -> tuple[list[pd.DataFrame], list[pd.DataFrame]]:
    single, panel = [], []
    for s in series:
        df = store.read(bronze_path(s.id))
        if df is None:
            continue
        if s.is_panel:
            panel.append(df.assign(series_id=s.id)[OBSERVATION_COLUMNS])
        else:
            single.append(df.assign(series_id=s.id, country=s.country)[OBSERVATION_COLUMNS])
    return single, panel


def _concat(frames: list[pd.DataFrame], keys: list[str]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=OBSERVATION_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(keys).reset_index(drop=True)


def build_silver(series: list[Series], sources: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """bronze を silver に統合する。

    - ``observations.parquet`` 単一の国の系列（1 行は series_id / date で決まる。利用側との契約）
    - ``panel.parquet``        国をまたぐ系列（1 行は series_id / country / date で決まる）
    - ``series.parquet``       カタログ（両方の系列）
    - ``countries.parquet``    国・地域の一覧（パネルの国コードを引く表）
    - ``profile.parquet``      系列ごとの特徴（実物から自動で作る。``psdata.profile``）
    - ``vintages.parquet``     値の有効期間（その時点で知り得た値を読むため。``psdata.vintages``）
    - ``manifest.json``        この silver を作った時刻・コードの版・実行者・件数
    """
    single, panel = _frames(series)
    obs = _concat(single, ["series_id", "date"])
    pan = _concat(panel, ["series_id", "country", "date"])
    table = catalog.series_table(series, sources)
    out = silver_dir()
    out.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(out / "observations.parquet", index=False)
    table.to_parquet(out / "series.parquet", index=False)
    if panel:
        pan.to_parquet(out / "panel.parquet", index=False)
    refs = [pd.read_parquet(p) for d in REFERENCES if (p := reference_path(d, "countries")).is_file()]
    if refs:
        # 配信元が増えたら先に載っている方を優先する（今は世界銀行だけ）
        pd.concat(refs, ignore_index=True).drop_duplicates("country").to_parquet(out / "countries.parquet", index=False)
    prof = profile.build(series)
    prof.to_parquet(out / "profile.parquet", index=False)
    vin = vintages.build(series)
    vin.to_parquet(out / "vintages.parquet", index=False)
    _write_manifest(out, obs, pan, table, prof, vin)
    return obs, pan, table


#: silver の形の版。列や意味を変えたら上げて、利用側へ先に知らせる
SCHEMA_VERSION = 3  # 2: series.parquet に単位の列 / 3: series.parquet に projection_years


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True,
            cwd=os.path.dirname(__file__),
        ).stdout.strip()
    except Exception:
        return ""


def _write_manifest(out, obs, pan, table, prof, vin) -> None:
    """silver 全体の記録票。誰が・いつ・どの版のコードで作ったかと、ファイルごとの件数。"""
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "built_by": {"actor": "ci" if run_id else "local", "ci_run_id": run_id},
        "files": {
            "observations.parquet": {"rows": len(obs), "series": int(obs["series_id"].nunique())},
            "panel.parquet": {"rows": len(pan), "series": int(pan["series_id"].nunique())},
            "series.parquet": {"rows": len(table)},
            "profile.parquet": {"rows": len(prof), "stale": prof.loc[prof["is_stale"], "series_id"].tolist()},
            "vintages.parquet": {
                "rows": len(vin),
                "since": vin["valid_from"].min().isoformat() if len(vin) else None,
            },
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
