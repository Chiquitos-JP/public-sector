---
paths:
  - "src/**/*"
  - "scripts/**/*"
  - "catalog/**/*"
  - "data/**/*"
---

# データパイプラインルール

- raw 層のデータは直接編集しない（取り直す）
- 系列を足すときは AGENTS.md の収録基準と「系列の追加」に従う。`used_by` には利用先 ID だけを書く
- ストアの書き込みは `psdata.store.update` だけを通す（検証・改定ログ・provenance を飛ばさない）。例外は国の一覧などの参照表（`pipeline.refresh_references`）
- 処理は冪等に保つ（何度実行しても同じ結果になる）
- データ形式は Parquet
