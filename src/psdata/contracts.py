"""silver の表の契約（``catalog/contracts/silver.yml``）を読み、実物が守っているかを確かめる。

確かめること: 列がそろっていて余計な列が無い・型・空でない列・1 行の単位（主キー）が重ならない・
決まった値だけ・期間の日付に時刻が無い・ほかの表への参照が切れていない。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from psdata.paths import CATALOG_DIR, silver_dir

CONTRACT_PATH = CATALOG_DIR / "contracts" / "silver.yml"


def load(path: Path = CONTRACT_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _type_ok(s: pd.Series, t: str) -> bool:
    if t == "string":
        return pd.api.types.is_string_dtype(s) or pd.api.types.is_object_dtype(s)
    if t == "date":
        return pd.api.types.is_datetime64_dtype(s) and getattr(s.dtype, "tz", None) is None
    if t == "timestamp_utc":
        return isinstance(s.dtype, pd.DatetimeTZDtype) and str(s.dtype.tz) == "UTC"
    if t == "float":
        return pd.api.types.is_float_dtype(s)
    if t == "int":
        return pd.api.types.is_integer_dtype(s)
    if t == "bool":
        return pd.api.types.is_bool_dtype(s)
    raise ValueError(f"契約の type が不正: {t}")


def check_table(name: str, df: pd.DataFrame, spec: dict) -> list[str]:
    errors = []
    cols = spec["columns"]
    missing = [c for c in cols if c not in df.columns]
    extra = [c for c in df.columns if c not in cols]
    if missing:
        errors.append(f"{name}: 契約の列が無い {missing}")
    if extra:
        errors.append(f"{name}: 契約に無い列がある {extra}（契約に書いてから足す）")
    if list(df.columns) != [c for c in cols if c in df.columns] + extra:
        errors.append(f"{name}: 列の順が契約と違う")
    for c, rule in cols.items():
        if c not in df.columns:
            continue
        s = df[c]
        if not _type_ok(s, rule["type"]):
            errors.append(f"{name}.{c}: 型が {s.dtype}（契約は {rule['type']}）")
            continue
        if not rule.get("nullable") and s.isna().any():
            errors.append(f"{name}.{c}: 空が {int(s.isna().sum())} 行（契約は空を許さない）")
        if rule["type"] == "date" and s.notna().any() and (s.dropna() != s.dropna().dt.normalize()).any():
            errors.append(f"{name}.{c}: 期間の日付に時刻が入っている")
        if "values" in rule:
            bad = sorted(set(s.dropna().astype(str)) - {str(v) for v in rule["values"]})
            if bad:
                errors.append(f"{name}.{c}: 決まっていない値 {bad[:5]}")
    pk = [c for c in spec.get("primary_key", []) if c in df.columns]
    if pk and len(pk) == len(spec["primary_key"]):
        dup = df.duplicated(pk)
        if dup.any():
            errors.append(f"{name}: {'・'.join(pk)} が重なる行が {int(dup.sum())}")
    return errors


def check_references(tables: dict[str, pd.DataFrame], contract: dict) -> list[str]:
    errors = []
    for name, spec in contract["tables"].items():
        df = tables.get(name)
        if df is None:
            continue
        for c, rule in spec["columns"].items():
            ref = rule.get("references")
            if not ref or c not in df.columns:
                continue
            rt, rc = ref.split(".")
            target = tables.get(rt)
            if target is None or rc not in target.columns:
                continue
            missing = sorted(set(df[c].dropna()) - set(target[rc]))
            if missing:
                errors.append(f"{name}.{c}: {rt}.{rc} に無い値 {missing[:5]}（{len(missing)} 件）")
    return errors


def check_silver(directory: Path | None = None, contract: dict | None = None) -> list[str]:
    """silver のすべての表を契約と照らす。無い表は、観測値の 2 つ以外は飛ばす。"""
    directory = directory or silver_dir()
    contract = contract or load()
    tables, errors = {}, []
    for name, spec in contract["tables"].items():
        path = directory / f"{name}.parquet"
        if not path.is_file():
            if name in ("observations", "series"):
                errors.append(f"{name}: silver に表が無い")
            continue
        tables[name] = df = pd.read_parquet(path)
        errors += check_table(name, df, spec)
    errors += check_references(tables, contract)
    return errors
