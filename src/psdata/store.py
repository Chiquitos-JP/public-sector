"""系列ごとの時系列ストア（bronze）。

## 前提

1. **過去の値は改定されうる。** 系列ごとの ``refetch_window`` の間は毎回取り直し、
   値が変わっていれば新しい値で上書きする。変更前後は改定ログ（``*.revisions.parquet``）に残す。
2. **窓より前は取り直さない。** 最終記録日 − ``refetch_window`` から先だけを取得する。
3. **検証に通らないものは書かない。** 書き込み前に新規分とマージ後の両方を検証する。
4. **蓄積は減らさない。** 取り直しで返ってこなかった日は消さずに残し、警告する。

## ファイル

- ``<id>.parquet``             列: date / value / fetched_at（その値を最後に書き換えた時刻・UTC）/
                               first_seen_at（その行を初めて取得した時刻・UTC。変わらない）
- ``<id>.revisions.parquet``   列: date / old_value / new_value / detected_at

国をまたぐ系列（パネル）は ``keys=("country", "date")`` で扱い、どのファイルにも ``country`` 列が付く。
- ``<id>.parquet.provenance.json``  取得ごとの出所・パラメータ・件数・検証結果（全件残す）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROVENANCE_SUFFIX = ".provenance.json"
REVISIONS_SUFFIX = ".revisions.parquet"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "Report") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


@dataclass
class UpdateResult:
    path: Path
    written: bool
    rows_before: int
    rows_after: int
    rows_added: int
    rows_revised: int
    date_max: str | None
    report: Report


def revisions_path(path: Path) -> Path:
    return path.with_name(path.stem + REVISIONS_SUFFIX)


def provenance_path(path: Path) -> Path:
    return path.with_name(path.name + PROVENANCE_SUFFIX)


def read(path: Path) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    df = pd.read_parquet(path)
    return None if df.empty else df


def _backfill_first_seen(existing: pd.DataFrame, path: Path, keys: tuple[str, ...]) -> pd.DataFrame:
    """``first_seen_at`` の無い古いストアに補う。

    改定されていない行は ``fetched_at`` がそのまま初めて取った時刻。改定された行は元の値を
    最初の取得で見ていたので、取得記録の最初の時刻（無ければ最初の改定の時刻）にする。
    """
    out = existing.copy()
    out["first_seen_at"] = pd.to_datetime(out["fetched_at"], utc=True)
    rp = revisions_path(path)
    if not rp.is_file():
        return out
    rev = pd.read_parquet(rp)
    if rev.empty:
        return out
    pp = provenance_path(path)
    records = json.loads(pp.read_text(encoding="utf-8")).get("records") if pp.is_file() else []
    first_fetch = pd.Timestamp(records[0]["fetched_at"]) if records else None
    rev = rev.astype({k: "string" for k in keys if k != "date"})
    earliest = rev.groupby(list(keys))["detected_at"].min().rename("_first_rev").reset_index()
    out = out.astype({k: "string" for k in keys if k != "date"}).merge(earliest, on=list(keys), how="left")
    revised = out["_first_rev"].notna()
    bound = pd.to_datetime(out["_first_rev"], utc=True)
    if first_fetch is not None:
        bound = pd.Series(first_fetch, index=out.index).where(revised)
    out.loc[revised, "first_seen_at"] = bound[revised]
    return out.drop(columns="_first_rev")


def fetch_start(path: Path, *, default_start: str, window: pd.DateOffset) -> str:
    """取得の開始日。ストアが無ければ ``default_start``、あれば最終記録日 − 窓。"""
    df = read(path)
    if df is None:
        return default_start
    start = pd.Timestamp(df["date"].max()) - window
    return max(start, pd.Timestamp(default_start)).date().isoformat()


def _normalize(fresh: pd.DataFrame, keys: tuple[str, ...]) -> pd.DataFrame:
    out = fresh[[*keys, "value"]].copy()
    for k in keys:
        if k != "date":
            out[k] = out[k].astype("string")
    d = pd.to_datetime(out["date"])
    if getattr(d.dtype, "tz", None) is not None:
        d = d.dt.tz_localize(None)
    out["date"] = d.astype("datetime64[ns]")
    out["value"] = pd.to_numeric(out["value"], errors="coerce").astype("float64")
    return out


def validate_frame(
    df: pd.DataFrame,
    *,
    label: str,
    max_abs_pct_change: float | None = None,
    allow_future_days: int = 1,
    keys: tuple[str, ...] = ("date",),
) -> Report:
    """構造の検証。errors は書き込みを止める。warnings は記録して続ける。

    ``keys`` は 1 行を決める列（単一系列は date、パネルは country / date）。重複と並び順はこの単位で見る。
    """
    rep = Report()
    if df is None or df.empty:
        rep.errors.append(f"{label}: 空のデータ")
        return rep
    dates = pd.to_datetime(df["date"])
    if dates.isna().any():
        rep.errors.append(f"{label}: 日付が欠けた行がある ({int(dates.isna().sum())} 行)")
    other = [k for k in keys if k != "date"]
    for k in other:
        if df[k].isna().any():
            rep.errors.append(f"{label}: {k} が欠けた行がある ({int(df[k].isna().sum())} 行)")
    dup = df.duplicated(list(keys))
    if dup.any():
        rep.errors.append(f"{label}: {'・'.join(keys)} が重複している ({int(dup.sum())} 行)")
    if other:
        k = df[list(keys)].reset_index(drop=True)
        if not k.equals(k.sort_values(list(keys)).reset_index(drop=True)):
            rep.errors.append(f"{label}: {'・'.join(keys)} の昇順でない")
    elif not dates.dropna().is_monotonic_increasing:
        rep.errors.append(f"{label}: 日付が昇順でない")
    limit = pd.Timestamp.now().normalize() + pd.Timedelta(days=allow_future_days)
    if (dates > limit).any():
        rep.errors.append(f"{label}: 未来日の行がある ({int((dates > limit).sum())} 行)")
    if not pd.api.types.is_float_dtype(df["value"]):
        rep.errors.append(f"{label}: value が浮動小数でない ({df['value'].dtype})")
        return rep
    if df["value"].isna().all():
        rep.errors.append(f"{label}: 値がすべて欠損")
    elif df["value"].isna().any():
        rep.warnings.append(f"{label}: 値が欠損した行がある ({int(df['value'].isna().sum())} 行)")
    if max_abs_pct_change is not None and len(df) > 1 and not other:
        chg = df["value"].pct_change(fill_method=None).abs().dropna()
        n = int((chg > max_abs_pct_change).sum())
        if n:
            rep.warnings.append(
                f"{label}: 前期比 {max_abs_pct_change:.0%} 超の変化が {n} 件（最大 {chg.max():.1%}）"
            )
    return rep


def _record_provenance(path: Path, record: dict[str, Any]) -> None:
    pp = provenance_path(path)
    data = json.loads(pp.read_text(encoding="utf-8")) if pp.is_file() else {"store": path.name, "records": []}
    # 取得の証拠なので消さない（raw は保存先で 90 日で消えるため、こちらが長期の記録になる）
    data["records"] = data["records"] + [record]
    pp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def update(
    path: Path,
    fresh: pd.DataFrame,
    *,
    start: str,
    source: str,
    params: dict[str, Any] | None = None,
    max_abs_pct_change: float | None = 1.0,
    revision_warn_pct: float = 0.05,
    label: str | None = None,
    keys: tuple[str, ...] = ("date",),
    context: dict[str, Any] | None = None,
    allow_future_days: int = 1,
    key_aliases: dict[str, str] | None = None,
) -> UpdateResult:
    """``start`` 以降を取り直した ``fresh``（keys / value）でストアを更新する。

    ``context`` は取得記録に添える「誰が・どの版で・どの応答から」（``psdata.pipeline.run_context``）。
    ``key_aliases`` は国コードの別名 → そろえる先（``psdata.harmonize.CODE_ALIASES``）。既存の行のコードも寄せる。
    """
    path = Path(path)
    name = label or path.stem
    now = pd.Timestamp(datetime.now(timezone.utc))
    start_ts = pd.Timestamp(start)

    keys = tuple(keys)
    fresh_n = _normalize(fresh, keys)
    fresh_n = fresh_n[fresh_n["date"] >= start_ts].sort_values(list(keys)).reset_index(drop=True)
    rep = validate_frame(fresh_n, label=f"{name}/新規", max_abs_pct_change=max_abs_pct_change, keys=keys,
                         allow_future_days=allow_future_days)

    existing = read(path)
    rows_before = 0 if existing is None else len(existing)

    def _result(written: bool, merged: pd.DataFrame | None, added: int = 0, revised: int = 0) -> UpdateResult:
        return UpdateResult(
            path=path,
            written=written,
            rows_before=rows_before,
            rows_after=rows_before if merged is None else len(merged),
            rows_added=added,
            rows_revised=revised,
            date_max=None if merged is None or merged.empty else str(merged["date"].max().date()),
            report=rep,
        )

    if not rep.ok:
        return _result(False, None)

    revisions = pd.DataFrame(columns=[*keys, "old_value", "new_value", "detected_at"])
    if existing is None:
        merged = fresh_n.assign(fetched_at=now, first_seen_at=now)
        added, revised = len(merged), 0
    else:
        if "first_seen_at" not in existing.columns:
            existing = _backfill_first_seen(existing, path, keys)
        if key_aliases and "country" in existing.columns and existing["country"].isin(list(key_aliases)).any():
            renamed = existing["country"].isin(list(key_aliases))
            existing = existing.assign(country=existing["country"].replace(key_aliases))
            # 寄せた先にすでに行があれば、寄せた先の行を残す（同じ国・同じ期間を 2 行にしない）
            existing = existing.assign(_renamed=renamed).sort_values("_renamed")
            dropped = existing.duplicated(list(keys))
            if dropped.any():
                rep.warnings.append(f"{name}: 国コードを寄せて重なった {int(dropped.sum())} 行は寄せた先の行を残した")
            existing = existing[~dropped].drop(columns="_renamed").sort_values(list(keys)).reset_index(drop=True)
            rep.warnings.append(f"{name}: 国コードの別名を寄せた {sorted(set(key_aliases))}")
        existing = existing.astype({k: "string" for k in keys if k != "date"})
        both = existing.merge(fresh_n, on=list(keys), how="outer", suffixes=("_old", "_new"), indicator=True)
        is_new = both["_merge"] == "right_only"
        in_both = both["_merge"] == "both"
        changed = in_both & ~np.isclose(
            both["value_old"].astype("float64"), both["value_new"].astype("float64"), rtol=1e-9, atol=0, equal_nan=True
        )
        missing_in_window = (both["_merge"] == "left_only") & (both["date"] >= start_ts)
        if missing_in_window.any():
            rep.warnings.append(
                f"{name}: 取り直しで返ってこなかった日が {int(missing_in_window.sum())} 件（既存の値を残した）"
            )

        revisions = pd.DataFrame(
            {
                **{k: both.loc[changed, k] for k in keys},
                "old_value": both.loc[changed, "value_old"].astype("float64"),
                "new_value": both.loc[changed, "value_new"].astype("float64"),
                "detected_at": now,
            }
        ).reset_index(drop=True)
        if len(revisions):
            rel = (revisions["new_value"] - revisions["old_value"]).abs() / revisions["old_value"].abs()
            big = int((rel > revision_warn_pct).sum())
            if big:
                rep.warnings.append(
                    f"{name}: {revision_warn_pct:.0%} を超える改定が {big} 件（最大 {rel.max():.1%}）。取得誤りでないか確認"
                )

        take_new = is_new | changed
        merged = pd.DataFrame(
            {
                **{k: both[k] for k in keys},
                "value": both["value_new"].where(take_new, both["value_old"]).astype("float64"),
                "fetched_at": both["fetched_at"].where(~take_new, now),
                "first_seen_at": both["first_seen_at"].where(~is_new, now),
            }
        )
        merged = merged.sort_values(list(keys)).reset_index(drop=True)
        added, revised = int(is_new.sum()), int(changed.sum())

    merged["fetched_at"] = pd.to_datetime(merged["fetched_at"], utc=True)
    merged["first_seen_at"] = pd.to_datetime(merged["first_seen_at"], utc=True)
    rep.extend(validate_frame(merged, label=f"{name}/マージ後", keys=keys, allow_future_days=allow_future_days))
    if len(merged) < rows_before:
        rep.errors.append(f"{name}: 行数が減る ({rows_before} → {len(merged)})。蓄積を失うので中止")
    if not rep.ok:
        return _result(False, merged, added, revised)

    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    if len(revisions):
        rp = revisions_path(path)
        prev = pd.read_parquet(rp) if rp.is_file() else None
        log = revisions if prev is None else pd.concat([prev, revisions], ignore_index=True)
        log.to_parquet(rp, index=False)

    result = _result(True, merged, added, revised)
    _record_provenance(
        path,
        {
            "fetched_at": now.isoformat(timespec="seconds"),
            "source": source,
            "params": {**(params or {}), "fetch_start": start},
            **(context or {}),
            "rows_before": rows_before,
            "rows_after": result.rows_after,
            "rows_added": added,
            "rows_revised": revised,
            "date_max": result.date_max,
            "validation": {"errors": rep.errors, "warnings": rep.warnings},
        },
    )
    return result
