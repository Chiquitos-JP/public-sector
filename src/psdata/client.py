"""利用側の入口。silver の観測値とカタログを読む。

    from psdata import client
    client.read("fred.UNRATE")                                   # date / value
    client.read(["fred.UNRATE", "fred.CPIAUCSL"], start="2020-01-01")  # 縦長
    client.read("wb.NY.GDP.MKTP.KD.ZG", countries="central_america")   # 国をまたぐ系列
    client.read("wb.FP.CPI.TOTL.ZG", countries=["GTM", "MEX", "region:LCN"])
    client.catalog()                                              # 系列の定義・クレジット・ライセンス
    client.countries()                                            # 国・地域の一覧（地域・所得区分）
    client.groups()                                               # 名前付きの国の組み合わせ
    print(client.describe("wb.FP.CPI.TOTL.ZG"))                   # 何を測っているか・前提・出典・特徴
    client.profile()                                              # 系列ごとの特徴（期間・欠け・値域・変化幅・遅れ・改定・鮮度）
    client.manifest()                                             # silver を作った時刻・コードの版・件数

``countries`` には ISO3 のリスト、catalog/groups.yml のグループ名、
世界銀行の区分（``region:LCN`` / ``income:LMC`` など）を混ぜて渡せる。
データの場所は ``PSDATA_DIR``（既定はリポジトリの ``data/``）。
"""
from __future__ import annotations

import pandas as pd

from psdata import catalog as _catalog
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
) -> pd.DataFrame:
    """1 つの単一系列なら date / value、それ以外は series_id / country / date / value の縦長で返す。

    国をまたぐ系列は ``countries`` で国を絞る（省略すると全ての国・地域）。
    """
    ids = [series_id] if isinstance(series_id, str) else list(series_id)
    cat = catalog()
    unknown = set(ids) - set(cat["series_id"])
    if unknown:
        raise KeyError(f"カタログに無い系列: {sorted(unknown)}")
    panel_ids = set(cat.loc[cat["country"] == _catalog.PANEL, "series_id"])
    single = [i for i in ids if i not in panel_ids]
    panel = [i for i in ids if i in panel_ids]

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
