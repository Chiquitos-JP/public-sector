#!/usr/bin/env python3
"""持っているデータの表と系列の一覧を、目録（ダッシュボード）用の JSON に書き出す（再取得しない）。

    python3 scripts/catalog_summary.py [出力先のフォルダ]   # 既定は data/exports/catalog/

書くもの:
- series.json  系列ごとの 1 行（何を・どこから・どの期間・どの国・鮮度・改定・ライセンス）
- tables.json  silver の表ごとの 1 行（何の表か・1 行の単位・列・行数）

どちらも ``{rows, meta}`` の形で、meta にこのスクリプト自身を入れる（同じ結果を作り直せるように）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from psdata import catalog, client
from psdata.paths import REPO_ROOT, silver_dir

TABLES = [
    ("observations.parquet", "単一の国の系列の観測値", "series_id × date", "利用側との契約（列・意味・系列 id は変える前に知らせる）"),
    ("panel.parquet", "国をまたぐ系列の観測値", "series_id × country × date", "世界銀行など。countries で国を選ぶ"),
    ("series.parquet", "系列のカタログ（宣言）", "series_id", "定義・前提・出典・ライセンス（手で書く）"),
    ("profile.parquet", "系列ごとの特徴（観察）", "series_id", "期間・欠け・値域・変化幅・公表の遅れ・改定・鮮度（毎回自動）"),
    ("vintages.parquet", "値の有効期間", "series_id × country × date × valid_from", "その時点で知り得た値（as_of）を読む"),
    ("countries.parquet", "国・地域の一覧", "country", "世界銀行の地域・所得区分・集計値かどうか"),
]

DISTRIBUTOR_JA = {"fred": "FRED（米セントルイス連銀）", "boj": "日本銀行", "wb": "世界銀行", "imf": "IMF（WEO）"}


def _date(x) -> str | None:
    return None if pd.isna(x) else pd.Timestamp(x).strftime("%Y-%m-%d")


def series_rows() -> list[dict]:
    cat = client.catalog().set_index("series_id")
    prof = client.profile().set_index("series_id")
    defs = {s.id: s for s in catalog.load_series()}
    rows = []
    for sid, r in cat.iterrows():
        p = prof.loc[sid] if sid in prof.index else None
        s = defs[sid]
        rows.append({
            "series_id": sid,
            "title_ja": r["title_ja"],
            "source": DISTRIBUTOR_JA.get(s.distributor, s.distributor),
            "creator": r["creator"],
            "scope": "各国（パネル）" if r["country"] == catalog.PANEL else r["country"],
            "freq": {"D": "日次", "W": "週次", "M": "月次", "Q": "四半期", "A": "年次"}[r["freq"]],
            "unit": r["unit"],
            "first_date": None if p is None else _date(p["first_date"]),
            "last_date": None if p is None else _date(p["last_observation_date"]),
            "rows": None if p is None else int(p["n_obs"]),
            "countries": None if p is None else int(p["n_countries"]),
            "release_lag_days": None if p is None or pd.isna(p["release_lag_days_median"]) else int(p["release_lag_days_median"]),
            "revisions": None if p is None else int(p["n_revisions"]),
            "status": "—" if p is None else ("古い" if bool(p["is_stale"]) else "最新"),
            "license": r["license"],
            "credit": r["credit"],
            "fetch": "週 1 回" if s.fetch_every == "7D" else "毎日",
            "concept": r.get("concept", "") or "",
            "coverage": r.get("coverage", "") or "",
            "definition": r["definition"],
        })
    return rows


def table_rows() -> list[dict]:
    rows = []
    for name, what, grain, note in TABLES:
        path = silver_dir() / name
        if not path.is_file():
            continue
        df = pd.read_parquet(path)
        rows.append({
            "table": f"silver/{name}",
            "what": what,
            "grain": grain,
            "columns": ", ".join(df.columns),
            "rows": len(df),
            "note": note,
        })
    return rows


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "data" / "exports" / "catalog"
    out.mkdir(parents=True, exist_ok=True)
    m = client.manifest()
    code = Path(__file__).read_text(encoding="utf-8")
    meta = [
        {"label": "catalog_summary.py", "value": code, "type": "code", "lang": "python"},
        {"label": "Input", "value": f"public-sector silver（{m['generated_at']} 作成・git {m['git_sha']}・{m['built_by']['actor']}）"},
    ]
    for name, rows in (("series.json", series_rows()), ("tables.json", table_rows())):
        (out / name).write_text(json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False), encoding="utf-8")
        print(f"{out / name}: {len(rows)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
