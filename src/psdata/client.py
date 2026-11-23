"""利用側の入口。silver の観測値とカタログを読む。

    from psdata import client
    client.read("fred.UNRATE")                                   # date / value
    client.read(["fred.UNRATE", "fred.CPIAUCSL"], start="2020-01-01")  # 縦長
    client.read("wb.NY.GDP.MKTP.KD.ZG", countries="g7")   # 国をまたぐ系列
    client.read("wb.FP.CPI.TOTL.ZG", countries=["GTM", "MEX", "region:LCN"])
    client.catalog()                                              # 系列の定義・クレジット・ライセンス
    client.countries()                                            # 国・地域の一覧（地域・所得区分）
    client.groups()                                               # 名前付きの国の組み合わせ
    print(client.describe("wb.FP.CPI.TOTL.ZG"))                   # 何を測っているか・前提・出典・特徴
    client.read("fred.M2SL", as_of="2026-12-01")                  # その時点で知り得た値（改定前の値）
    client.search("school enrollment")                            # 取得元の指標を探す（世界銀行。FRED は API キーがあれば）
    client.pull("wb.SE.PRM.ENRR")                                 # カタログに無い系列をその場で取る（手元にだけ置く）
    print(client.draft("wb.SE.PRM.ENRR"))                         # 常備に上げるときのカタログの下書き
    df = client.extract(["wb.NY.GDP.MKTP.KD.ZG"], countries="g7", measures=["value"])  # 目的に合わせた tidy な表
    client.export(df, "out/macro.xlsx")                           # データカード（出典・定義・前提）付きで書き出す
    client.sql("select * from obs where series_id = 'fred.UNRATE'")  # SQL で読む（DuckDB。ビューは psdata.db）
    client.concepts()                                             # 同じものを測る系列の束（取得元をまたぐ）
    client.compare("gov_debt_gdp", countries="g7", start="2015-01-01")  # 取得元を並べて比べる
    client.profile()                                              # 系列ごとの特徴（期間・欠け・値域・変化幅・遅れ・改定・鮮度）
    client.manifest()                                             # silver を作った時刻・コードの版・件数

``countries`` には ISO3 のリスト、catalog/groups.yml のグループ名、
世界銀行の区分（``region:LCN`` / ``income:LMC`` など）を混ぜて渡せる。
データの場所は ``PSDATA_DIR``（既定はリポジトリの ``data/``）。
"""
from __future__ import annotations

import pandas as pd

from psdata import catalog as _catalog
from psdata import vintages as _vintages
from psdata.ondemand import draft, pull, pulled, search  # noqa: F401  必要なときに引き出す入口
from psdata.paths import silver_dir


def catalog() -> pd.DataFrame:
    return pd.read_parquet(silver_dir() / "series.parquet")


def profile(series_id: str | list[str] | None = None) -> pd.DataFrame:
    """系列ごとの特徴（実物から自動で作ったもの）。列の意味は ``psdata.profile`` を参照。"""
    df = pd.read_parquet(silver_dir() / "profile.parquet")
    if series_id is None:
        return df
    ids = [series_id] if isinstance(series_id, str) else list(series_id)
    return df[df["series_id"].isin(ids)].reset_index(drop=True)


def manifest() -> dict:
    import json

    return json.loads((silver_dir() / "manifest.json").read_text(encoding="utf-8"))


