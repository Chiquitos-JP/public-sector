"""利用側の入口。silver の観測値とカタログを読む。

    from psdata import client
    client.read("fred.UNRATE")                                   # date / value
    client.read(["fred.UNRATE", "fred.CPIAUCSL"], start="2020-01-01")  # 縦長
    client.read("wb.NY.GDP.MKTP.KD.ZG", countries="central_america")   # 国をまたぐ系列
    client.read("wb.FP.CPI.TOTL.ZG", countries=["GTM", "MEX", "region:LCN"])
    client.catalog()                                              # 系列の定義・クレジット・ライセンス
    client.countries()                                            # 国・地域の一覧（地域・所得区分）
    client.groups()                                               # 名前付きの国の組み合わせ

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
