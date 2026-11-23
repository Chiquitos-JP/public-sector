"""必要なときに必要なデータを引き出す。

- ``search``  取得元の指標を探す（世界銀行は WDI の全指標、FRED は ``FRED_API_KEY`` があれば）
- ``pull``    カタログに無い系列を、その場で取得元から取ってくる。**手元（``data/ondemand/``）にだけ置き、保存先には書かない**
- ``draft``   使い続けると決めた系列を常備に上げるための、カタログの下書き（YAML）

常備に上げる基準（AGENTS.md「系列の追加」）: 2 回以上の作業で使う・CI や別のプロジェクトから読む・
改定を追いたい、のどれかに当たり、収録基準（公的機関・ライセンス・利用先）を満たすもの。
取り置き（pull）の段階では収録基準を確かめていない。とくに FRED には再配布できない系列が混ざる。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from psdata import catalog, pipeline, store
from psdata.catalog import Series
from psdata.paths import cache_dir, ondemand_dir
from psdata.sources import FETCHERS, _http, fred, wb

#: 取り置きを取り直すまでの時間。これより新しければ手元のものを返す
MAX_AGE = pd.Timedelta(hours=20)
#: 指標一覧のキャッシュを取り直すまでの日数
LIST_MAX_AGE = pd.Timedelta(days=30)

#: 配信元ごとの既定の国（パネルでない配信元）
_DEFAULT_COUNTRY = {"fred": "USA", "boj": "JPN"}


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def _wb_list(refresh: bool = False) -> pd.DataFrame:
    path = cache_dir() / "wb_indicators.parquet"
    if not refresh and path.is_file():
        age = _now() - pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")
        if age < LIST_MAX_AGE:
            return pd.read_parquet(path)
    df = wb.fetch_indicator_list()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def search(text: str, source: str = "wb", limit: int = 30) -> pd.DataFrame:
    """取得元の指標をキーワードで探す。結果の ``series_id`` をそのまま ``pull`` に渡せる。

    ``in_catalog`` が True のものは常備済み（``client.read`` で読める）。
    """
    if source == "wb":
        df = _wb_list()
        words = [w.lower() for w in text.split()]
        hay = (df["code"] + " " + df["name"] + " " + df["topics"] + " " + df["source_note"]).str.lower()
        hit = pd.Series(True, index=df.index)
        for w in words:
            hit &= hay.str.contains(w, regex=False)
        # 名前に全部の語が当たるものを先に、その中では名前が短い（＝総合的な）ものを先に
        name_hit = df["name"].str.lower().apply(lambda n: all(w in n for w in words))
        out = (
            df[hit].assign(_rank=~name_hit[hit], _len=df["name"].str.len())
            .sort_values(["_rank", "_len", "code"]).drop(columns=["_rank", "_len"]).head(limit)
        )
    elif source == "fred":
        out = fred.search(text, limit=limit)
    else:
        raise KeyError(f"検索できる取得元は wb / fred: {source!r}")
    stocked = {s.id for s in catalog.load_series()}
    out = out.assign(series_id=f"{source}." + out["code"])
    out = out.assign(in_catalog=out["series_id"].isin(stocked))
    cols = ["series_id", "name", "unit", "in_catalog"] + [c for c in ["freq", "topics", "source_org"] if c in out.columns]
    return out[cols + ["source_note"]].reset_index(drop=True)


def _adhoc(series_id: str, country: str | None, start: str) -> Series:
    dist = series_id.split(".", 1)[0]
    if dist not in FETCHERS:
        raise KeyError(f"取得元 {dist!r} は扱っていない（{sorted(FETCHERS)}）")
    panel = dist == "wb"
    return Series(
        id=series_id, title_ja="", title_en="", creator="", distributor=dist,
        country=catalog.PANEL if panel else (country or _DEFAULT_COUNTRY[dist]),
        freq="A" if panel else "D", unit="", seasonal_adjustment="", tier="on_demand",
        refetch_window="100Y", start=start, used_by=(),
    )


def path_of(series_id: str):
    return ondemand_dir() / series_id.split(".", 1)[0] / f"{series_id}.parquet"


def pull(
    series_id: str,
    *,
    country: str | None = None,
    start: str = "1960-01-01",
    refresh: bool = False,
) -> pd.DataFrame:
    """カタログに無い系列をその場で取ってきて返す（手元に取り置き、次からはそれを返す）。

    常備の系列ならそのまま ``client.read`` と同じものを返す。
    単一の系列は date / value、世界銀行（パネル）は country / date / value。
    """
    stocked = {s.id: s for s in catalog.load_series()}
    if series_id in stocked:
        from psdata import client

        return client.read(series_id)
    s = _adhoc(series_id, country, start)
    path = path_of(series_id)
    last = pipeline.last_fetched_at(store.provenance_path(path))
    if refresh or last is None or _now() - last > MAX_AGE:
        _http.SAVED.clear()
        fresh, params = FETCHERS[s.distributor](s, start)
        if fresh.empty:
            raise KeyError(f"{series_id}: 取得元から値が返ってこなかった（コードを確かめる）")
        context = {**pipeline.run_context(), "mode": "on_demand", "raw": list(_http.SAVED)}
        res = store.update(path, fresh, start=start, source=s.distributor, params=params, label=series_id,
                           keys=s.keys, max_abs_pct_change=None, context=context)
        if not res.written:
            raise RuntimeError(f"{series_id}: 検証で止めた: {res.report.errors}")
    df = store.read(path)
    cols = ["country", "date", "value"] if s.is_panel else ["date", "value"]
    return df[cols].reset_index(drop=True)


def pulled() -> pd.DataFrame:
    """手元に取り置いた系列の一覧（いつ取ったか・件数・期間）。常備に上げる候補を見るのに使う。"""
    rows = []
    for p in sorted(ondemand_dir().glob("*/*.parquet")):
        if p.name.endswith(store.REVISIONS_SUFFIX):
            continue
        df = store.read(p)
        recs = json.loads(store.provenance_path(p).read_text(encoding="utf-8"))["records"] if store.provenance_path(p).is_file() else []
        rows.append({
            "series_id": p.stem,
            "rows": 0 if df is None else len(df),
            "first_date": None if df is None else df["date"].min(),
            "last_date": None if df is None else df["date"].max(),
            "pulls": len(recs),
            "last_pulled_at": recs[-1]["fetched_at"] if recs else None,
        })
    return pd.DataFrame(rows)


def draft(series_id: str, used_by: str = "u??") -> str:
    """常備に上げるためのカタログの下書き（YAML）。definition と notes は日本語で書き直して確かめる。"""
    dist, code = series_id.split(".", 1)
    if dist == "wb":
        meta = _wb_list()
        r = meta[meta["code"] == code]
        if r.empty:
            raise KeyError(f"WDI に無い指標: {code}")
        r = r.iloc[0]
        lines = [
            f"- id: {series_id}",
            "  title_ja: TODO",
            f'  title_en: "{r["name"]}"',
            "  definition: \"TODO（何を測っているか。原文: " + r["source_note"][:200].replace('"', "'") + "…）\"",
            "  notes:",
            "    - \"TODO（実データで欠けた期間・定義の変更を確かめてから書く）\"",
            f"  source_url: https://data.worldbank.org/indicator/{code}",
            "  creator: worldbank", "  distributor: wb", "  country: ALL", "  freq: A",
            f'  unit: "{r["unit"] or "TODO"}"',
            '  unit_kind: "TODO（percent / index / currency / fx / count）"',
            '  unit_of: "TODO（% なら何に対する比。GDP など。無ければ行ごと消す）"',
            "  seasonal_adjustment: NSA", "  tier: stocked",
            "  refetch_window: 70Y", "  fetch_every: 7D", '  start: "1960-01-01"',
            f'  original_source: "{r["source_org"][:120]}"' if r["source_org"] else "",
            "  used_by:", f"    - {used_by}",
        ]
        return "\n".join(x for x in lines if x)
    s = _adhoc(series_id, None, "2006-01-01")
    return "\n".join([
        f"- id: {series_id}", "  title_ja: TODO", "  title_en: TODO", '  definition: "TODO"', "  notes:", '    - "TODO"',
        f"  source_url: {'https://fred.stlouisfed.org/series/' + code if dist == 'fred' else 'https://www.stat-search.boj.or.jp/'}",
        "  creator: TODO（sources.yml の creators。FRED は作成元で判断し、著作権つきは入れない）",
        f"  distributor: {dist}", f"  country: {s.country}", "  freq: TODO", '  unit: "TODO"', "  unit_kind: TODO", "  seasonal_adjustment: TODO",
        "  tier: stocked", "  refetch_window: TODO", '  start: "2006-01-01"', "  used_by:", f"    - {used_by}",
    ])