def _profile_lines(series_id: str) -> list[str]:
    path = silver_dir() / "profile.parquet"
    if not path.is_file():
        return []
    p = profile(series_id)
    if p.empty:
        return []
    r = p.iloc[0]

    def d(x) -> str:
        return "—" if pd.isna(x) else pd.Timestamp(x).strftime("%Y-%m-%d")

    def n(x, fmt="{:,.4g}") -> str:
        return "—" if pd.isna(x) else fmt.format(x)

    lines = [
        "",
        "特徴（実データから自動）:",
        f"  期間: {d(r['first_date'])} 〜 {d(r['last_observation_date'])}（{int(r['n_obs']):,} 件"
        + (f"・{int(r['n_countries'])} の国・地域、最終の年は {int(r['n_countries_at_last'])}" if r["is_panel"] else "")
        + "）",
        f"  欠けた期間: {int(r['missing_periods'])}" + (f"（{r['missing_dates']}）" if r["missing_dates"] else "")
        + f" / 最大の空き {n(r['max_gap_days'], '{:.0f}')} 日",
        f"  値: 最小 {n(r['value_min'])} / 中央 {n(r['value_median'])} / 最大 {n(r['value_max'])}",
        f"  1 期の変化幅: 中央 {n(r['abs_change_p50'])} / 99% {n(r['abs_change_p99'])} / 最大 {n(r['abs_change_max'])}",
        f"  公表の遅れ: 取得時に最新は {n(r['release_lag_days_median'], '{:.0f}')} 日前の期間（中央）",
        f"  改定: {int(r['n_revisions'])} 件" + (f"（最大 {r['max_rel_revision']:.2%}・最後 {d(r['last_revision_at'])}）" if r["n_revisions"] else ""),
        f"  鮮度: 最後に確かめた {d(r['last_checked_at'])} / 値が最後に変わった {d(r['last_changed_at'])}"
        + (f" / ⚠️ 古い（{int(r['release_lag_days_now'])} 日 > {int(r['stale_limit_days'])} 日）" if r["is_stale"] else ""),
    ]
    return lines


def describe(series_id: str) -> str:
    """系列の説明（何を測っているか・読むときの前提・出典）を読みやすい文にする。"""
    cat = catalog().set_index("series_id")
    if series_id not in cat.index:
        raise KeyError(f"カタログに無い系列: {series_id}")
    r = cat.loc[series_id]
    where = "全ての国・地域（countries で選ぶ）" if r["country"] == _catalog.PANEL else r["country"]
    lines = [
        f"{series_id}  {r['title_ja']}",
        f"  {r['title_en']}",
        "",
        r["definition"],
        "",
        f"単位: {r['unit']} / 頻度: {r['freq']} / 季節調整: {r['seasonal_adjustment']} / 国: {where}",
    ]
    if r["notes"]:
        lines += ["", "前提:"] + [f"  - {n}" for n in r["notes"].split("\n")]
    lines += _profile_lines(series_id)
    lines += ["", f"出典: {r['credit']}", f"ライセンス: {r['license']}", f"定義: {r['source_url']}"]
    return "\n".join(lines)


def countries() -> pd.DataFrame:
    return pd.read_parquet(silver_dir() / "countries.parquet")


def groups() -> dict[str, list[str]]:
    return {k: list(v["countries"]) for k, v in _catalog.load_groups().items()}


def resolve_countries(spec: str | list[str]) -> list[str]:
    """グループ名・区分・ISO3 を国コードのリストにする（順序を保ち、重複を除く）。"""
    items = [spec] if isinstance(spec, str) else list(spec)
    named = groups()
    out: list[str] = []
    table: pd.DataFrame | None = None
    for item in items:
        if item in named:
            out += named[item]
        elif ":" in item:
            kind, code = item.split(":", 1)
            if kind not in {"region", "income", "lending"}:
                raise KeyError(f"区分は region / income / lending のいずれか: {item!r}")
            table = countries() if table is None else table
            hit = table.loc[(table[kind] == code) & ~table["is_aggregate"], "country"].tolist()
            if not hit:
                raise KeyError(f"該当する国が無い区分: {item!r}")
            out += hit
        else:
            out.append(item)
    return list(dict.fromkeys(out))


def _filters(ids: list[str], start: str | None, end: str | None, extra: list | None = None) -> list:
    f = [("series_id", "in", ids), *(extra or [])]
    if start:
        f.append(("date", ">=", pd.Timestamp(start)))
    if end:
        f.append(("date", "<=", pd.Timestamp(end)))
    return f


