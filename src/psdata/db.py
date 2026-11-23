"""DuckDB で silver を SQL として読む。保存の形（Parquet）はそのままで、ビューを被せるだけ。

    from psdata import client
    client.sql("select * from obs where series_id = 'fred.UNRATE' order by date desc limit 3")
    client.sql("select * from as_of('2027-01-15') where series_id = 'fred.M2SL'")
    con = client.db()            # duckdb の接続そのもの（R2 を直接読むなら client.db(source="r2")）

## ビュー

- ``observations`` / ``panel`` / ``series`` / ``profile`` / ``vintages`` / ``countries``  silver の各表そのまま
- ``obs``      単一の系列とパネルを 1 つにした観測値（series_id / country / date / value / fetched_at）
- ``groups``   catalog/groups.yml の国の組み合わせ（group / title_ja / country）
- ``as_of(t)`` その時点で知り得た値（series_id / country / date / value）。``client.read(as_of=...)`` と同じ定義

## 置き場所

- ``source="local"``（既定）  手元の ``data/silver/``（``./data_sync.sh pull`` で取ってきたもの）
- ``source="r2"``  保存先の ``silver/`` を直接読む（取ってこなくてよい）。読み取り専用の鍵を使う:
  環境変数 ``PSDATA_R2_KEY_ID`` / ``PSDATA_R2_SECRET`` / ``PSDATA_R2_ENDPOINT``、無ければ rclone の remote ``r2ps`` の設定。
  鍵はメモリの中だけで使い、ファイルには書かない
"""
from __future__ import annotations

import json
import os
import subprocess
from urllib.parse import urlparse

import duckdb
import pandas as pd

from psdata import catalog
from psdata.paths import silver_dir

BUCKET = "public-sector-data"
RCLONE_REMOTE = "r2ps"
TABLES = ["observations", "panel", "series", "profile", "vintages", "countries"]


def _r2_credentials() -> tuple[str, str, str]:
    key, secret, endpoint = (os.environ.get(k, "").strip() for k in ("PSDATA_R2_KEY_ID", "PSDATA_R2_SECRET", "PSDATA_R2_ENDPOINT"))
    if key and secret and endpoint:
        return key, secret, endpoint
    try:
        out = subprocess.run(["rclone", "config", "dump"], capture_output=True, text=True, check=True).stdout
        conf = json.loads(out).get(RCLONE_REMOTE) or {}
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as e:
        raise RuntimeError(f"R2 の鍵が見つからない（環境変数か rclone の remote {RCLONE_REMOTE}）: {e}") from e
    key, secret, endpoint = conf.get("access_key_id", ""), conf.get("secret_access_key", ""), conf.get("endpoint", "")
    if not (key and secret and endpoint):
        raise RuntimeError(f"rclone の remote {RCLONE_REMOTE} に鍵か endpoint が無い")
    return key, secret, endpoint


def _base(source: str, con: duckdb.DuckDBPyConnection) -> str:
    if source == "local":
        return str(silver_dir()).rstrip("/")
    if source == "r2":
        key, secret, endpoint = _r2_credentials()
        host = urlparse(endpoint).netloc or endpoint
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(
            "CREATE OR REPLACE SECRET psdata_r2 (TYPE s3, KEY_ID ?, SECRET ?, ENDPOINT ?, URL_STYLE 'path', REGION 'auto')",
            [key, secret, host],
        )
        return f"s3://{BUCKET}/silver"
    raise KeyError(f"source は local / r2: {source!r}")


def connect(source: str = "local") -> duckdb.DuckDBPyConnection:
    """silver の表をビューにした DuckDB の接続（メモリの中だけ。何も書かない）。"""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")  # 日付だけの as_of を UTC の 0 時として読む（client.read と同じ）
    base = _base(source, con)
    for t in TABLES:
        path = f"{base}/{t}.parquet"
        if source == "local" and not os.path.isfile(path):
            continue
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{path}')")
    have = {r[0] for r in con.execute("select view_name from duckdb_views() where not internal").fetchall()}
    parts = [f"SELECT series_id, country, date, value, fetched_at FROM {t}" for t in ("observations", "panel") if t in have]
    if parts:
        con.execute("CREATE VIEW obs AS " + " UNION ALL ".join(parts))
    if "vintages" in have:
        con.execute(
            """
            CREATE MACRO as_of(t) AS TABLE
            SELECT series_id, country, date, value FROM vintages
            WHERE valid_from <= CAST(t AS TIMESTAMPTZ) AND (valid_to IS NULL OR valid_to > CAST(t AS TIMESTAMPTZ))
            """
        )
    rows = [
        {"group": name, "title_ja": g.get("title_ja", ""), "country": c}
        for name, g in catalog.load_groups().items()
        for c in g.get("countries") or []
    ]
    con.register("_groups", pd.DataFrame(rows, columns=["group", "title_ja", "country"]))
    con.execute('CREATE VIEW groups AS SELECT "group", title_ja, country FROM _groups')
    return con


def sql(query: str, params: list | None = None, source: str = "local") -> pd.DataFrame:
    con = connect(source)
    try:
        return con.execute(query, params or []).df()
    finally:
        con.close()
