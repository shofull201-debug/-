"""血統評価。

父と母父の「コース適性 × 距離適性」から今回条件への適合度を 0〜100 で採点する。
種牡馬データは data/sire_aptitude.json に持ち、ユーザーが自由に追加・調整できる。
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

# 父と母父の寄与比率
SIRE_WEIGHT = 0.65
DAM_SIRE_WEIGHT = 0.35


@lru_cache(maxsize=1)
def _load_sire_data() -> dict:
    with resources.files("keiba.data").joinpath("sire_aptitude.json").open(encoding="utf-8") as f:
        return json.load(f)


def distance_category(distance: int) -> str:
    """距離をカテゴリに分類する。"""
    if distance <= 1400:
        return "短距離"
    if distance <= 1800:
        return "マイル"
    if distance <= 2400:
        return "中距離"
    return "長距離"


def _sire_score(sire: str | None, surface: str, distance: int) -> tuple[float, bool]:
    """種牡馬 1 頭分の適性スコア（0〜100）と、データが登録済みかを返す。

    未登録の種牡馬は default（中立 = 50 点）で評価する。
    """
    data = _load_sire_data()
    entry = data["sires"].get(sire or "")
    known = entry is not None
    if entry is None:
        entry = data["default"]
    surf = entry["surface"].get(surface, 5)
    dist = entry["distance"].get(distance_category(distance), 5)
    return (surf * 0.5 + dist * 0.5) * 10, known


def pedigree_score(
    sire: str,
    dam_sire: str | None,
    surface: str,
    distance: int,
) -> dict:
    """血統総合スコア（0〜100）を返す。

    戻り値には内訳（父スコア・母父スコア・未登録フラグ）も含める。
    """
    s_score, s_known = _sire_score(sire, surface, distance)
    if dam_sire:
        d_score, d_known = _sire_score(dam_sire, surface, distance)
        total = s_score * SIRE_WEIGHT + d_score * DAM_SIRE_WEIGHT
    else:
        d_score, d_known = None, False
        total = s_score

    return {
        "score": round(total, 1),
        "sire_score": round(s_score, 1),
        "dam_sire_score": round(d_score, 1) if d_score is not None else None,
        "sire_known": s_known,
        "dam_sire_known": d_known,
    }
