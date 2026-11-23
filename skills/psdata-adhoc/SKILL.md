---
name: psdata-adhoc
description: 公開統計（public-sector の psdata）から、目的に合わせた tidy な表を切り出し、データカード付きで書き出して、資料（プレゼン・HTML・グラフ）を作る。各国・米国・日本の経済・財政・社会の統計（GDP・物価・失業・金利・為替・債務・人口・貧困など）を使った分析や資料づくりを頼まれたとき、「〜の数字で資料を作って」「〜を比べたい」と言われたときに使う。
---

# 公開統計からのアドホックな切り出しと資料づくり

普段の使い方（`client.read` など）はそのまま。ここは**ときどき目的に応じて切り出して外で使う**ための手順。
データの正本は public-sector。ここでは**読むだけ**で、保存先（R2）には書かない。

## 0. 準備（最初の 1 回・その機で）

- public-sector が `~/dev/public-sector` にあり、データが手元にあること: `cd ~/dev/public-sector && ./data_sync.sh pull`
- Python は public-sector の venv を使う
  - Mac: `~/dev/public-sector/.venv-mac/bin/python3`
  - Windows: `~/dev/public-sector/.venv/Scripts/python.exe`（`py` ではなく venv の python）
- 無ければ `~/dev/MACHINE_AND_INFRA.md` の「プロジェクト: public-sector」の手順で用意する

## 1. 目的を確かめる（短く）

次の 4 つを決める。分からないものだけ聞き、決めた内容を一行で返してから進む。

- **問い**: 何を示したいか（例: G7 の成長と物価の推移を比べる）
- **切り口**: 系列・国（ISO3 / `g7` などのグループ / `region:LCN`・`income:LMC`）・期間
- **値の種類**: `value`（元の値）/ `yoy_pct`（前年同期比 %）/ `yoy_diff`（前年同期差）/ `pop_pct` / `pop_diff`。
  **率の系列（成長率・失業率・金利・GDP 比）は `yoy_pct` ではなく `yoy_diff`**（`unit_kind` が `percent` の系列。求めると警告が出る）
- **単位**: 表の `unit_kind` / `currency` / `scale` を見る。金額を足し合わせたり比べたりするときは `base_units=True` で倍率をそろえる（M2 は十億ドル、FRB の総資産は百万ドル）
- **出力**: 表（Excel / CSV）だけか、資料（pptx・HTML・グラフ）までか

## 2. 系列を選ぶ

```python
from psdata import client
cat = client.catalog()                      # 常備の系列（title_ja・unit・freq・country）
client.search("government debt")            # 取得元の指標を探す（in_catalog=True なら常備）
print(client.describe("wb.GC.DOD.TOTL.GD.ZS"))  # 定義・前提・特徴（欠け・遅れ）・出典
client.groups()                             # 名前付きの国の組み合わせ
```

- `describe` の**前提（notes）を必ず読む**。資料で誤解を生むもの（中央政府だけ・モデル推計・欠けた年・公表の遅れ）は資料の注記に書く
- 常備に無ければ `client.pull("wb.<コード>")` で手元に取る。取り置きは収録基準を確かめていないので、資料に使う前に出典とライセンスを確かめる（`client.draft` の原典・世界銀行の指標ページ）

## 3. 切り出して書き出す

```python
df = client.extract(
    ["wb.NY.GDP.MKTP.KD.ZG", "wb.FP.CPI.TOTL.ZG"],
    countries="g7", start="2015-01-01",
    measures=["value"],
    # as_of="2026-12-01",  # 再現したい時点があれば（2026-11-23 より前は不可）
)
files = client.export(df, "exports/20261123-g7-macro/data.xlsx")
```

- 表は tidy（1 行 = 1 観測）: `series_id / title_ja / country / country_name / date / period / measure / value / unit`
- 横持ちが要るときだけ `client.to_wide(df, columns="country")`
- 書き出し先は**今のプロジェクトの `exports/<日付>-<内容>/`**（ユーザーの指定があればそこ）。public-sector の中には置かない
- `.xlsx` は data と card のシート、`.csv` は Excel で開ける。どちらも `.card.md` / `.card.json`（出典・定義・前提・鮮度・条件）が隣にできる

## 4. 資料を作る

- **書き出したファイルだけ**を材料にする（同じ表から作り直せるように）。数字を手で書き写さない
- どの図・表にも**出典**を入れる: `card.json` の `credits` をそのまま（CC BY・出典明記のライセンスは表示が条件）
- 単位・期間・国を図の中に書く。前年比などは「何と比べたか」を書く（card の `measures`）
- 前提で誤解を生むものは注記にする（例: 「中央政府のみ」「2025-10 は政府閉鎖で欠測」）
- データの最終の期間（card の `last_observation_date`）を資料に書く
- 形式は依頼に合わせる（pptx・HTML・グラフ）。それぞれのスキルや Artifact の手順に従う

## 5. 最後に伝えること

- 作ったファイルの場所（表・データカード・資料）
- 使った系列と出典、注意すべき前提
- 足りなかったデータ（取り置きで済ませたもの・取れなかったもの）。繰り返し使いそうなら「常備に上げる」ことを提案する
