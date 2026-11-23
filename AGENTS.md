# public-sector

公開統計を取得・整形し、非公開の保存先へ置くパイプライン（パッケージ名 `psdata`）。

- このリポジトリは公開されている。**秘密情報・個人のパス・非公開データ・利用先の固有名をコミットしない**
- 補足の運用メモ（非公開）が `~/dev/public-sector-history/psdata-notes.md` にあれば先に読む

## 収録基準（すべて満たすものだけ）

1. 公的機関が作成した統計（判断は作成元。配信元ではない）。市場価格は対象外
2. `catalog/sources.yml` の `allowed_licenses` にあるライセンス
3. `used_by` に利用先 ID（`u01` など）がある。固有名は書かない

## 構成

```
catalog/sources.yml        作成元・ライセンス・クレジット・配信元
catalog/series/*.yml       系列の定義
catalog/groups.yml         よく使う国の組み合わせ（中米・CARICOM・G7 など）
src/psdata/                catalog / store / sources / pipeline / client
scripts/fetch.py           常備系列の取得（--full で全期間取り直し）
scripts/validate.py        カタログとストアの検証（再取得しない）
data/                      git に入れない
```

## データの層

- `raw/<配信元>/<コード>/<時刻>.<拡張子>.gz` — 応答そのまま（gzip）。変更しない。保存先では 90 日を過ぎたら CI が消す
- `bronze/<配信元>/<id>.parquet` — `date / value / fetched_at / first_seen_at`（その行を初めて取得した時刻）。改定ログ `*.revisions.parquet`、取得記録 `*.provenance.json`（取得ごとに 1 件・全件残す。出所と引数・件数・検証の結果・実行者（CI の run id か手元か）・コードの版・元になった raw のパスと sha256）
- `silver/observations.parquet` — 単一の国の系列。`series_id / country / date / value / fetched_at`（1 行は `series_id / date`）
- `silver/panel.parquet` — 国をまたぐ系列（`country: ALL`）。列は同じで、1 行は `series_id / country / date`
- `silver/series.parquet` — カタログ（クレジット・ライセンス）
- `silver/countries.parquet` — 国・地域の一覧（世界銀行の地域・所得区分・集計値かどうか）
- `silver/profile.parquet` — 系列ごとの特徴。実物から毎回自動で作る（期間・欠けた期間・最大の空き・値域・1 期の変化幅・公表の遅れ・改定の回数と最大幅・鮮度）
- `silver/vintages.parquet` — 値の有効期間（`series_id / country / date / value / valid_from / valid_to`）。bronze と改定ログから毎回組み立てる。`client.read(..., as_of=...)` が読む
- `docs/catalog/series.json` / `tables.json` — 目録（持っている系列と表の一覧。CI が毎回 `scripts/catalog_summary.py` で作る）
- `silver/manifest.json` — silver を作った時刻・コードの版（git）・実行者（CI の run id か手元か）・ファイルごとの件数・`schema_version`

カタログ（`definition` / `notes` / `source_url` など）は手で書く**宣言**、プロファイルは実データの**観察**。
鮮度は `profile.parquet` の `last_observation_date`（データの最後の日付）で見る。`last_checked_at` は最後に取り直して確かめた時刻、`last_changed_at` は最後に値が変わった時刻

系列 id は `<配信元>.<元のコード>`。`country` は ISO3、`date` は期間の開始日。
パネルの bronze は `country / date / value / fetched_at`。国の一覧（`bronze/<配信元>/_countries.parquet`）は系列ではないので取るたびに置き換える。
`fetch_every` のある系列は、前回の取得から間隔が空いたときだけ取る（年次統計で CI の時間を節約する）。
`refetch_window` の間は毎回取り直し、値が変われば上書きして改定ログに残す。取り直しで返らなかった日は消さない。

`fetched_at` は**その値を最後に書き換えた時刻**（UTC）。値が変わらない行は最初に取得した時刻のまま進まない。
取得が動いているかの判定には使わず、系列ごとの最終 `date` で判定する。

**利用側との契約**: `silver/observations.parquet` の列名・意味と系列 id は、変える前に利用側へ知らせる。
**表の契約**: silver の各表の列・型・意味・1 行の単位・参照は `catalog/contracts/silver.yml`（`client.contract()`）。
`scripts/validate.py` が毎回確かめ、違反すれば CI は保存先に書かない。列を足す・意味を変えるときは、契約と `pipeline.SCHEMA_VERSION` を同じ変更で直す。
国をまたぐ系列は `observations.parquet` に混ぜない（利用側は `series_id / date` が重ならない前提で読む）。

## 系列の追加

1. 作成元が `catalog/sources.yml` に無ければ追加する
2. `catalog/series/<配信元>.yml` に定義を書く。単位は表示用の `unit` とは別に、機械が読む `unit_kind`（percent / index / currency / fx / count）・`unit_of`（% なら何に対する比、指数なら基準、為替なら通貨の組）・`currency`（ISO 4217）・`scale`（10 の何乗か。十億 = 9）・`price_basis`（nominal / real）を書く。`definition`（何を測っているか）・`notes`（季節調整・欠けている期間・定義の変更・比べるときの注意など読むときの前提）・`source_url`（公式の定義のページ）は必須
3. 新しい配信元は `src/psdata/sources/<配信元>.py` に `fetch(series, start)` を書き、`FETCHERS` に登録する
   見通し（未来の期間）を含む系列は `projection_years` を書く（書かなければ未来の日付は検証で止まる）
4. `scripts/validate.py` と `pytest` を通す

## 必要なときに引き出す

