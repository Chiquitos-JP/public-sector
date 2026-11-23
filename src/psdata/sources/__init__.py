"""配信元ごとの取得処理。各モジュールは ``fetch(series, start) -> (DataFrame, params)`` を持つ。

単一系列は date / value、パネル（``country: ALL``）は country / date / value を返す。
"""
from psdata.sources import boj, fred, imf, wb

FETCHERS = {"fred": fred.fetch, "boj": boj.fetch, "wb": wb.fetch, "imf": imf.fetch}

#: パネルを持つ配信元の参照表（国の一覧）。silver/countries.parquet の元
REFERENCES = {"wb": wb.fetch_countries, "imf": imf.fetch_countries}  # 先に書いた方の名前を優先
