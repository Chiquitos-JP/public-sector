#!/usr/bin/env python3
"""常備（stocked）系列を取り直して bronze を更新し、silver を作り直す。

    python3 scripts/fetch.py                 # 常備の全系列
    python3 scripts/fetch.py fred.UNRATE     # 指定した系列だけ
    python3 scripts/fetch.py --full          # 定義上の開始日から全部取り直す

``fetch_every`` のある系列は、前回の取得から間隔が空いたときだけ取る（id を指定したときと --full は常に取る）。

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
    if args.ids:
        targets = [by_id[i] for i in args.ids]
    else:
        stocked = [s for s in series if s.tier == "stocked"]
        targets = stocked if args.full else [s for s in stocked if pipeline.is_due(s)]
        skipped = len(stocked) - len(targets)
        if skipped:
            print(f"  ⏭️ 間隔に達していない {skipped} 系列は取らない")

    failed = 0
    panel_sources = {s.distributor for s in targets if s.is_panel}
    if panel_sources:
        try:
            for d, n in pipeline.refresh_references(panel_sources).items():
                print(f"  ✅ {d} 国の一覧 — {n} 件")
        except Exception as e:  # 国の一覧が取れなくても系列の取得は続ける（前回の一覧を使う）
            print(f"  ❌ 国の一覧の取得に失敗: {e}")
            failed += 1
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

    obs, pan, table = pipeline.build_silver(series, sources)
    print(f"silver: 単一 {len(obs)} 行 / {obs['series_id'].nunique()} 系列、パネル {len(pan)} 行 / {pan['series_id'].nunique()} 系列")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
