# public-sector

公開統計を取得・整形し、非公開の保存先へ置くパイプライン（パッケージ名 `psdata`）。

- このリポジトリは公開されている。**秘密情報・個人のパス・非公開データ・利用先の固有名をコミットしない**
- 補足の運用メモ（非公開）が `~/dev/knowledge-base/projects/public-sector/psdata-notes.md` にあれば先に読む

## 収録基準（すべて満たすものだけ）

1. 公的機関が作成した統計（判断は作成元。配信元ではない）。市場価格は対象外
2. `catalog/sources.yml` の `allowed_licenses` にあるライセンス
3. `used_by` に利用先 ID（`u01` など）がある。固有名は書かない

## 構成

```
catalog/sources.yml        作成元・ライセンス・クレジット・配信元
catalog/series/*.yml       系列の定義
src/psdata/                catalog / store / sources / pipeline / client
scripts/fetch.py           常備系列の取得（--full で全期間取り直し）
scripts/validate.py        カタログとストアの検証（再取得しない）
data/                      git に入れない
```

## データの層

- `raw/<配信元>/<コード>/<時刻>.*` — 応答そのまま。変更しない
- `bronze/<配信元>/<id>.parquet` — `date / value / fetched_at`。改定ログ `*.revisions.parquet`、取得記録 `*.provenance.json`
- `silver/observations.parquet` — `series_id / country / date / value / fetched_at`
- `silver/series.parquet` — カタログ（クレジット・ライセンス）

系列 id は `<配信元>.<元のコード>`。`country` は ISO3、`date` は期間の開始日。
`refetch_window` の間は毎回取り直し、値が変われば上書きして改定ログに残す。取り直しで返らなかった日は消さない。

## 系列の追加

1. 作成元が `catalog/sources.yml` に無ければ追加する
2. `catalog/series/<配信元>.yml` に定義を書く
3. 新しい配信元は `src/psdata/sources/<配信元>.py` に `fetch(series, start)` を書き、`FETCHERS` に登録する
4. `scripts/validate.py` と `pytest` を通す

## 実行（Mac は `.venv-mac/` と `python3`、Windows は `.venv/` と `py`）

```bash
python3 -m venv .venv-mac && .venv-mac/bin/python3 -m pip install -e '.[dev]'
.venv-mac/bin/python3 scripts/fetch.py
.venv-mac/bin/python3 scripts/validate.py
.venv-mac/bin/python3 -m pytest
```

環境変数: `FRED_API_KEY`（任意）、`PSDATA_DIR`（データの場所。既定は `data/`）

## 保存先と定期取得

- 保存先は Cloudflare R2（非公開）。**書くのは `.github/workflows/fetch.yml` だけ**。手元からは書かない
- 手元へは `./data_sync.sh pull`（rclone remote `r2ps:`）
- CI の公開ログは成否と件数だけ。詳細は保存先の `logs/`

## 読み方

```python
from psdata import client
client.read("fred.UNRATE")
client.read(["fred.CPIAUCSL", "boj.FM08.FXERD04"], start="2020-01-01")
client.catalog()
```

## 規約

- Python は PEP 8。ファイル名はスネークケース。文書は日本語