- `client.search("...")` — 取得元の指標を探す（世界銀行 WDI の全指標。FRED は `FRED_API_KEY` があれば）。`in_catalog` は常備済みか
- `client.pull("wb.SE.PRM.ENRR")` — カタログに無い系列をその場で取る。**手元の `data/ondemand/` にだけ置き、保存先には書かない**。20 時間以内に取ったものは手元から返す
- `client.pulled()` — 取り置いた系列の一覧（取った回数・期間）。常備に上げる候補を見る
- `client.draft("wb.SE.PRM.ENRR", "u04")` — 常備に上げるときのカタログの下書き（`definition` と `notes` は書き直す）

取り置きの段階では収録基準を確かめていない（FRED には再配布できない系列が混ざる）。
**常備に上げる基準**: 2 回以上の作業で使う・CI や別のプロジェクトから読む・改定を追いたい、のどれかに当たり、収録基準を満たすもの。上げたら下の「系列の追加」に従う。

## SQL で読む（DuckDB）

```python
client.sql("select * from obs where series_id = 'fred.UNRATE' order by date desc limit 3")
client.sql("select * from as_of('2027-01-15') where series_id = 'fred.M2SL'")
client.sql("select ...", source="r2")   # 保存先を直接読む（pull しなくてよい）
con = client.db()                        # duckdb の接続
```

- 保存の形は Parquet のまま。DuckDB はビューを被せるだけで、何も書かない
- ビュー: `obs`（単一の系列とパネルを合わせた観測値）/ `observations` / `panel` / `series` / `profile` / `vintages` / `countries` / `groups`、表関数 `as_of(t)`（定義は `src/psdata/db.py`）
- `source="r2"` は読み取り専用の鍵を使う: 環境変数 `PSDATA_R2_KEY_ID` / `PSDATA_R2_SECRET` / `PSDATA_R2_ENDPOINT`、無ければ rclone の remote `r2ps`。鍵はメモリの中だけで使う。初回は DuckDB が httpfs 拡張を取ってくる

## 目的に合わせて切り出す（アドホックな利用）

```python
df = client.extract(["wb.NY.GDP.MKTP.KD.ZG", "wb.FP.CPI.TOTL.ZG"], countries="g7",
                    start="2015-01-01", measures=["value", "yoy_diff"])   # tidy: 1 行 = 1 観測
client.export(df, "exports/20261123-ca/data.xlsx")   # .xlsx / .csv / .parquet ＋ .card.md / .card.json
client.to_wide(df, columns="country")                 # 横持ち（表・グラフ用）
```

- 列: `series_id / title_ja / country / country_name / date / period / measure / value / unit / unit_kind / currency / scale`。率の系列の差（`yoy_diff`）の単位はポイント。`base_units=True` で倍率を値に掛けて 1 単位（1 ドルなど）にそろえる
- `measure`: `value` / `yoy_pct` / `yoy_diff` / `pop_pct` / `pop_diff`（定義は `src/psdata/extract.py`。基準は観測日で今日ではない。前の値が無ければ空で、0 で埋めない）
- データカードに出典・クレジット・ライセンス・定義・前提・鮮度・切り出しの条件が入る。外で使うときは `credits` を出典として書く
- AI に目的を伝えて資料（pptx・HTML・グラフ）まで作るときの手順はスキル `skills/psdata-adhoc/SKILL.md`（`~/.claude/skills/psdata-adhoc` にリンクして使う）

## 実行（Mac は `.venv-mac/` と `python3`、Windows は `.venv/` と `py`）

```bash
python3 -m venv .venv-mac && .venv-mac/bin/python3 -m pip install -e '.[dev]'
.venv-mac/bin/python3 scripts/fetch.py
.venv-mac/bin/python3 scripts/validate.py
.venv-mac/bin/python3 -m pytest
```

環境変数: `FRED_API_KEY`（任意。FRED の公式 API と検索）・`ESTAT_APP_ID`（e-Stat）。Mac ではキーチェーンのサービス `psdata-fred-api-key` / `psdata-estat-app-id` からも読む（`src/psdata/secrets.py`）。CI は GitHub Secrets の同じ名前。`PSDATA_DIR`（データの場所。既定は `data/`）、`PSDATA_R2_KEY_ID` / `PSDATA_R2_SECRET` / `PSDATA_R2_ENDPOINT`（任意。`client.sql(source="r2")` 用）

## 保存先と定期取得

- 保存先は Cloudflare R2（非公開）。**書くのは `.github/workflows/fetch.yml` だけ**。手元からは書かない
- 手元へは `./data_sync.sh pull`（rclone remote `r2ps:`）
- CI の公開ログは成否と件数だけ。詳細は保存先の `logs/`

## 読み方

```python
from psdata import client
client.read("fred.UNRATE")
client.read(["fred.CPIAUCSL", "boj.FM08.FXERD04"], start="2020-01-01")
client.read("wb.NY.GDP.MKTP.KD.ZG", countries="g7")          # groups.yml の名前
client.read("wb.FP.CPI.TOTL.ZG", countries=["GTM", "MEX", "region:LCN"])  # ISO3・世界銀行の区分を混ぜてよい
client.catalog()
client.countries()
client.groups()
client.read("fred.M2SL", as_of="2027-01-15")  # その時点で知り得た値（取得した時刻が基準。2026-11-23 より前は不可）
client.profile("fred.UNRATE")   # 系列ごとの特徴
client.manifest()
print(client.describe("wb.FP.CPI.TOTL.ZG"))  # 何を測っているか・前提・出典
```

## 規約

- Python は PEP 8。ファイル名はスネークケース。文書は日本語
