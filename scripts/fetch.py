#!/usr/bin/env python3
"""常備（stocked）系列を取り直して bronze を更新し、silver を作り直す。

    python3 scripts/fetch.py                 # 常備の全系列
    python3 scripts/fetch.py fred.UNRATE     # 指定した系列だけ
    python3 scripts/fetch.py --full          # 定義上の開始日から全部取り直す

終了コード: 0 = すべて成功 / 1 = カタログ違反・取得失敗・検証エラーのいずれかがあった
（失敗した系列の既存データは書き換えない）
"""
from __future__ import annotations

import argparse
import sys

from psdata import catalog, pipeline


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="取得する系列 id（省略時は常備の全系列）")
    ap.add_argument("--full", action="store_true", help="定義上の開始日から取り直す")
    args = ap.parse_args()

    sources = catalog.load_sources()
    series = catalog.load_series()
    errors = catalog.validate(series, sources)
    if errors:
        print("❌ カタログが収録基準を満たしていない")
        for e in errors:
            print(f"  - {e}")
        return 1

    by_id = {s.id: s for s in series}
    unknown = [i for i in args.ids if i not in by_id]
    if unknown:
        print(f"❌ カタログに無い系列: {unknown}")
        return 1
    targets = [by_id[i] for i in args.ids] if args.ids else [s for s in series if s.tier == "stocked"]

    failed = 0
    for s in targets:
        try:
            res = pipeline.refresh(s, full=args.full)
        except Exception as e:  # 1 系列の失敗で他を止めない
            print(f"  ❌ {s.id} — 取得失敗: {e}")
            failed += 1
            continue
        for w in res.report.warnings:
            print(f"    ⚠️ {w}")
        if not res.written:
            print(f"  ❌ {s.id} — 検証で中止（既存データは保持）")
            for e in res.report.errors:
                print(f"    ❌ {e}")
            failed += 1
            continue
        print(f"  ✅ {s.id} — 追加 {res.rows_added} / 改定 {res.rows_revised} / 計 {res.rows_after} 行（〜{res.date_max}）")

    obs, table = pipeline.build_silver(series, sources)
    print(f"silver: {len(obs)} 行 / {obs['series_id'].nunique()} 系列")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
