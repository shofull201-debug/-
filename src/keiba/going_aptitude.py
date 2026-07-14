"""道悪適性の評価。

当日の馬場が良以外（稍重・重・不良）のとき、
- 血統の道悪適性（種牡馬データの wet 値、父 65% + 母父 35%）
- 本馬の道悪実績（過去走のうち良以外の馬場での着順率）
を合成して 0〜100 のスコアにする。

道悪実績が 1 走も無い馬は血統のみで評価する（実績があるときは血統 60% + 実績 40%）。
"""

from __future__ import annotations

from .models import HorseEntry, PastRace
from .pedigree import DAM_SIRE_WEIGHT, SIRE_WEIGHT, _load_sire_data

WET_GOINGS = ("稍重", "重", "不良")

# 血統と本馬実績のブレンド比率（実績がある場合）
PEDIGREE_WEIGHT = 0.6
RECORD_WEIGHT = 0.4


def is_wet(going: str) -> bool:
    return going in WET_GOINGS


def _sire_wet(name: str | None) -> tuple[float, bool]:
    """種牡馬 1 頭の道悪適性（0〜100）と登録済みかを返す。"""
    data = _load_sire_data()
    entry = data["sires"].get(name or "")
    known = entry is not None and "wet" in (entry or {})
    if entry is None:
        entry = data["default"]
    return entry.get("wet", 5) * 10.0, known


def pedigree_wet_score(sire: str, dam_sire: str | None) -> float:
    """血統からの道悪適性スコア（0〜100）。"""
    s, _ = _sire_wet(sire)
    if dam_sire:
        d, _ = _sire_wet(dam_sire)
        return s * SIRE_WEIGHT + d * DAM_SIRE_WEIGHT
    return s


def record_wet_score(past_races: list[PastRace]) -> float | None:
    """本馬の道悪実績スコア（0〜100）。道悪出走が無ければ None。

    各道悪レースの着順率 (1着=100, 最下位=0) の平均。
    頭数不明のレースは評価から除く。
    """
    perfs = []
    for r in past_races:
        if (
            is_wet(r.going)
            and r.finish_position
            and r.field_size
            and r.field_size > 1
        ):
            perfs.append(1.0 - (r.finish_position - 1) / (r.field_size - 1))
    if not perfs:
        return None
    return sum(perfs) / len(perfs) * 100.0


def going_aptitude_score(horse: HorseEntry) -> dict:
    """道悪適性の総合スコア（0〜100）と内訳を返す。"""
    ped = pedigree_wet_score(horse.sire, horse.dam_sire)
    rec = record_wet_score(horse.past_races)
    total = ped if rec is None else ped * PEDIGREE_WEIGHT + rec * RECORD_WEIGHT
    return {
        "score": round(total, 1),
        "pedigree_wet": round(ped, 1),
        "record_wet": round(rec, 1) if rec is not None else None,
        "wet_starts": sum(1 for r in horse.past_races if is_wet(r.going)),
    }
