"""取得元が違っても同じものは同じに見えるようにそろえる（国コード・国名の日本語）。

- **国コード**: ISO 3166-1 alpha-3 にそろえる。ISO に無いもの（コソボなど）は世界銀行のコードを使う。
  取得元ごとの別名は ``CODE_ALIASES`` で寄せる（取得の時点と、既存の bronze の両方で）
- **国名の日本語**: ISO 3166 の日本語名（pycountry）に、短く通りのよい呼び方を上書きする。
  世界銀行・IMF の地域や所得区分などの集計値は ``AGGREGATE_JA`` で付ける
"""
from __future__ import annotations

import gettext
import re

import pandas as pd

try:
    import pycountry

    _JA = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["ja"])
except Exception:  # pycountry が無くても動く（日本語名が空になるだけ）
    pycountry = None
    _JA = None

#: 取得元ごとの国コードの別名 → そろえる先
CODE_ALIASES: dict[str, dict[str, str]] = {
    "imf": {"UVK": "XKX", "WBG": "PSE"},  # コソボ・パレスチナ（世界銀行と ISO の表記に寄せる）
}

#: ISO の日本語名が長い・通りが悪いものを、よく使う呼び方に
JA_OVERRIDES = {
    "KOR": "韓国", "PRK": "北朝鮮", "TWN": "台湾", "XKX": "コソボ", "IRN": "イラン", "SYR": "シリア",
    "VEN": "ベネズエラ", "BOL": "ボリビア", "BRN": "ブルネイ", "TZA": "タンザニア", "LAO": "ラオス",
    "MDA": "モルドバ", "RUS": "ロシア", "VNM": "ベトナム", "VAT": "バチカン", "FLK": "フォークランド諸島",
    "CCK": "ココス諸島", "MAF": "サンマルタン（仏領）", "SXM": "シントマールテン（蘭領）", "GBR": "英国",
    "USA": "米国", "CZE": "チェコ", "MKD": "北マケドニア", "CHN": "中国", "HKG": "香港", "MAC": "マカオ",
    "PSE": "パレスチナ", "COD": "コンゴ民主共和国", "COG": "コンゴ共和国", "FSM": "ミクロネシア連邦",
    "SHN": "セントヘレナ", "CIV": "コートジボワール", "TUR": "トルコ", "SWZ": "エスワティニ",
}

#: 集計値（地域・所得区分・グループ）の日本語名。世界銀行と IMF の主なもの
AGGREGATE_JA = {
    "WLD": "世界", "HIC": "高所得国", "UMC": "上位中所得国", "LMC": "下位中所得国", "LIC": "低所得国",
    "MIC": "中所得国", "LMY": "低・中所得国", "OED": "OECD 加盟国", "EUU": "EU", "EMU": "ユーロ圏",
    "LCN": "中南米・カリブ", "LAC": "中南米・カリブ（低・中所得）", "EAS": "東アジア・太平洋",
    "EAP": "東アジア・太平洋（低・中所得）", "ECS": "欧州・中央アジア", "ECA": "欧州・中央アジア（低・中所得）",
    "MEA": "中東・北アフリカ", "MNA": "中東・北アフリカ（低・中所得）", "NAC": "北米", "SAS": "南アジア",
    "SSF": "サブサハラ・アフリカ", "SSA": "サブサハラ・アフリカ（低・中所得）", "ARB": "アラブ諸国",
    "AFE": "東部・南部アフリカ", "AFW": "西部・中部アフリカ", "IDA": "IDA 対象国", "IBD": "IBRD 対象国",
    "FCS": "脆弱・紛争影響国", "SST": "小島嶼国", "CSS": "カリブ小国", "PSS": "太平洋小国",
    "LDC": "後発開発途上国", "HPC": "重債務貧困国",
}


def canonical(code: str, distributor: str) -> str:
    return CODE_ALIASES.get(distributor, {}).get(code, code)


def canonicalize(df: pd.DataFrame, distributor: str, column: str = "country") -> pd.DataFrame:
    aliases = CODE_ALIASES.get(distributor)
    if not aliases or column not in df.columns:
        return df
    return df.assign(**{column: df[column].replace(aliases)})


def name_ja(code: str, fallback: str = "") -> str:
    if code in JA_OVERRIDES:
        return JA_OVERRIDES[code]
    if code in AGGREGATE_JA:
        return AGGREGATE_JA[code]
    if pycountry is not None:
        c = pycountry.countries.get(alpha_3=code)
        if c is not None and _JA is not None:
            name = _JA.gettext(c.name)
            m = re.fullmatch(r".+ \((.+)\)", name)  # 「大韓民国 (韓国)」のような括弧の通称を使う
            return m.group(1) if m and not m.group(1).endswith("領") else name
    return fallback


def add_name_ja(countries: pd.DataFrame) -> pd.DataFrame:
    """国の一覧に日本語名（name_ja）を足す。分からないものは英語名のまま。"""
    out = countries.copy()
    out["name_ja"] = [name_ja(c, n) for c, n in zip(out["country"], out["name"])]
    return out
