"""配信元ごとの取得処理。各モジュールは ``fetch(series, start) -> (DataFrame[date, value], params)`` を持つ。"""
from psdata.sources import boj, fred

FETCHERS = {"fred": fred.fetch, "boj": boj.fetch}
