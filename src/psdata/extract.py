"""目的に合わせた切り出し（tidy な表）と、データカード付きの書き出し。

    df = client.extract(["wb.NY.GDP.MKTP.KD.ZG", "wb.FP.CPI.TOTL.ZG"], countries="central_america",
                        start="2015-01-01", measures=["value"])
    client.export(df, "out/central_america_macro.xlsx")   # data・card の 2 シート
    client.export(df, "out/central_america_macro.csv")    # CSV ＋ .card.md ＋ .card.json

## 表の形（tidy: 1 行 = 1 観測、1 列 = 1 つの意味）

    series_id / title_ja / country / country_name / date / period / measure / value / unit

``measure`` は値の種類で、行ごとに単位が決まる（``unit``）。横に並べたいときは ``to_wide``。

## 計算した値（measure）の定義（全利用先で同じ。変えるときは版を上げる）

- ``value``     元の値
- ``yoy_pct``   前年同期比（%）。同じ系列・同じ国の「ちょうど 1 年前の期間」の値と比べる。
                日次・週次は「1 年前の日以前で最も新しい値」（7 日以内に無ければ空）
- ``yoy_diff``  前年同期差（元の単位）。率の系列（失業率・金利など）はこちらを使う
- ``pop_pct``   前期比（%）。直前の観測と比べる（欠けた期間をまたぐときは空）
- ``pop_diff``  前期差（元の単位）

基準は各系列の観測日で、今日ではない。前の値が 0 以下・欠けているときは空（0 で埋めない）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

MEASURES_VERSION = 1
MEASURES = {
    "value": "元の値",
    "yoy_pct": "前年同期比（%）。ちょうど 1 年前の期間と比べる（日次・週次は 1 年前の日以前で最も新しい値、7 日以内）",
    "yoy_diff": "前年同期差（元の単位）",
    "pop_pct": "前期比（%）。直前の期間と比べる（欠けた期間をまたぐときは空）",
    "pop_diff": "前期差（元の単位）",
}
_PERIOD = {"M": pd.DateOffset(months=1), "Q": pd.DateOffset(months=3), "A": pd.DateOffset(years=1)}
_TOLERANCE = pd.Timedelta(days=7)


def _period_label(dates: pd.Series, freq: str) -> pd.Series:
    if freq == "A":
        return dates.dt.year.astype(str)
    if freq == "Q":
        return dates.dt.year.astype(str) + "Q" + dates.dt.quarter.astype(str)
    if freq == "M":
        return dates.dt.strftime("%Y-%m")
    return dates.dt.strftime("%Y-%m-%d")


def _year_ago(g: pd.DataFrame, freq: str) -> pd.Series:
    """各行の「1 年前」の値。"""
    s = g.set_index("date")["value"]
    target = g["date"] - pd.DateOffset(years=1)
    if freq in _PERIOD:
        return pd.Series(s.reindex(target).to_numpy(), index=g.index)
    # 日次・週次: 1 年前の日以前で最も新しい値（7 日以内）
    left = pd.DataFrame({"t": target.to_numpy(), "_i": g.index}).sort_values("t")
    right = s.reset_index().rename(columns={"date": "t", "value": "v"}).sort_values("t")
    m = pd.merge_asof(left, right, on="t", direction="backward", tolerance=_TOLERANCE)
    return pd.Series(m["v"].to_numpy(), index=m["_i"].to_numpy()).reindex(g.index)


def _prev(g: pd.DataFrame, freq: str) -> pd.Series:
    prev = g["value"].shift(1)
    if freq in _PERIOD:
        expected = g["date"] - _PERIOD[freq]
        prev = prev.where(g["date"].shift(1) == expected)
    return prev


def _pct(cur: pd.Series, base: pd.Series) -> pd.Series:
    return ((cur / base - 1) * 100).where(base > 0)


def add_measures(df: pd.DataFrame, freq_of: dict[str, str], measures: list[str]) -> pd.DataFrame:
    """縦長の観測値（series_id / country / date / value）に計算した値を足し、measure の列で縦に積む。"""
    unknown = set(measures) - set(MEASURES)
    if unknown:
        raise KeyError(f"measure は {sorted(MEASURES)} のいずれか: {sorted(unknown)}")
    out = []
    for (sid, _), g in df.sort_values(["series_id", "country", "date"]).groupby(["series_id", "country"], sort=False):
        f = freq_of[sid]
        base = {}
        if {"yoy_pct", "yoy_diff"} & set(measures):
            base["yoy"] = _year_ago(g, f)
        if {"pop_pct", "pop_diff"} & set(measures):
            base["pop"] = _prev(g, f)
        calc = {
            "value": g["value"],
            "yoy_pct": lambda: _pct(g["value"], base["yoy"]),
            "yoy_diff": lambda: g["value"] - base["yoy"],
            "pop_pct": lambda: _pct(g["value"], base["pop"]),
            "pop_diff": lambda: g["value"] - base["pop"],
        }
        for m in measures:
            v = calc[m] if m == "value" else calc[m]()
            out.append(g.assign(measure=m, value=v.to_numpy()))
    if not out:
        return df.assign(measure=pd.Series(dtype="string"))
    return pd.concat(out, ignore_index=True)


def tidy(
    obs: pd.DataFrame,
    cat: pd.DataFrame,
    countries: pd.DataFrame | None,
    measures: list[str],
    dropna: bool,
) -> pd.DataFrame:
    meta = cat.set_index("series_id")
    df = add_measures(obs, meta["freq"].to_dict(), measures)
    df["title_ja"] = df["series_id"].map(meta["title_ja"])
    unit = df["series_id"].map(meta["unit"]).astype("string")
    df["unit"] = unit.where(df["measure"].isin(["value", "yoy_diff", "pop_diff"]), "%")
    df["period"] = pd.concat(
        [_period_label(g["date"], meta.loc[sid, "freq"]) for sid, g in df.groupby("series_id", sort=False)]
    ).reindex(df.index)
    names = countries.set_index("country")["name"] if countries is not None else pd.Series(dtype="string")
    df["country_name"] = df["country"].map(names).astype("string").fillna("")
    if dropna:
        # 計算できなかった行（前の期間が無い）だけを落とす。元の値の欠けは元々行が無い
        df = df[df["value"].notna() | (df["measure"] == "value")]
    cols = ["series_id", "title_ja", "country", "country_name", "date", "period", "measure", "value", "unit"]
    return df[cols].sort_values(["series_id", "measure", "country", "date"]).reset_index(drop=True)


def to_wide(df: pd.DataFrame, columns: str = "series_id") -> pd.DataFrame:
    """横持ちにする（表やグラフ用）。``columns`` は series_id / country / measure のどれか。"""
    index = [c for c in ["country", "series_id", "measure", "date", "period"] if c != columns]
    keep = [c for c in index if df[c].nunique() > 1 or c in ("date", "period")]
    return df.pivot_table(index=keep, columns=columns, values="value", aggfunc="first").reset_index()


# --- データカード -----------------------------------------------------------------------------


def card(df: pd.DataFrame, cat: pd.DataFrame, prof: pd.DataFrame | None, query: dict) -> dict:
    """書き出した表の説明。出典・クレジット・ライセンス・定義・前提・鮮度・切り出しの条件。"""
    meta = cat.set_index("series_id")
    p = prof.set_index("series_id") if prof is not None and len(prof) else None
    series = []
    for sid in df["series_id"].drop_duplicates():
        r = meta.loc[sid]
        item = {
            "series_id": sid,
            "title_ja": r["title_ja"],
            "title_en": r["title_en"],
            "unit": r["unit"],
            "freq": r["freq"],
            "seasonal_adjustment": r["seasonal_adjustment"],
            "definition": r.get("definition", ""),
            "notes": [n for n in str(r.get("notes", "") or "").split("\n") if n],
            "credit": r["credit"],
            "license": r["license"],
            "source_url": r.get("source_url", ""),
        }
        if p is not None and sid in p.index:
            q = p.loc[sid]
            item["last_observation_date"] = str(pd.Timestamp(q["last_observation_date"]).date())
            item["last_checked_at"] = None if pd.isna(q["last_checked_at"]) else pd.Timestamp(q["last_checked_at"]).isoformat()
        series.append(item)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "query": query,
        "rows": len(df),
        "columns": {
            "series_id": "系列 id（<配信元>.<元のコード>）",
            "title_ja": "系列名",
            "country": "国・地域（ISO3。世界銀行の集計値を含む）",
            "country_name": "国・地域の名前（世界銀行の表記）",
            "date": "期間の開始日",
            "period": "期間の表記（年・四半期・月・日）",
            "measure": "値の種類（下の measures）",
            "value": "値",
            "unit": "単位（measure ごと）",
        },
        "measures_version": MEASURES_VERSION,
        "measures": {m: MEASURES[m] for m in df["measure"].drop_duplicates()},
        "series": series,
        "credits": sorted({s["credit"] for s in series}),
        "licenses": sorted({s["license"] for s in series}),
        "how_to_cite": "使うときは credits をそのまま出典として書く（CC BY・出典明記のライセンスは出典の表示が条件）",
    }


def card_markdown(c: dict) -> str:
    q = c["query"]
    lines = [
        "# データカード",
        "",
        f"- 作成: {c['generated_at']}（UTC）/ 行数: {c['rows']:,}",
        f"- 系列: {', '.join(q['series'])}",
        f"- 国: {q.get('countries') or '指定なし'} / 期間: {q.get('start') or '最初'} 〜 {q.get('end') or '最後'}"
        + (f" / 時点: {q['as_of']} に知り得た値" if q.get("as_of") else ""),
        f"- 値の種類: {', '.join(c['measures'])}",
        "",
        "## 出典（使うときはこのまま書く）",
        "",
        *[f"- {x}" for x in c["credits"]],
        "",
        f"ライセンス: {', '.join(c['licenses'])}。{c['how_to_cite']}",
        "",
        "## 値の種類",
        "",
        *[f"- `{k}`: {v}" for k, v in c["measures"].items()],
        "",
        "## 系列",
    ]
    for s in c["series"]:
        lines += [
            "",
            f"### {s['title_ja']}（`{s['series_id']}`）",
            "",
            s["definition"],
            "",
            f"- 単位: {s['unit']} / 頻度: {s['freq']} / 季節調整: {s['seasonal_adjustment']}",
            f"- 最終の期間: {s.get('last_observation_date', '—')} / 最後に確かめた: {s.get('last_checked_at', '—')}",
            f"- 出典: {s['credit']}（{s['license']}）/ 定義: {s['source_url']}",
        ]
        if s["notes"]:
            lines += ["- 前提:"] + [f"  - {n}" for n in s["notes"]]
    lines += ["", "## 列", "", *[f"- `{k}`: {v}" for k, v in c["columns"].items()], ""]
    return "\n".join(lines)


def write(df: pd.DataFrame, path: str | Path, c: dict) -> list[Path]:
    """表とデータカードを書く。拡張子で形式を選ぶ（.csv / .xlsx / .parquet）。書いたファイルを返す。"""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    md = card_markdown(c)
    written = [path]
    if path.suffix == ".csv":
        out.to_csv(path, index=False, encoding="utf-8-sig")  # Excel でそのまま開けるよう BOM 付き
    elif path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    elif path.suffix == ".xlsx":
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            out.to_excel(xw, sheet_name="data", index=False)
            pd.DataFrame({"データカード": md.split("\n")}).to_excel(xw, sheet_name="card", index=False)
    else:
        raise ValueError(f"書き出しの形式は .csv / .xlsx / .parquet: {path.name}")
    stem = path.with_suffix("")
    for suffix, text in ((".card.md", md), (".card.json", json.dumps(c, ensure_ascii=False, indent=2))):
        p = Path(str(stem) + suffix)
        p.write_text(text, encoding="utf-8")
        written.append(p)
    return written