def read(
    series_id: str | list[str],
    *,
    countries: str | list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """1 つの単一系列なら date / value、それ以外は series_id / country / date / value の縦長で返す。

    国をまたぐ系列は ``countries`` で国を絞る（省略すると全ての国・地域）。
    ``as_of`` を渡すと、その時点で public-sector が持っていた値を返す（後の改定・後から届いた期間を含めない）。
    時刻は取得した時刻（UTC。日付だけなら 0 時）で、作成元の公表時刻ではない。取得を始める前の時点は KeyError。
    """
    ids = [series_id] if isinstance(series_id, str) else list(series_id)
    cat = catalog()
    unknown = set(ids) - set(cat["series_id"])
    if unknown:
        raise KeyError(f"カタログに無い系列: {sorted(unknown)}")
    panel_ids = set(cat.loc[cat["country"] == _catalog.PANEL, "series_id"])
    single = [i for i in ids if i not in panel_ids]
    panel = [i for i in ids if i in panel_ids]

    if as_of is not None:
        return _read_as_of(series_id, ids, panel, countries, start, end, as_of)

    frames = []
    if single:
        frames.append(pd.read_parquet(silver_dir() / "observations.parquet", filters=_filters(single, start, end)))
    if panel:
        extra = [("country", "in", resolve_countries(countries))] if countries is not None else None
        frames.append(pd.read_parquet(silver_dir() / "panel.parquet", filters=_filters(panel, start, end, extra)))
    df = pd.concat(frames, ignore_index=True).sort_values(["series_id", "country", "date"]).reset_index(drop=True)
    if isinstance(series_id, str) and not panel:
        return df[["date", "value"]]
    return df[["series_id", "country", "date", "value"]]


def _read_as_of(series_id, ids, panel, countries, start, end, as_of) -> pd.DataFrame:
    v = pd.read_parquet(silver_dir() / "vintages.parquet", filters=_filters(ids, start, end))
    if countries is not None and panel:
        # 国を絞るのはパネルの系列だけ（単一の系列の国は固定）
        keep = ~v["series_id"].isin(panel) | v["country"].isin(resolve_countries(countries))
        v = v[keep]
    since = pd.read_parquet(silver_dir() / "vintages.parquet", columns=["valid_from"])["valid_from"].min()
    t = pd.Timestamp(as_of)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    if pd.isna(since) or t < since:
        raise KeyError(f"{t} は取得を始める前（{since}）なので再現できない")
    df = _vintages.as_of(v, t).sort_values(["series_id", "country", "date"]).reset_index(drop=True)
    if isinstance(series_id, str) and not panel:
        return df[["date", "value"]]
    return df[["series_id", "country", "date", "value"]]


def extract(
    series_id: str | list[str],
    *,
    countries: str | list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    as_of: str | pd.Timestamp | None = None,
    measures: list[str] | tuple[str, ...] = ("value",),
    dropna: bool = True,
    base_units: bool = False,
) -> pd.DataFrame:
    """目的に合わせて切り出した tidy な表（1 行 = 1 観測）。列と measure の定義は ``psdata.extract``。

    前年比などは期間を絞る前の全履歴から計算するので、``start`` の最初の年も前年比が出る。
    ``base_units=True`` なら倍率（scale）を値に掛けて 1 単位（1 ドルなど）にそろえる。
    率の系列に変化率（yoy_pct など）を求めると警告し、データカードにも残す。
    """
    import warnings as _warnings

    from psdata import extract as _extract

    ids = [series_id] if isinstance(series_id, str) else list(series_id)
    # 前年比・前期比のために 1 年分さかのぼって読み、計算したあとで期間を絞る
    need_base = any(m != "value" for m in measures)
    read_start = (pd.Timestamp(start) - pd.DateOffset(years=1, days=7)).date().isoformat() if start and need_base else start
    obs = read(ids, countries=countries, start=read_start, end=end, as_of=as_of)
    if "series_id" not in obs.columns:  # 単一の系列は date / value だけで返るので揃える
        row = catalog().set_index("series_id").loc[ids[0]]
        obs = obs.assign(series_id=ids[0], country=row["country"])
    path = silver_dir() / "countries.parquet"
    cat = catalog()
    df = _extract.tidy(obs, cat, pd.read_parquet(path) if path.is_file() else None, list(measures), dropna, base_units)
    notes = _extract.unit_warnings(cat, ids, list(measures))
    for n in notes:
        _warnings.warn(n, stacklevel=2)
    if start:
        df = df[df["date"] >= pd.Timestamp(start)].reset_index(drop=True)
    df.attrs["query"] = {
        "series": ids, "countries": countries, "start": start, "end": end,
        "as_of": None if as_of is None else str(as_of), "measures": list(measures),
        "base_units": base_units, "warnings": notes,
    }
    return df


def to_wide(df: pd.DataFrame, columns: str = "series_id") -> pd.DataFrame:
    """extract の表を横持ちにする（表やグラフ用）。"""
    from psdata import extract as _extract

    return _extract.to_wide(df, columns)


def export(df: pd.DataFrame, path: str, query: dict | None = None) -> list[str]:
    """extract の表を、データカード（出典・クレジット・ライセンス・定義・前提・鮮度・条件）付きで書き出す。

    拡張子で形式を選ぶ: .xlsx（data と card のシート）/ .csv（Excel で開ける）/ .parquet。
    どの形式でも ``<名前>.card.md`` と ``<名前>.card.json`` を隣に置く。書いたファイルのパスを返す。
    """
    from psdata import extract as _extract

    q = query or df.attrs.get("query") or {"series": sorted(df["series_id"].unique())}
    path_prof = silver_dir() / "profile.parquet"
    prof = pd.read_parquet(path_prof) if path_prof.is_file() else None
    c = _extract.card(df, catalog(), prof, q)
    return [str(p) for p in _extract.write(df, path, c)]


def db(source: str = "local"):
    """silver をビューにした DuckDB の接続。``source="r2"`` で保存先を直接読む。ビューは ``psdata.db``。"""
    from psdata import db as _db

    return _db.connect(source)


def sql(query: str, params: list | None = None, source: str = "local") -> pd.DataFrame:
    """SQL で読む（DuckDB）。ビュー: obs / observations / panel / series / profile / vintages / countries / groups、表関数 as_of(t)。"""
    from psdata import db as _db

    return _db.sql(query, params, source)


def contract(table: str | None = None) -> dict:
    """silver の表の契約（列・型・意味・1 行の単位）。``catalog/contracts/silver.yml``。"""
    from psdata import contracts as _contracts

    c = _contracts.load()
    return c if table is None else c["tables"][table]


def concepts() -> pd.DataFrame:
    """概念（同じものを測る系列の束）ごとに、どの取得元のどの系列があり、並べたときに何が違うか。"""
    from psdata import catalog as _cat

    cat = catalog()
    rows = []
    for key, c in _cat.load_concepts().items():
        members = cat[cat["concept"] == key]
        rows.append({
            "concept": key,
            "title_ja": c.get("title_ja", ""),
            "series": ", ".join(members["series_id"]),
            "coverages": ", ".join(f"{r.series_id}={r.coverage}" for r in members.itertuples()),
            "compare_notes": "\n".join(c.get("compare_notes") or []),
        })
    return pd.DataFrame(rows)


def compare(
    concept: str,
    *,
    countries: str | list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    as_of: str | pd.Timestamp | None = None,
    to_freq: str | None = None,
) -> pd.DataFrame:
    """同じ概念の系列を、取得元をまたいで同じ形に並べる（tidy）。

    国コード・単位の倍率（base_units）・期間の表記をそろえ、``source`` / ``coverage`` の列で違いを示す。
    頻度が混ざるときは ``to_freq``（既定は一番粗い頻度。年なら年平均）にそろえる。並べたときの注意は
    ``client.concepts()`` の compare_notes とデータカードに入る。横に並べるなら ``to_wide(df, columns="series_id")``。
    """
    from psdata import catalog as _cat
    from psdata import extract as _extract

    cat = catalog()
    members = cat[cat["concept"] == concept]
    if members.empty:
        raise KeyError(f"概念 {concept!r} の系列が無い（client.concepts() を参照）")
    ids = members["series_id"].tolist()
    df = extract(ids, countries=countries, start=start, end=end, as_of=as_of, base_units=True)
    order = ["D", "W", "M", "Q", "A"]
    freq_of = members.set_index("series_id")["freq"]
    target = to_freq or max(freq_of, key=order.index)
    rule = {"A": "YS", "Q": "QS", "M": "MS", "W": "W-MON", "D": "D"}[target]
    coarse, gaps = [], []
    expected = {("M", "A"): 12, ("Q", "A"): 4, ("M", "Q"): 3}
    for sid, g in df.groupby("series_id", sort=False):
        if freq_of[sid] == target:
            coarse.append(g)
            continue
        # 細かい頻度の系列は平均して粗い頻度にそろえる（年なら年平均）。
        # 終わっていない期間（最後の観測が期間の終わりより前）は平均しない。欠けがある期間は残して注意に書く
        r = g.set_index("date").groupby(["country"])["value"].resample(rule)
        agg = pd.concat([r.mean().rename("value"), r.count().rename("n"), r.apply(lambda x: x.index.max()).rename("last")], axis=1)
        agg = agg.dropna(subset=["value"]).reset_index()
        period_end = agg["date"] + pd.tseries.frequencies.to_offset(rule) - pd.Timedelta(days=1)
        finished = agg["last"] >= period_end - pd.Timedelta(days=7 if freq_of[sid] in "DW" else 0)
        if freq_of[sid] in "MQ":
            finished = agg["last"] >= period_end.dt.to_period("M").dt.to_timestamp()
        agg = agg[finished]
        need = expected.get((freq_of[sid], target))
        if need:
            short = agg[agg["n"] < need]
            gaps += [f"{sid} {r.country} {_extract._period_label(pd.Series([r.date]), target).iloc[0]} は {int(r.n)}/{need} 期間の平均" for r in short.itertuples()]
        agg = agg.drop(columns=["n", "last"])
        first = g.iloc[0]
        agg = agg.assign(**{c: first[c] for c in ["series_id", "title_ja", "measure", "unit", "unit_kind", "currency", "scale"]})
        agg["country_name"] = agg["country"].map(g.drop_duplicates("country").set_index("country")["country_name"])
        agg["country_name_ja"] = agg["country"].map(g.drop_duplicates("country").set_index("country")["country_name_ja"])
        agg["period"] = _extract._period_label(agg["date"], target)
        coarse.append(agg[g.columns])
    df = pd.concat(coarse, ignore_index=True)
    meta = members.set_index("series_id")
    df.insert(0, "concept", concept)
    df.insert(2, "source", df["series_id"].map(meta["distributor"]))
    df.insert(3, "coverage", df["series_id"].map(meta["coverage"]))
    c = _cat.load_concepts()[concept]
    notes = list(c.get("compare_notes") or [])
    if any(freq_of[s] != target for s in ids):
        notes.append(f"頻度をそろえた: {', '.join(s for s in ids if freq_of[s] != target)} を {target} の平均にした（終わっていない期間は除く）")
    notes += gaps
    df = df.sort_values(["country", "date", "series_id"]).reset_index(drop=True)
    df.attrs["query"] = {
        "series": ids, "concept": concept, "countries": countries, "start": start, "end": end,
        "as_of": None if as_of is None else str(as_of), "measures": ["value"], "base_units": True,
        "to_freq": target, "warnings": notes,
    }
    return df
