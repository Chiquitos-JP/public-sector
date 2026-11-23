"""日銀 時系列統計データ検索サイト API（認証不要）からの取得。系列 id は ``boj.<DB>.<コード>``。"""
from __future__ import annotations

import pandas as pd

from psdata.catalog import Series
from psdata.sources._http import get, save_raw

API_URL = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"


def parse(body: dict) -> pd.DataFrame:
    if str(body.get("STATUS")) != "200":
        raise RuntimeError(f"日銀 API がエラーを返した: {body.get('STATUS')} {body.get('MESSAGE')}")
    values = body["RESULTSET"][0]["VALUES"]
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([str(d) for d in values["SURVEY_DATES"]], format="%Y%m%d"),
            "value": pd.to_numeric(pd.Series(values["VALUES"]), errors="coerce"),
        }
    )
    # 祝日・休場は値なしで返るので落とす
    return df.dropna().reset_index(drop=True)


def fetch(series: Series, start: str) -> tuple[pd.DataFrame, dict]:
    db, code = series.code.split(".", 1)
    params = {"format": "json", "lang": "jp", "db": db, "code": code, "startDate": pd.Timestamp(start).strftime("%Y%m")}
    resp = get(API_URL, params)
    save_raw("boj", series.code, resp.text, "json")
    df = parse(resp.json())
    return df[df["date"] >= pd.Timestamp(start)].reset_index(drop=True), {"db": db, "code": code}
