"""総合予想エンジン。

3 要素（西田式スピード指数・血統・追切）をそれぞれ算出し、出走メンバー内の
偏差値に変換してから重み付き合成で総合評価を出す。

偏差値化する理由: スピード指数（〜110 程度）と血統・追切スコア（0〜100）は
スケールも分散も異なるため、生値のまま足すと配分が崩れる。メンバー内での
相対的な位置に揃えることで、重みが意図どおり効く。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev

from .models import HorseEntry, RaceCard
from .pedigree import pedigree_score
from .speed_index import aggregate_speed_score
from .workout import workout_score

# デフォルトの重み（スピード指数を主軸に、追切・血統で補正）
DEFAULT_WEIGHTS = {"speed": 0.5, "workout": 0.3, "pedigree": 0.2}

# 上位馬に付ける印
MARKS = ("◎", "○", "▲", "△", "△")


@dataclass
class HorseResult:
    """1 頭分の評価結果。"""

    name: str
    horse_number: int | None
    total: float                       # 総合点（偏差値の加重合成）
    mark: str                          # 印（◎○▲△、無印は ""）
    rank: int
    speed_score: float                 # 過去5走の集約スピード指数
    speed_indices: list[float]         # 各走の生指数（直近順）
    pedigree: dict                     # 血統スコア内訳
    workout: dict                      # 追切スコア内訳
    deviations: dict[str, float] = field(default_factory=dict)  # 各要素の偏差値


def _to_deviation(values: list[float]) -> list[float]:
    """メンバー内偏差値（平均 50, 標準偏差 10）へ変換する。"""
    if len(values) <= 1:
        return [50.0 for _ in values]
    mu = mean(values)
    sigma = pstdev(values)
    if sigma == 0:
        return [50.0 for _ in values]
    return [50.0 + (v - mu) / sigma * 10 for v in values]


def evaluate_horse(horse: HorseEntry, surface: str, distance: int) -> dict:
    """1 頭の 3 要素の生スコアを算出する。"""
    speed, indices = aggregate_speed_score(horse.past_races, surface, distance)
    ped = pedigree_score(horse.sire, horse.dam_sire, surface, distance)
    work = workout_score(horse.workouts)
    return {
        "speed": speed,
        "speed_indices": indices,
        "pedigree": ped,
        "workout": work,
    }


def predict(card: RaceCard, weights: dict[str, float] | None = None) -> list[HorseResult]:
    """レースカード全頭を評価し、総合点順のランキングを返す。"""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    total_w = sum(w.values())
    w = {k: v / total_w for k, v in w.items()}

    race = card.race
    raws = [evaluate_horse(h, race.surface, race.distance) for h in card.horses]

    # 過去走が無い馬（新馬など）のスピード指数はメンバー平均で補完する
    known_speeds = [r["speed"] for r in raws if r["speed_indices"]]
    fill = mean(known_speeds) if known_speeds else 0.0
    speeds = [r["speed"] if r["speed_indices"] else fill for r in raws]

    dev_speed = _to_deviation(speeds)
    dev_ped = _to_deviation([r["pedigree"]["score"] for r in raws])
    dev_work = _to_deviation([r["workout"]["score"] for r in raws])

    results = []
    for horse, raw, ds, dp, dw in zip(card.horses, raws, dev_speed, dev_ped, dev_work):
        total = ds * w["speed"] + dp * w["pedigree"] + dw * w["workout"]
        results.append(
            HorseResult(
                name=horse.name,
                horse_number=horse.horse_number,
                total=round(total, 2),
                mark="",
                rank=0,
                speed_score=round(raw["speed"], 1),
                speed_indices=[round(v, 1) for v in raw["speed_indices"]],
                pedigree=raw["pedigree"],
                workout=raw["workout"],
                deviations={
                    "speed": round(ds, 1),
                    "pedigree": round(dp, 1),
                    "workout": round(dw, 1),
                },
            )
        )

    results.sort(key=lambda r: r.total, reverse=True)
    for i, r in enumerate(results):
        r.rank = i + 1
        r.mark = MARKS[i] if i < len(MARKS) else ""
    return results
