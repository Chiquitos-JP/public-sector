"""系列カタログの読み込みと収録基準の検証。

収録基準（AGENTS.md）をここで機械的に確かめる:
1. 作成元が公的機関として ``sources.yml`` の creators に載っている
2. ライセンスが allowed_licenses にある
3. ``used_by`` が空でない

``country: ALL`` の系列はパネル（国をまたぐ。1 行は country / date で決まる）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from psdata.paths import CATALOG_DIR

TIERS = {"stocked", "on_demand"}
FREQS = {"D", "W", "M", "Q", "A"}
#: 単位の種類（機械が読む）。unit（表示用の文字）とは別に持ち、換算や計算の判断に使う
#: percent: 率・比率（%）。unit_of に何に対する比か（GDP・labor_force など）／index: 指数（unit_of に基準）／
#: currency: 金額（currency に ISO 4217、scale に 10 の何乗か）／fx: 為替（unit_of に JPY/USD など）／count: 個数・人数
UNIT_KINDS = {"percent", "index", "currency", "fx", "count"}
PRICE_BASES = {"", "nominal", "real"}
PANEL = "ALL"
#: 頻度ごとに、最終日がこれより古ければ「古い」とする日数（公表の遅れを見込んだ値）。
#: 年次は日付が年の初日で、財政などは公表が 2 年ほど遅れるので長めにとる
MAX_STALE_DAYS = {"D": 10, "W": 21, "M": 90, "Q": 150, "A": 1200}
_WINDOW_RE = re.compile(r"^(\d+)([DWMY])$")


@dataclass(frozen=True)
class Series:
    id: str
    title_ja: str
    title_en: str
    creator: str
    distributor: str
    country: str
    freq: str
    unit: str
    seasonal_adjustment: str
    tier: str
    refetch_window: str
    start: str
    used_by: tuple[str, ...]
    #: 単位の種類（UNIT_KINDS）・何に対する値か・通貨（ISO 4217）・10 の何乗か・名目か実質か
    unit_kind: str = ""
    unit_of: str = ""
    currency: str = ""
    scale: int = 0
    price_basis: str = ""
    #: 何を測っているか（1〜2 文）
    definition: str = ""
    #: 読むときの大切な前提（季節調整・欠けている期間・定義の変更・比べるときの注意など）
    notes: tuple[str, ...] = ()
    #: 定義を確かめられる公式のページ
    source_url: str = ""
    #: 作成元が他機関の統計を編集して載せている場合の原典（クレジットに添える）
    original_source: str = ""
    #: 取得の間隔（例: 7D）。空なら毎回取る。更新の少ない年次統計で CI の時間を節約する
    fetch_every: str = ""
    #: 見通し（未来の期間）を何年先まで含むか。0 なら未来の日付は検証で止める
    projection_years: int = 0

    @property
    def allow_future_days(self) -> int:
        return 1 + 366 * self.projection_years

    @property
    def is_panel(self) -> bool:
        return self.country == PANEL

    @property
    def keys(self) -> tuple[str, ...]:
        return ("country", "date") if self.is_panel else ("date",)

    @property
    def code(self) -> str:
        """配信元での元のコード（``fred.UNRATE`` → ``UNRATE``）。"""
        return self.id.split(".", 1)[1]


def parse_window(window: str) -> pd.DateOffset:
    m = _WINDOW_RE.match(window)
    if not m:
        raise ValueError(f"refetch_window の形式が不正: {window!r}（例: 10D, 8W, 13M, 5Y）")
    n, unit = int(m.group(1)), m.group(2)
    return {
        "D": pd.DateOffset(days=n),
        "W": pd.DateOffset(weeks=n),
        "M": pd.DateOffset(months=n),
        "Y": pd.DateOffset(years=n),
    }[unit]


def load_sources(catalog_dir: Path = CATALOG_DIR) -> dict:
    return yaml.safe_load((catalog_dir / "sources.yml").read_text(encoding="utf-8"))


def load_series(catalog_dir: Path = CATALOG_DIR) -> list[Series]:
    out: list[Series] = []
    for f in sorted((catalog_dir / "series").glob("*.yml")):
        for entry in yaml.safe_load(f.read_text(encoding="utf-8")) or []:
            entry = dict(entry)
            entry["used_by"] = tuple(entry.get("used_by") or ())
            entry["notes"] = tuple(entry.get("notes") or ())
            out.append(Series(**entry))
    return out


def load_groups(catalog_dir: Path = CATALOG_DIR) -> dict[str, dict]:
    """国の組み合わせ（catalog/groups.yml）。"""
    f = catalog_dir / "groups.yml"
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}) if f.is_file() else {}


def validate_groups(groups: dict[str, dict], known: set[str] | None = None) -> list[str]:
    """国コードの形と重複を確かめる。``known`` があれば国一覧に無いコードも落とす。"""
    errors = []
    for name, g in groups.items():
        codes = list((g or {}).get("countries") or [])
        if not codes:
            errors.append(f"グループ {name}: countries が空")
        bad = [c for c in codes if not re.fullmatch(r"[A-Z]{3}", str(c))]
        if bad:
            errors.append(f"グループ {name}: ISO3 でないコード {bad}")
        if len(set(codes)) != len(codes):
            errors.append(f"グループ {name}: 国が重複している")
        if known is not None:
            missing = sorted(set(codes) - known - set(bad))
            if missing:
                errors.append(f"グループ {name}: 国一覧に無いコード {missing}")
    return errors


def validate(series: list[Series], sources: dict) -> list[str]:
    """収録基準と定義の整合を確かめ、違反を文字列で返す（空なら合格）。"""
    errors: list[str] = []
    creators = sources.get("creators", {})
    distributors = sources.get("distributors", {})
    licenses = sources.get("allowed_licenses", {})

    for name, c in creators.items():
        if c.get("license") not in licenses:
            errors.append(f"作成元 {name}: ライセンス {c.get('license')!r} が allowed_licenses に無い")

    seen: set[str] = set()
    for s in series:
        pre = f"{s.id}:"
        if s.id in seen:
            errors.append(f"{pre} id が重複している")
        seen.add(s.id)
        if s.id.split(".", 1)[0] != s.distributor:
            errors.append(f"{pre} id の接頭辞が配信元 {s.distributor!r} と一致しない")
        if s.creator not in creators:
            errors.append(f"{pre} 作成元 {s.creator!r} が sources.yml の creators に無い（基準 1・2）")
        if s.distributor not in distributors:
            errors.append(f"{pre} 配信元 {s.distributor!r} が sources.yml の distributors に無い")
        if not s.used_by:
            errors.append(f"{pre} used_by が空（基準 3: 利用先の無い系列は持たない）")
        if s.unit_kind not in UNIT_KINDS:
            errors.append(f"{pre} unit_kind {s.unit_kind!r} は {sorted(UNIT_KINDS)} のいずれか")
        if s.unit_kind in {"currency", "fx"} and not re.fullmatch(r"[A-Z]{3}", s.currency or ""):
            errors.append(f"{pre} unit_kind {s.unit_kind} には currency（ISO 4217 の 3 文字）が要る")
        if s.unit_kind in {"index", "fx"} and not s.unit_of:
            errors.append(f"{pre} unit_kind {s.unit_kind} には unit_of（指数の基準・通貨の組）が要る")
        if not isinstance(s.scale, int) or not -3 <= s.scale <= 15:
            errors.append(f"{pre} scale は 10 の何乗かの整数（例: 十億 = 9）")
        if not isinstance(s.projection_years, int) or not 0 <= s.projection_years <= 10:
            errors.append(f"{pre} projection_years は 0〜10 の整数")
        if s.price_basis not in PRICE_BASES:
            errors.append(f"{pre} price_basis は nominal / real / 空")
        if not s.definition.strip():
            errors.append(f"{pre} definition が空（何を測っているかを書く）")
        if not s.source_url.startswith("https://"):
            errors.append(f"{pre} source_url が無い（定義を確かめられる公式のページ）")
        if s.tier not in TIERS:
            errors.append(f"{pre} tier {s.tier!r} は {sorted(TIERS)} のいずれか")
        if s.freq not in FREQS:
            errors.append(f"{pre} freq {s.freq!r} は {sorted(FREQS)} のいずれか")
        if not re.fullmatch(r"[A-Z]{3}", s.country):
            errors.append(f"{pre} country {s.country!r} は ISO3 の大文字 3 文字（パネルは {PANEL}）")
        for name, w in (("refetch_window", s.refetch_window), ("fetch_every", s.fetch_every)):
            if name == "fetch_every" and not w:
                continue
            try:
                parse_window(w)
            except ValueError as e:
                errors.append(f"{pre} {name}: {e}")
    return errors


def series_table(series: list[Series], sources: dict) -> pd.DataFrame:
    """利用側に配るカタログ（silver/series.parquet）。クレジットとライセンスを含める。"""
    creators = sources["creators"]
    rows = []
    for s in series:
        c = creators[s.creator]
        rows.append(
            {
                "series_id": s.id,
                "title_ja": s.title_ja,
                "title_en": s.title_en,
                "definition": s.definition,
                "notes": "\n".join(s.notes),
                "country": s.country,
                "freq": s.freq,
                "unit": s.unit,
                "unit_kind": s.unit_kind,
                "unit_of": s.unit_of,
                "currency": s.currency,
                "scale": s.scale,
                "price_basis": s.price_basis,
                "seasonal_adjustment": s.seasonal_adjustment,
                "creator": c["name"],
                "license": c["license"],
                "credit": c["credit"] + (f"（原典: {s.original_source}）" if s.original_source else ""),
                "distributor": sources["distributors"][s.distributor]["name"],
                "source_url": s.source_url,
                "tier": s.tier,
                "projection_years": s.projection_years,
            }
        )
    return pd.DataFrame(rows)
