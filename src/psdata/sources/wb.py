"""世界銀行 Indicators API（認証不要）からの取得。系列 id は ``wb.<指標コード>``。

指標ごとに全ての国・地域をまとめて取る（パネル）。日付は年の初日。
値の無い国・年は API が null で返すので落とす。
"""
from __future__ import annotations

import pandas as pd

from psdata.catalog import Series
from psdata.sources._http import get, save_raw

API_URL = "https://api.worldbank.org/v2"
#: World Development Indicators
WDI_SOURCE = "2"
#: 1 回の応答の行数。大きくすると応答が遅くなり時間切れになりやすい
PER_PAGE = 5000
#: 応答の遅い日があるので、待ち時間を長めにして取り直す
TIMEOUT = 120
RETRIES = 3


def _pages(url: str, params: dict, raw_code: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        resp = get(url, {**params, "format": "json", "per_page": PER_PAGE, "page": page}, timeout=TIMEOUT, retries=RETRIES)
        save_raw("wb", raw_code, resp.text, f"p{page}.json")  # 同じ秒に複数ページを書いても上書きしない
        body = resp.json()
        if not isinstance(body, list) or len(body) < 2:
            msg = body[0].get("message") if isinstance(body, list) and body else body
            raise RuntimeError(f"世界銀行 API がエラーを返した: {msg}")
        rows.extend(body[1] or [])
        if page >= int(body[0].get("pages") or 1):
            return rows
        page += 1


def parse(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "country": [r.get("countryiso3code") or "" for r in rows],
            "date": pd.to_datetime([f"{r['date']}-01-01" for r in rows], errors="coerce"),
            "value": pd.to_numeric(pd.Series([r.get("value") for r in rows], dtype="object"), errors="coerce"),
        }
    )
    # 国コードの無い行（一部の地域区分）と値の無い行は持たない
    df = df[df["country"].str.fullmatch(r"[A-Z]{3}")]
    return df.dropna(subset=["date", "value"]).sort_values(["country", "date"]).reset_index(drop=True)


def fetch(series: Series, start: str) -> tuple[pd.DataFrame, dict]:
    year_from = pd.Timestamp(start).year
    year_to = pd.Timestamp.now().year
    params = {"source": WDI_SOURCE, "date": f"{year_from}:{year_to}"}
    rows = _pages(f"{API_URL}/country/all/indicator/{series.code}", params, series.code)
    return parse(rows), {"indicator": series.code, **params}


def parse_countries(rows: list[dict]) -> pd.DataFrame:
    def v(r: dict, k: str) -> str:
        return ((r.get(k) or {}).get("value") or "").strip()

    df = pd.DataFrame(
        {
            "country": [r["id"] for r in rows],
            "name": [r["name"].strip() for r in rows],
            "region": [(r.get("region") or {}).get("id", "") for r in rows],
            "region_name": [v(r, "region") for r in rows],
            "income": [(r.get("incomeLevel") or {}).get("id", "") for r in rows],
            "income_name": [v(r, "incomeLevel") for r in rows],
            "lending": [(r.get("lendingType") or {}).get("id", "") for r in rows],
        }
    )
    df["is_aggregate"] = df["region_name"] == "Aggregates"
    df.loc[df["is_aggregate"], ["region", "region_name", "income", "income_name", "lending"]] = ""
    return df.sort_values("country").reset_index(drop=True)


def fetch_countries() -> pd.DataFrame:
    """国・地域の一覧（ISO3・名称・地域・所得水準・集計値かどうか）。"""
    return parse_countries(_pages(f"{API_URL}/country", {}, "_countries"))


def fetch_indicator_list() -> pd.DataFrame:
    """WDI の全指標（約 1,500）の一覧。id / name / unit / source_note / source_org / topics。"""
    rows = _pages(f"{API_URL}/indicator", {"source": WDI_SOURCE}, "_indicators")
    return pd.DataFrame(
        {
            "code": [r["id"] for r in rows],
            "name": [(r.get("name") or "").strip() for r in rows],
            "unit": [(r.get("unit") or "").strip() for r in rows],
            "source_note": [(r.get("sourceNote") or "").strip() for r in rows],
            "source_org": [(r.get("sourceOrganization") or "").strip() for r in rows],
            "topics": [", ".join(t.get("value", "").strip() for t in (r.get("topics") or []) if t.get("value")) for r in rows],
        }
    )
